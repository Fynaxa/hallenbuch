"""Ende-zu-Ende ueber echtes HTTP: anmelden, erfassen, abrechnen, ausstellen, gutschreiben."""
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hallenbuch import datenbank, lexware, server, sicherheit  # noqa: E402
from tests import hilfe  # noqa: E402


def seiten_pfad():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "hallenbuch", "statisch")


def freier_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class UeberHttp(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ["HALLENBUCH_GEHEIMNIS"] = "test-geheimnis"
        cls.ordner = tempfile.mkdtemp(prefix="hallenbuch-test-")
        cls.db = os.path.join(cls.ordner, "test.db")

        verb = datenbank.verbinden(cls.db)
        datenbank.aufbauen(verb)
        verb.execute(
            "INSERT INTO benutzer (name,anmeldename,passwort,rolle,angelegt_am)"
            " VALUES ('Achmed','achmed',?,'buero',?)",
            (sicherheit.passwort_hashen("geheim123"), datenbank.jetzt()))
        verb.execute(
            "INSERT INTO benutzer (name,anmeldename,passwort,rolle,angelegt_am)"
            " VALUES ('Halle','halle',?,'erfasser',?)",
            (sicherheit.passwort_hashen("geheim123"), datenbank.jetzt()))
        verb.execute(
            "INSERT INTO autohaus (id,name,kurz,strasse,plz,ort,kaeuferreferenz)"
            " VALUES (1,'Autohaus Nordstadt','NORD','Industriestr. 12','38228','Salzgitter',"
            "'04011000-1234512345-01')")
        verb.execute(
            "INSERT INTO autohaus (id,name,kurz,strasse,plz,ort)"
            " VALUES (2,'Autohaus Lebenstedt','LEBE','Am Ring 4','38226','Salzgitter')")
        # Je Abrechnungstest ein eigenes Autohaus. Die Sammelrechnung nimmt
        # alles Offene bis zum Stichtag mit; zwei Tests am selben Autohaus
        # naehmen sich sonst gegenseitig die Fahrzeuge weg, und das Ergebnis
        # haenge an der Reihenfolge statt am Code.
        for kennung, name, kurz in ((3, "Autohaus Fallersleben", "FALL"),
                                    (4, "Autohaus Watenstedt", "WATE"),
                                    (5, "Autohaus Thiede", "THIE")):
            verb.execute(
                "INSERT INTO autohaus (id,name,kurz,strasse,plz,ort)"
                " VALUES (?,?,?,'Werkstr. 1','38228','Salzgitter')", (kennung, name, kurz))
        verb.execute(
            "INSERT INTO leistung (id,bezeichnung,netto_cent) VALUES (1,'Vollaufbereitung',14900)")
        verb.execute(
            "INSERT INTO leistung (id,bezeichnung,netto_cent) VALUES (2,'Innenreinigung',8900)")
        verb.close()

        cls.fotos_vorher = server.FOTOS
        cls.sicherungen_vorher = server.SICHERUNGEN
        cls.betrieb_vorher = server.BETRIEB
        server.BETRIEB = cls.ordner
        server.FOTOS = os.path.join(cls.ordner, "fotos")
        server.SICHERUNGEN = os.path.join(cls.ordner, "sicherungen")
        cls.dienst = lexware.Attrappe()
        cls.port = freier_port()
        server.Griff.anwendung = server.Hallenbuch(cls.db, cls.dienst)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), server.Griff)
        cls.faden = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.faden.start()

    @classmethod
    def tearDownClass(cls):
        server.FOTOS = cls.fotos_vorher
        server.SICHERUNGEN = cls.sicherungen_vorher
        server.BETRIEB = cls.betrieb_vorher
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.oeffner = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))

    def basis(self, weg=""):
        return "http://127.0.0.1:%d%s" % (self.port, weg)

    def holen(self, weg):
        return self.oeffner.open(self.basis(weg), timeout=10)

    def senden(self, weg, felder, json_modus=False):
        if json_modus:
            anfrage = urllib.request.Request(
                self.basis(weg), data=json.dumps(felder).encode(),
                headers={"Content-Type": "application/json",
                         "Origin": self.basis()}, method="POST")
        else:
            anfrage = urllib.request.Request(
                self.basis(weg), data=urllib.parse.urlencode(felder).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Origin": self.basis()}, method="POST")
        return self.oeffner.open(anfrage, timeout=10)

    def anmelden(self, name="achmed"):
        return self.senden("/anmelden", {"anmeldename": name, "passwort": "geheim123"})

    # --- Tests ------------------------------------------------------------

    def test_ohne_anmeldung_keine_daten(self):
        antwort = self.holen("/fahrzeuge")
        self.assertIn("Anmelden", antwort.read().decode())

    def test_falsches_passwort_wird_abgewiesen(self):
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self.senden("/anmelden", {"anmeldename": "achmed", "passwort": "falsch"})
        self.assertEqual(fall.exception.code, 401)

    def test_fremde_herkunft_wird_abgewiesen(self):
        anfrage = urllib.request.Request(
            self.basis("/anmelden"),
            data=urllib.parse.urlencode({"anmeldename": "achmed"}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": "https://beispiel.example"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self.oeffner.open(anfrage, timeout=10)
        self.assertEqual(fall.exception.code, 403)

    def test_erfasser_darf_nicht_ins_buero(self):
        self.anmelden("halle")
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self.holen("/buero")
        self.assertEqual(fall.exception.code, 403)

    def test_ganzer_ablauf_vom_hof_bis_zur_gutschrift(self):
        self.anmelden()
        # 1. Erfassen ueber die Schnittstelle, wie es das Handy tut.
        kennungen = []
        for i in range(6):
            antwort = json.loads(self.senden("/api/erfassen", {
                "fin": hilfe.fin_nummer(300 + i), "autohaus_id": 3, "leistung_id": 1,
                "fertig_am": "2026-09-21", "vorgang": "http-%d" % i,
            }, json_modus=True).read().decode())
            self.assertTrue(antwort["ok"], antwort)
            kennungen.append(antwort["id"])

        # 2. Derselbe Vorgang noch einmal: nichts doppelt.
        wieder = json.loads(self.senden("/api/erfassen", {
            "fin": hilfe.fin_nummer(300), "autohaus_id": 3, "leistung_id": 1,
            "fertig_am": "2026-09-21", "vorgang": "http-0"}, json_modus=True).read().decode())
        self.assertEqual(wieder["id"], kennungen[0])

        # 3. Uebersicht zeigt die offenen Fahrzeuge.
        seite = self.holen("/buero?von=2026-09-14&bis=2026-09-27").read().decode()
        self.assertIn("Autohaus Fallersleben", seite)

        # 4. Sammelrechnung erzeugen.
        antwort = self.senden("/abrechnen", {"autohaus_id": 3, "von": "2026-09-14",
                                             "bis": "2026-09-27"})
        ziel = antwort.geturl()
        self.assertIn("/rechnung/", ziel)
        rechnung_id = int(ziel.split("/rechnung/")[1].split("?")[0])
        seite = self.holen("/rechnung/%d" % rechnung_id).read().decode()
        self.assertIn("Stand: <b>Entwurf</b>", seite)
        self.assertIn("894,00", seite)      # 6 x 149,00 netto

        # 5. Freigeben und ausstellen.
        self.senden("/rechnung/%d/freigeben" % rechnung_id, {})
        seite = self.senden("/rechnung/%d/ausstellen" % rechnung_id, {}).read().decode()
        self.assertIn("Stand: <b>Finalisiert</b>", seite)
        self.assertIn("RE-PROBE", seite)

        # 6. Gutschrift fuer ein Fahrzeug, Rechnung bleibt unberuehrt.
        seite = self.senden("/gutschrift/%d" % kennungen[2],
                            {"grund": "Reklamation Innenraum"}).read().decode()
        self.assertIn("gutgeschrieben", seite)
        self.assertIn("894,00", seite)

        # 7. Protokoll hat jeden Schritt.
        protokoll = self.holen("/protokoll").read().decode()
        for wort in ("Fahrzeug erfasst", "Sammelrechnung erzeugt", "Rechnung freigegeben",
                     "Rechnung ausgestellt", "Gutschrift erzeugt"):
            self.assertIn(wort, protokoll)

    def test_zweite_rechnung_gleicher_zeitraum_meldet_sich(self):
        self.anmelden()
        for i in range(2):
            self.senden("/api/erfassen", {
                "fin": hilfe.fin_nummer(500 + i), "autohaus_id": 4, "leistung_id": 1,
                "fertig_am": "2026-04-05", "vorgang": "zwei-%d" % i}, json_modus=True)
        self.senden("/abrechnen", {"autohaus_id": 4, "von": "2026-04-01", "bis": "2026-04-11"})
        self.senden("/api/erfassen", {
            "fin": hilfe.fin_nummer(520), "autohaus_id": 4, "leistung_id": 1,
            "fertig_am": "2026-04-05", "vorgang": "zwei-x"}, json_modus=True)
        antwort = self.senden("/abrechnen", {"autohaus_id": 4, "von": "2026-04-01",
                                             "bis": "2026-04-11"})
        self.assertIn("schon eine Rechnung", urllib.parse.unquote(antwort.geturl()))

    def test_unbrauchbare_fin_wird_mit_klartext_abgelehnt(self):
        self.anmelden()
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self.senden("/api/erfassen", {
                "fin": "ZU-KURZ", "autohaus_id": 1, "leistung_id": 1,
                "fertig_am": "2026-09-21", "vorgang": "kurz-1"}, json_modus=True)
        antwort = json.loads(fall.exception.read().decode())
        self.assertFalse(antwort["ok"])
        self.assertIn("17 Zeichen", antwort["fehler"])

    # --- Schwächenprüfung: Fotos und Datumsfallen ---

    def test_fotos_gibt_es_nur_nach_der_anmeldung(self):
        """Vorher standen sie unter einer offenen Adresse. Der Name ist nicht zu
        raten, aber Adressen werden geteilt, protokolliert und weitergereicht —
        und es sind Kundendaten."""
        self.anmelden()
        kennung = self._fahrzeug("gespeichert")
        antwort = json.loads(self._foto(kennung).read().decode())
        # Angemeldet: das Bild kommt
        self.assertEqual(self.holen(antwort["adresse"]).read(), self.JPEG)
        # Ohne Anmeldung: die Anmeldeseite statt des Bildes
        fremd = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))
        roh = fremd.open(self.basis(antwort["adresse"]), timeout=10).read()
        self.assertNotEqual(roh, self.JPEG)
        self.assertIn(b"Anmelden", roh)

    def test_fertigstellung_in_der_zukunft_wird_abgelehnt(self):
        """Ein vertipptes Jahr parkt das Fahrzeug ausserhalb jedes
        Abrechnungszeitraums — erfasst, aber nie berechnet."""
        from datetime import date, timedelta
        self.anmelden()
        morgen_plus = (date.today() + timedelta(days=5)).isoformat()
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self.senden("/api/erfassen", {
                "fin": hilfe.fin_nummer(900), "autohaus_id": 1, "leistung_id": 1,
                "fertig_am": morgen_plus, "vorgang": "zukunft-1"}, json_modus=True)
        antwort = json.loads(fall.exception.read().decode())
        self.assertFalse(antwort["ok"])
        self.assertIn("Zukunft", antwort["fehler"])
        self.assertIn("nie berechnet", antwort["fehler"])

    def test_heute_und_gestern_gehen_selbstverstaendlich(self):
        from datetime import date, timedelta
        self.anmelden()
        for versatz, marke in ((0, "heute"), (1, "gestern")):
            tag = (date.today() - timedelta(days=versatz)).isoformat()
            antwort = json.loads(self.senden("/api/erfassen", {
                "fin": hilfe.fin_nummer(910 + versatz), "autohaus_id": 1,
                "leistung_id": 1, "fertig_am": tag,
                "vorgang": "nah-%s" % marke}, json_modus=True).read().decode())
            self.assertTrue(antwort["ok"], marke)

    def test_lange_zurueck_ist_ein_hinweis_keine_sperre(self):
        """Alte Fahrzeuge nachzutragen ist erlaubt. Es soll nur auffallen."""
        from datetime import date, timedelta
        self.anmelden()
        lange_her = (date.today() - timedelta(days=200)).isoformat()
        antwort = json.loads(self.senden("/api/erfassen", {
            "fin": hilfe.fin_nummer(920), "autohaus_id": 1, "leistung_id": 1,
            "fertig_am": lange_her, "vorgang": "alt-1"}, json_modus=True).read().decode())
        self.assertTrue(antwort["ok"])
        self.assertTrue(any("200 Tage zurück" in h for h in antwort["hinweise"]),
                        antwort["hinweise"])

    def test_unlesbares_datum_wird_abgefangen(self):
        self.anmelden()
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self.senden("/api/erfassen", {
                "fin": hilfe.fin_nummer(930), "autohaus_id": 1, "leistung_id": 1,
                "fertig_am": "31.02.2026", "vorgang": "krumm-1"}, json_modus=True)
        self.assertIn("nicht lesbar",
                      json.loads(fall.exception.read().decode())["fehler"])

    # --- C4: Lexware-Ausfall ---

    def _freigegebene_rechnung(self, marke, tag, von, bis):
        for i in range(2):
            self.senden("/api/erfassen", {
                "fin": hilfe.fin_nummer(800 + abs(hash(marke)) % 50 + i),
                "autohaus_id": 2, "leistung_id": 1, "fertig_am": tag,
                "vorgang": "c4-%s-%d" % (marke, i)}, json_modus=True)
        antwort = self.senden("/abrechnen", {"autohaus_id": 2, "von": von, "bis": bis})
        kennung = int(antwort.geturl().split("/rechnung/")[1].split("?")[0])
        self.senden("/rechnung/%d/freigeben" % kennung, {})
        return kennung

    def _mit_streik(self, sicher):
        from hallenbuch import lexware as lx

        class Streik:
            bereit = True

            def rechnung_ausstellen(self, kopf, posten):
                raise lx.LexwareFehler("HTTP 503: Wartungsarbeiten", sicher)
        echt = server.Griff.anwendung.dienst
        server.Griff.anwendung.dienst = Streik()
        return echt

    def test_ausfall_bleibt_auf_der_rechnungsseite_stehen(self):
        """Der Grund darf nicht nur in der Adresszeile stehen und beim nächsten
        Laden verschwinden."""
        self.anmelden()
        kennung = self._freigegebene_rechnung("steht", "2026-07-06",
                                              "2026-07-01", "2026-07-12")
        echt = self._mit_streik(True)
        try:
            self.senden("/rechnung/%d/ausstellen" % kennung, {})
        finally:
            server.Griff.anwendung.dienst = echt
        # Frisch geladen, ohne Meldung in der Adresszeile
        seite = self.holen("/rechnung/%d" % kennung).read().decode()
        self.assertIn("Ausstellen fehlgeschlagen", seite)
        self.assertIn("Wartungsarbeiten", seite)
        self.assertIn("Stand: <b>Freigegeben</b>", seite)
        self.assertIn("Erneut ausstellen", seite)

    def test_eine_festsitzende_rechnung_faellt_in_der_uebersicht_auf(self):
        """Sie sah in der Liste aus wie eine, die nur auf den Klick wartet.
        Bei acht Rechnungen die Woche übersieht man die eine — und dann ist sie
        nicht gestellt."""
        self.anmelden()
        kennung = self._freigegebene_rechnung("auffallen", "2026-07-13",
                                              "2026-07-13", "2026-07-19")
        def zeile_von(seite, kennung):
            """Nur die Tabellenzeile dieser Rechnung. Die Liste zeigt die
            letzten vierzig Belege, unabhängig vom gewählten Zeitraum."""
            anker = seite.index('href="/rechnung/%d"' % kennung)
            ende = seite.find("</tr>", anker)
            return seite[anker:ende]

        seite = self.holen("/buero?von=2026-07-13&bis=2026-07-19").read().decode()
        self.assertNotIn("Nicht ausgestellt", zeile_von(seite, kennung))

        echt = self._mit_streik(True)           # sicher nichts angelegt
        try:
            self.senden("/rechnung/%d/ausstellen" % kennung, {})
        finally:
            server.Griff.anwendung.dienst = echt

        seite = self.holen("/buero?von=2026-07-13&bis=2026-07-19").read().decode()
        self.assertIn("Nicht ausgestellt", zeile_von(seite, kennung))
        self.assertIn("nicht ausgestellt</span>", seite)   # die Kennzahl oben

    def test_bei_unklarer_lage_steht_in_der_uebersicht_nachsehen(self):
        self.anmelden()
        kennung = self._freigegebene_rechnung("unklar-liste", "2026-07-20",
                                              "2026-07-20", "2026-07-26")
        echt = self._mit_streik(False)          # unklar, ob drüben schon etwas ist
        try:
            self.senden("/rechnung/%d/ausstellen" % kennung, {})
        finally:
            server.Griff.anwendung.dienst = echt
        seite = self.holen("/buero?von=2026-07-20&bis=2026-07-26").read().decode()
        anker = seite.index('href="/rechnung/%d"' % kennung)
        self.assertIn("In Lexware nachsehen", seite[anker:seite.find("</tr>", anker)])

    def test_bei_unklarer_lage_wird_vor_der_doppelten_rechnung_gewarnt(self):
        self.anmelden()
        kennung = self._freigegebene_rechnung("unklar", "2026-08-05",
                                              "2026-08-01", "2026-08-09")
        echt = self._mit_streik(False)
        try:
            self.senden("/rechnung/%d/ausstellen" % kennung, {})
        finally:
            server.Griff.anwendung.dienst = echt
        seite = self.holen("/rechnung/%d" % kennung).read().decode()
        self.assertIn("Vor einem zweiten Versuch in Lexware nachsehen", seite)
        self.assertIn("zweimal berechnet", seite)
        self.assertIn("confirm(", seite)     # Rückfrage am Knopf

    def test_nach_gegluecktem_zweitem_versuch_ist_die_stoerung_weg(self):
        self.anmelden()
        kennung = self._freigegebene_rechnung("zweiter", "2026-06-03",
                                              "2026-06-01", "2026-06-14")
        echt = self._mit_streik(False)
        try:
            self.senden("/rechnung/%d/ausstellen" % kennung, {})
        finally:
            server.Griff.anwendung.dienst = echt
        self.senden("/rechnung/%d/ausstellen" % kennung, {})
        seite = self.holen("/rechnung/%d" % kennung).read().decode()
        self.assertNotIn("Ausstellen fehlgeschlagen", seite)
        self.assertIn("Stand: <b>Finalisiert</b>", seite)
        self.assertIn("RE-PROBE", seite)

    # --- C2: Anmeldeversuche begrenzen ---

    def _daneben(self, name, herkunft=None):
        kopf = {"Content-Type": "application/x-www-form-urlencoded",
                "Origin": self.basis()}
        if herkunft:
            kopf["X-Forwarded-For"] = herkunft
        anfrage = urllib.request.Request(
            self.basis("/anmelden"),
            data=urllib.parse.urlencode(
                {"anmeldename": name, "passwort": "falsch"}).encode(),
            headers=kopf, method="POST")
        try:
            self.oeffner.open(anfrage, timeout=10)
            return 200
        except urllib.error.HTTPError as fehler:
            return fehler.code

    def test_nach_fuenf_fehlversuchen_kommt_eine_pause(self):
        """Auf einen nicht vorhandenen Namen, damit kein echter Zugang
        blockiert wird — gezählt wird trotzdem."""
        for _ in range(5):
            self.assertEqual(self._daneben("gibtsnicht"), 401)
        anfrage = urllib.request.Request(
            self.basis("/anmelden"),
            data=urllib.parse.urlencode(
                {"anmeldename": "gibtsnicht", "passwort": "falsch"}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": self.basis()}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self.oeffner.open(anfrage, timeout=10)
        self.assertEqual(fall.exception.code, 429)
        text = fall.exception.read().decode()
        self.assertIn("Zu viele Fehlversuche", text)
        self.assertIn("Minuten warten", text)

    def test_vor_der_pause_wird_gewarnt(self):
        for _ in range(3):
            self._daneben("gibtsauchnicht")
        anfrage = urllib.request.Request(
            self.basis("/anmelden"),
            data=urllib.parse.urlencode(
                {"anmeldename": "gibtsauchnicht", "passwort": "falsch"}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": self.basis()}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self.oeffner.open(anfrage, timeout=10)
        self.assertIn("Noch 1 Versuch", fall.exception.read().decode())

    def test_erfolgreiche_anmeldung_raeumt_die_fehlversuche_weg(self):
        """Wer sich vertippt und dann richtig anmeldet, fängt bei null an."""
        for _ in range(4):
            self._daneben("halle")
        self.anmelden("halle")
        verb = datenbank.verbinden(self.db)
        try:
            offen = verb.execute(
                "SELECT COUNT(*) FROM anmeldeversuch WHERE anmeldename='halle'"
                " AND erfolg=0").fetchone()[0]
        finally:
            verb.close()
        self.assertEqual(offen, 0)

    def test_unbekannter_name_und_falsches_passwort_klingen_gleich(self):
        """Sonst liest man an der Antwort ab, welche Namen es gibt."""
        anfragen = []
        for name in ("achmed", "denspiblsesnicht"):
            anfrage = urllib.request.Request(
                self.basis("/anmelden"),
                data=urllib.parse.urlencode(
                    {"anmeldename": name, "passwort": "ganz-sicher-falsch"}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Origin": self.basis()}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as fall:
                self.oeffner.open(anfrage, timeout=10)
            self.assertEqual(fall.exception.code, 401)
            anfragen.append("Name oder Passwort stimmt nicht"
                            in fall.exception.read().decode())
        self.assertEqual(anfragen, [True, True])

    def test_viele_namen_von_einer_adresse_werden_gebremst(self):
        from hallenbuch import anmeldeschutz
        vorher = anmeldeschutz.VERSUCHE_JE_HERKUNFT
        anmeldeschutz.VERSUCHE_JE_HERKUNFT = 3
        try:
            for i in range(3):
                self._daneben("streuname%d" % i, herkunft="203.0.113.7")
            self.assertEqual(self._daneben("streuname9", herkunft="203.0.113.7"), 429)
            # Von einer anderen Adresse geht es weiter
            self.assertEqual(self._daneben("streuname9", herkunft="203.0.113.8"), 401)
        finally:
            anmeldeschutz.VERSUCHE_JE_HERKUNFT = vorher

    # --- C1: Sicherung als Bordmittel ---

    def test_buero_kann_auf_knopfdruck_sichern(self):
        self.anmelden()
        antwort = self.senden("/sicherung", {})
        ziel = urllib.parse.unquote(antwort.geturl())
        self.assertIn("Sicherung angelegt und geprüft", ziel)
        self.assertIn("sicherung-", ziel)
        # Steht danach in der Liste und im Protokoll
        self.assertIn("sicherung-", self.holen("/verwaltung").read().decode())
        self.assertIn("Sicherung angelegt", self.holen("/protokoll").read().decode())

    def test_die_sicherung_ist_wirklich_lesbar(self):
        from hallenbuch import sicherung as sicherungsmodul
        self.anmelden()
        self.senden("/sicherung", {})
        vorhanden = sicherungsmodul.vorhandene(server.SICHERUNGEN)
        self.assertTrue(vorhanden)
        befund = sicherungsmodul.pruefen(
            os.path.join(server.SICHERUNGEN, vorhanden[0]["datei"]))
        self.assertGreater(befund["zeilen"]["fahrzeug"], 0)

    def test_die_halle_darf_nicht_sichern(self):
        self.anmelden("halle")
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self.senden("/sicherung", {})
        self.assertEqual(fall.exception.code, 403)

    def test_verwaltung_bietet_den_sicherungsknopf(self):
        self.anmelden()
        seite = self.holen("/verwaltung").read().decode()
        self.assertIn('action="/sicherung"', seite)
        self.assertIn("Sicherung ist keine Sicherung", seite)

    # --- B6: Leere Zustände ---

    def test_leerer_filter_unterscheidet_sich_von_gar_nichts(self):
        """Es gibt Fahrzeuge, nur nicht im gewählten Ausschnitt. Das muss anders
        klingen als 'noch nie etwas erfasst', sonst sucht jemand den Fehler."""
        self.anmelden()
        seite = self.holen("/fahrzeuge?von=2021-01-01&bis=2021-01-02").read().decode()
        self.assertIn("Nichts im gewählten Ausschnitt", seite)
        self.assertNotIn("Noch kein Fahrzeug erfasst", seite)

    # --- B5: App-Symbole für den Startbildschirm ---

    @staticmethod
    def _png_masse(roh):
        """Kantenlängen direkt aus dem PNG-Kopf lesen, ohne Fremdbibliothek."""
        import struct
        assert roh[:8] == b"\x89PNG\r\n\x1a\n", "kein PNG"
        breite, hoehe = struct.unpack(">II", roh[16:24])
        return breite, hoehe

    def test_manifest_nennt_symbole_und_die_gibt_es_auch(self):
        manifest = json.loads(self.holen("/manifest.json").read().decode())
        self.assertTrue(manifest["icons"], "ohne Symbole nimmt das Handy einen Seitenausschnitt")
        for symbol in manifest["icons"]:
            roh = self.holen(symbol["src"]).read()
            breite, hoehe = self._png_masse(roh)
            self.assertEqual("%dx%d" % (breite, hoehe), symbol["sizes"], symbol["src"])

    def test_manifest_hat_ein_maskierbares_symbol(self):
        """Android schneidet die Ecken weg. Ohne maskierbares Symbol wird der
        Schriftzug angeschnitten."""
        manifest = json.loads(self.holen("/manifest.json").read().decode())
        zwecke = [s.get("purpose") for s in manifest["icons"]]
        self.assertIn("maskable", zwecke)
        self.assertIn("any", zwecke)

    def test_apple_touch_icon_ist_verlinkt_und_vorhanden(self):
        """iOS liest das Manifest für das Symbol nicht, es braucht diesen Verweis."""
        self.anmelden()
        seite = self.holen("/erfassen").read().decode()
        self.assertIn('rel="apple-touch-icon"', seite)
        breite, hoehe = self._png_masse(
            self.holen("/statisch/symbole/apple-touch-icon.png").read())
        self.assertEqual((breite, hoehe), (180, 180))

    def test_farben_von_manifest_seite_und_stil_passen_zusammen(self):
        """Eine abweichende theme-color färbt die Statusleiste anders als die
        Kopfzeile. Das sieht aus wie ein Fehler, und es ist auch einer."""
        self.anmelden()
        manifest = json.loads(self.holen("/manifest.json").read().decode())
        seite = self.holen("/erfassen").read().decode()
        stil = self.holen("/statisch/stil.css").read().decode()
        self.assertIn('content="%s"' % manifest["theme_color"], seite)
        self.assertIn("--nacht:%s" % manifest["theme_color"], stil)
        self.assertIn("--grund:%s" % manifest["background_color"], stil)

    # --- B4: Suche über alle Fahrzeuge ---

    SUCH_FIN = "W0L0AHL3572123456"

    def _suchfahrzeug(self):
        self.senden("/api/erfassen", {
            "fin": self.SUCH_FIN, "autohaus_id": 1, "leistung_id": 1,
            "fertig_am": "2026-05-05", "kennzeichen": "SZ-QR 404",
            "vorgang": "b4-suche"}, json_modus=True)

    def test_suche_findet_ueber_die_ganze_fin(self):
        self.anmelden()
        self._suchfahrzeug()
        seite = self.holen("/suche?q=%s" % self.SUCH_FIN).read().decode()
        self.assertIn("W0L 0AHL35 72123456", seite)   # gruppiert dargestellt

    def test_suche_findet_ueber_die_letzten_stellen(self):
        """Am Telefon liest jemand die letzten sechs Stellen vor."""
        self.anmelden()
        self._suchfahrzeug()
        self.assertIn("W0L 0AHL35 72123456",
                      self.holen("/suche?q=123456").read().decode())

    def test_suche_vertraegt_o_statt_null(self):
        self.anmelden()
        self._suchfahrzeug()
        self.assertIn("W0L 0AHL35 72123456",
                      self.holen("/suche?q=WOLOAHL").read().decode())

    def test_suche_findet_kennzeichen_mit_und_ohne_trenner(self):
        self.anmelden()
        self._suchfahrzeug()
        for frage in ("SZ-QR+404", "SZQR404", "szqr404", "SZ+QR+404"):
            self.assertIn("W0L 0AHL35 72123456",
                          self.holen("/suche?q=%s" % frage).read().decode(), frage)

    def test_suche_zeigt_auf_welcher_rechnung_das_fahrzeug_steht(self):
        self.anmelden()
        self.senden("/api/erfassen", {
            "fin": hilfe.fin_nummer(760), "autohaus_id": 5, "leistung_id": 1,
            "fertig_am": "2026-03-09", "kennzeichen": "SZ-XY 9",
            "vorgang": "b4-rechnung"}, json_modus=True)
        self.senden("/abrechnen", {"autohaus_id": 5, "von": "2026-03-02",
                                   "bis": "2026-03-15"})
        seite = self.holen("/suche?q=SZXY9").read().decode()
        self.assertIn("Auf Rechnung", seite)
        self.assertIn("Entwurf Nr.", seite)

    def test_suche_zeigt_auch_was_noch_offen_ist(self):
        self.anmelden()
        self._suchfahrzeug()
        self.assertIn("Noch auf keiner Rechnung",
                      self.holen("/suche?q=%s" % self.SUCH_FIN).read().decode())

    def test_zu_kurze_suche_wird_abgefangen(self):
        self.anmelden()
        seite = self.holen("/suche?q=WV").read().decode()
        self.assertIn("mindestens drei Zeichen", seite)

    def test_suche_ohne_treffer_erklaert_sich(self):
        self.anmelden()
        seite = self.holen("/suche?q=ZZZZZZZZZ9").read().decode()
        self.assertIn("Nichts gefunden", seite)
        self.assertIn("O und 0 werden beide gefunden", seite)

    def test_auch_die_halle_darf_suchen(self):
        self.anmelden("halle")
        seite = self.holen("/suche").read().decode()
        self.assertIn('href="/suche"', seite)
        self.assertIn("FIN oder Kennzeichen", seite)

    # --- B3: Rückmeldung am Handy ---

    def test_quittung_ist_auf_jeder_seite_vorhanden(self):
        self.anmelden()
        for weg in ("/erfassen", "/fahrzeuge", "/buero"):
            seite = self.holen(weg).read().decode()
            self.assertIn('id="quittung"', seite, weg)
            self.assertIn('role="status"', seite, weg)
            self.assertIn('aria-live="polite"', seite, weg)

    def test_quittung_steht_hinter_der_daumenleiste(self):
        """Strukturelle Bedingung: nur so kann die CSS-Regel
        `main.mit-leiste ~ .quittung` sie über die Leiste heben. Wer die
        Reihenfolge umdreht, schiebt die Quittung unter die Knöpfe."""
        self.anmelden()
        seite = self.holen("/erfassen").read().decode()
        self.assertLess(seite.index('class="leiste"'), seite.index('id="quittung"'))
        stil = self.holen("/statisch/stil.css").read().decode()
        self.assertIn("main.mit-leiste ~ .quittung", stil)

    def test_quittung_startet_versteckt(self):
        self.anmelden()
        seite = self.holen("/erfassen").read().decode()
        stelle = seite.index('id="quittung"')
        self.assertIn("hidden", seite[stelle:stelle + 120])

    # Das Verhalten der Quittung — Ausblenden, Rütteln, Fehlermeldung — wird
    # in tests/warteschlange.mjs geprüft, indem der Code wirklich läuft.
    # Frühere Fassung schnitt 900 Zeichen aus dem Quelltext und suchte darin
    # nach Zeichenketten: Das bricht bei jeder Umformulierung, ohne dass sich
    # am Verhalten etwas ändert.

    # --- B2: Erfassung merkt sich das Autohaus ---

    def test_autohaus_wird_vom_letzten_fahrzeug_uebernommen(self):
        self.anmelden()
        self.senden("/api/erfassen", {
            "fin": hilfe.fin_nummer(700), "autohaus_id": 2, "leistung_id": 1,
            "fertig_am": "2026-05-05", "vorgang": "b2-eins"}, json_modus=True)
        seite = self.holen("/erfassen").read().decode()
        self.assertIn('<option value="2" selected>', seite)
        self.assertNotIn('<option value="1" selected>Autohaus Nordstadt', seite)

    def test_vorbelegung_wird_sichtbar_gemacht(self):
        """Eine stille Vorbelegung ist eine Falle. Wer das Haus gewechselt hat,
        muss es sehen."""
        self.anmelden()
        self.senden("/api/erfassen", {
            "fin": hilfe.fin_nummer(701), "autohaus_id": 2, "leistung_id": 1,
            "fertig_am": "2026-05-05", "vorgang": "b2-zwei"}, json_modus=True)
        self.assertIn("Vom letzten Fahrzeug übernommen",
                      self.holen("/erfassen").read().decode())

    def test_leistung_wird_bewusst_nicht_vorbelegt(self):
        """Ein falsch vorbelegtes Autohaus sieht man im Feld. Eine falsch
        vorbelegte Leistung ist ein Preisfehler, den niemand bemerkt."""
        self.anmelden()
        self.senden("/api/erfassen", {
            "fin": hilfe.fin_nummer(702), "autohaus_id": 2, "leistung_id": 2,
            "fertig_am": "2026-05-05", "vorgang": "b2-drei"}, json_modus=True)
        seite = self.holen("/erfassen").read().decode()
        self.assertIn('<option value="2" selected>Autohaus Lebenstedt', seite)
        self.assertNotIn('<option value="2" selected>Innenreinigung', seite)

    def test_jeder_mitarbeiter_hat_seine_eigene_vorbelegung(self):
        self.anmelden()
        self.senden("/api/erfassen", {
            "fin": hilfe.fin_nummer(703), "autohaus_id": 2, "leistung_id": 1,
            "fertig_am": "2026-05-05", "vorgang": "b2-vier"}, json_modus=True)
        # Anderer Mitarbeiter, eigene Sitzung
        anderer = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))
        self.oeffner = anderer
        self.anmelden("halle")
        seite = self.holen("/erfassen").read().decode()
        self.assertNotIn('<option value="2" selected>Autohaus Lebenstedt', seite)
        self.assertNotIn("Vom letzten Fahrzeug übernommen", seite)

    # --- B1: Zeitraum-Schnellwahl ---

    def test_buero_bietet_die_schnellwahl_an(self):
        from hallenbuch import zeitraum
        self.anmelden()
        seite = self.holen("/buero").read().decode()
        for schluessel in zeitraum.SCHLUESSEL:
            self.assertIn("/buero?zeitraum=%s" % schluessel, seite)
        # Ohne Angabe ist die laufende Woche aktiv
        self.assertIn('class="wahl an"', seite)

    def test_schnellwahl_setzt_den_zeitraum(self):
        from hallenbuch import zeitraum
        self.anmelden()
        von, bis = zeitraum.aufloesen("letzte-woche")
        seite = self.holen("/buero?zeitraum=letzte-woche").read().decode()
        self.assertIn('name="von" value="%s"' % von, seite)
        self.assertIn('name="bis" value="%s"' % bis, seite)

    def test_unsinniger_zeitraum_faellt_auf_diese_woche_zurueck(self):
        from hallenbuch import zeitraum
        self.anmelden()
        von, bis = zeitraum.aufloesen("diese-woche")
        seite = self.holen("/buero?zeitraum=vorgestern").read().decode()
        self.assertIn('name="von" value="%s"' % von, seite)
        self.assertIn('name="bis" value="%s"' % bis, seite)

    def test_eigener_zeitraum_laesst_keinen_knopf_leuchten(self):
        self.anmelden()
        seite = self.holen("/buero?von=2026-09-03&bis=2026-09-11").read().decode()
        self.assertNotIn('class="wahl an"', seite)
        self.assertIn('name="von" value="2026-09-03"', seite)

    # --- A3: Vollständiger Datenexport ---

    def _export_holen(self):
        import io as _io
        import zipfile
        antwort = self.holen("/export")
        self.assertEqual(antwort.headers.get("Content-Type"), "application/zip")
        self.assertIn("attachment", antwort.headers.get("Content-Disposition", ""))
        roh = antwort.read()
        return roh, zipfile.ZipFile(_io.BytesIO(roh))

    def test_export_enthaelt_jede_tabelle(self):
        self.anmelden()
        roh, zip_datei = self._export_holen()
        namen = set(zip_datei.namelist())
        from hallenbuch import export
        for tabelle in export.TABELLEN:
            self.assertIn("%s.csv" % tabelle, namen)
        self.assertIn("LIESMICH.txt", namen)

    def test_export_zeilenzahlen_stimmen_mit_der_datenbank(self):
        self.anmelden()
        roh, zip_datei = self._export_holen()
        verb = datenbank.verbinden(self.db)
        try:
            for tabelle in ("fahrzeug", "rechnung", "rechnungsposition", "autohaus"):
                inhalt = zip_datei.read("%s.csv" % tabelle).decode("utf-8-sig")
                zeilen = [z for z in inhalt.splitlines() if z.strip()]
                soll = verb.execute("SELECT COUNT(*) FROM %s" % tabelle).fetchone()[0]
                self.assertEqual(len(zeilen) - 1, soll, tabelle)   # minus Kopfzeile
        finally:
            verb.close()

    def test_export_enthaelt_keine_passwoerter(self):
        """Ein Export wandert per Mail. Passwörter haben darin nichts zu suchen."""
        self.anmelden()
        roh, zip_datei = self._export_holen()
        self.assertNotIn(b"pbkdf2", roh)
        kopf = zip_datei.read("benutzer.csv").decode("utf-8-sig").splitlines()[0]
        self.assertNotIn("passwort", kopf)
        self.assertIn("anmeldename", kopf)

    def test_export_bringt_die_fotos_mit(self):
        self.anmelden()
        kennung = self._fahrzeug("gespeichert")
        foto = json.loads(self._foto(kennung).read().decode())
        roh, zip_datei = self._export_holen()
        self.assertIn("fotos/%s" % foto["datei"], zip_datei.namelist())
        self.assertEqual(zip_datei.read("fotos/%s" % foto["datei"]), self.JPEG)

    def test_export_wird_blockweise_gesendet(self):
        """Ein read() auf die ganze Datei killt den Dienst, sobald genug Fotos
        zusammengekommen sind. Diese Zusicherung haelt die Entscheidung fest."""
        import inspect
        quelle = inspect.getsource(server.Griff._export)
        self.assertIn("datei.read(self.BLOCK)", quelle)
        self.assertNotIn("datei.read()", quelle)

    def test_export_bleibt_bei_vielen_fotos_vollstaendig(self):
        self.anmelden()
        kennung = self._fahrzeug("gespeichert")
        erwartet = {}
        for i in range(6):
            bild = self.JPEG + bytes([i]) * 20000       # jedes Foto eigener Inhalt
            antwort = json.loads(self._foto(kennung, bild=bild).read().decode())
            self.assertTrue(antwort["ok"], antwort)
            erwartet[antwort["datei"]] = bild
        roh, zip_datei = self._export_holen()
        for name, inhalt in erwartet.items():
            self.assertEqual(zip_datei.read("fotos/%s" % name), inhalt, name)

    def test_export_ist_fuer_excel_lesbar(self):
        """Semikolon und BOM, sonst zerlegt Excel in Deutschland die Spalten falsch."""
        self.anmelden()
        roh, zip_datei = self._export_holen()
        inhalt = zip_datei.read("autohaus.csv")
        self.assertTrue(inhalt.startswith(b"\xef\xbb\xbf"))
        kopf = inhalt.decode("utf-8-sig").splitlines()[0]
        self.assertIn(";", kopf)
        self.assertNotIn(",", kopf.replace("\r", ""))

    def test_export_wird_protokolliert(self):
        self.anmelden()
        self._export_holen()
        self.assertIn("Datenexport erzeugt", self.holen("/protokoll").read().decode())

    def test_erfasser_darf_nicht_exportieren(self):
        self.anmelden("halle")
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self.holen("/export")
        self.assertEqual(fall.exception.code, 403)

    def test_verwaltung_bietet_den_export_an(self):
        self.anmelden()
        seite = self.holen("/verwaltung").read().decode()
        self.assertIn('href="/export"', seite)

    # --- A2: Lesehilfe und Barcode-Arten ---

    def test_erfassung_bietet_barcode_und_lesehilfe(self):
        self.anmelden()
        seite = self.holen("/erfassen").read().decode()
        self.assertIn('id="scannen"', seite)
        self.assertIn('id="lesehilfe-knopf"', seite)
        self.assertIn('id="lesehilfe-bild"', seite)
        # Die Aufnahme darf nicht als gespeichertes Foto missverstanden werden.
        self.assertIn("wird nicht gespeichert", seite)

    def test_barcode_arten_decken_die_gaengigen_typenschilder_ab(self):
        quelle = self.holen("/statisch/app.js").read().decode()
        for art in ("code_128", "code_39", "data_matrix", "pdf417", "itf"):
            self.assertIn('"%s"' % art, quelle)

    def test_lesehilfe_laeuft_ueber_dasselbe_dateifeld(self):
        """Ein Dateifeld für Fotos und Lesehilfe. Die beiden dürfen sich nicht
        gegenseitig auslösen."""
        self.anmelden()
        seite = self.holen("/erfassen").read().decode()
        self.assertEqual(seite.count('id="foto-datei"'), 1)
        quelle = self.holen("/statisch/app.js").read().decode()
        self.assertIn("if (!datei || !offen) return;", quelle)
        self.assertIn("if (!wartet) return;", quelle)

    # --- A1: Fotos am Fahrzeug ---

    JPEG = b"\xff\xd8\xff" + b"\x00" * 200
    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

    # Eigenes Zeitfenster, damit diese Fahrzeuge in keiner Abrechnung eines
    # anderen Tests landen. Und feste Nummern statt hash(), das ist je Lauf anders.
    FOTO_MARKEN = ["gespeichert", "png", "kein-bild", "art", "zwei"]

    def _fahrzeug(self, marke="foto"):
        nummer = 600 + self.FOTO_MARKEN.index(marke)
        antwort = json.loads(self.senden("/api/erfassen", {
            "fin": hilfe.fin_nummer(nummer), "autohaus_id": 1,
            "leistung_id": 1, "fertig_am": "2026-05-05", "vorgang": "f-%s" % marke,
        }, json_modus=True).read().decode())
        self.assertTrue(antwort["ok"], antwort)
        return antwort["id"]

    def _foto(self, fahrzeug_id, art="aussen", bild=None):
        import base64
        return self.senden("/api/foto", {
            "fahrzeug_id": fahrzeug_id, "art": art,
            "daten": "data:image/jpeg;base64," + base64.b64encode(bild or self.JPEG).decode(),
        }, json_modus=True)

    def test_foto_wird_gespeichert_und_erscheint_am_fahrzeug(self):
        self.anmelden()
        kennung = self._fahrzeug("gespeichert")
        antwort = json.loads(self._foto(kennung).read().decode())
        self.assertTrue(antwort["ok"], antwort)
        self.assertTrue(os.path.isfile(os.path.join(server.FOTOS, antwort["datei"])))
        self.assertEqual(antwort["adresse"], "/fotos/%s" % antwort["datei"])
        # Wird ausgeliefert
        self.assertEqual(self.holen(antwort["adresse"]).read(), self.JPEG)
        # Steht auf der Fahrzeugseite
        seite = self.holen("/fahrzeuge").read().decode()
        self.assertIn(antwort["datei"], seite)
        # Und im Protokoll
        self.assertIn("Foto angehängt", self.holen("/protokoll").read().decode())

    def test_png_geht_auch(self):
        self.anmelden()
        kennung = self._fahrzeug("png")
        antwort = json.loads(self._foto(kennung, bild=self.PNG).read().decode())
        self.assertTrue(antwort["ok"], antwort)
        self.assertTrue(antwort["datei"].endswith(".png"))

    def test_kein_bild_wird_abgelehnt(self):
        """Ohne diese Pruefung landet beliebiger Inhalt unter einem .jpg."""
        self.anmelden()
        kennung = self._fahrzeug("kein-bild")
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self._foto(kennung, bild=b"<html>kein Bild</html>")
        antwort = json.loads(fall.exception.read().decode())
        self.assertFalse(antwort["ok"])
        self.assertIn("kein JPEG", antwort["fehler"])

    def test_unbekannte_fotoart_wird_abgelehnt(self):
        self.anmelden()
        kennung = self._fahrzeug("art")
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self._foto(kennung, art="ruecksitz")
        self.assertIn("Fotoart", json.loads(fall.exception.read().decode())["fehler"])

    def test_foto_ohne_fahrzeug_hinterlaesst_keine_datei(self):
        self.anmelden()
        vorher = len(os.listdir(server.FOTOS)) if os.path.isdir(server.FOTOS) else 0
        with self.assertRaises(urllib.error.HTTPError):
            self._foto(999999)
        nachher = len(os.listdir(server.FOTOS)) if os.path.isdir(server.FOTOS) else 0
        self.assertEqual(vorher, nachher)

    def test_zwei_fotos_ueberschreiben_sich_nicht(self):
        self.anmelden()
        kennung = self._fahrzeug("zwei")
        eins = json.loads(self._foto(kennung, "aussen").read().decode())
        zwei = json.loads(self._foto(kennung, "innen").read().decode())
        self.assertNotEqual(eins["datei"], zwei["datei"])
        seite = self.holen("/fahrzeuge").read().decode()
        self.assertIn(eins["datei"], seite)
        self.assertIn(zwei["datei"], seite)

    # --- Eine Aenderung am Aussehen muss den Browser auch erreichen ---

    def test_seite_verweist_auf_die_aktuelle_fassung(self):
        """Ohne Fingerabdruck in der Adresse liefert ein gefuellter Zwischenspeicher
        ewig das alte Design aus. Genau das ist am 21.09. passiert."""
        from hallenbuch import seiten
        seite = self.holen("/anmelden").read().decode()
        self.assertIn("stil.css?v=%s" % seiten.MARKE, seite)
        self.assertIn("app.js?v=%s" % seiten.MARKE, seite)
        self.assertEqual(seiten.MARKE, seiten._fingerabdruck())

    def test_fingerabdruck_folgt_dem_inhalt(self):
        import hashlib
        import os
        h = hashlib.sha256()
        for name in ("stil.css", "app.js", "schriften.css"):
            with open(os.path.join(seiten_pfad(), name), "rb") as d:
                h.update(d.read())
        from hallenbuch import seiten
        self.assertEqual(seiten.MARKE, h.hexdigest()[:10])

    def test_zwischenspeicher_regeln(self):
        from hallenbuch import seiten
        mit = self.holen("/statisch/stil.css?v=%s" % seiten.MARKE)
        self.assertIn("immutable", mit.headers.get("Cache-Control", ""))
        ohne = self.holen("/statisch/stil.css")
        self.assertIn("no-cache", ohne.headers.get("Cache-Control", ""))
        seite = self.holen("/anmelden")
        self.assertIn("no-store", seite.headers.get("Cache-Control", ""))

    def test_service_worker_fragt_zuerst_das_netz(self):
        """Cache-first war der Fehler. Der Zwischenspeicher ist nur der Notnagel."""
        quelle = self.holen("/sw.js").read().decode()
        self.assertIn("hallenbuch-v2", quelle)
        netz = quelle.index("fetch(e.request)")
        speicher = quelle.index("caches.match")
        self.assertLess(netz, speicher,
                        "Der Zwischenspeicher darf erst nach dem Netz drankommen.")

    def test_statische_dateien_und_gesundheit(self):
        self.assertEqual(self.holen("/gesund").read().decode(), "ok")
        self.assertIn(b"Hallenbuch", self.holen("/statisch/stil.css").read())
        self.assertIn(b"schriften", self.holen("/statisch/schriften.css").read())
        with self.assertRaises(urllib.error.HTTPError):
            self.holen("/statisch/../hallenbuch/datenbank.py")


if __name__ == "__main__":
    unittest.main()


class LeereZustaende(unittest.TestCase):
    """Wie sieht das Hallenbuch am allerersten Tag aus, bevor irgendetwas
    angelegt ist? Genau dann darf es keine stummen Sackgassen geben."""

    @classmethod
    def setUpClass(cls):
        os.environ["HALLENBUCH_GEHEIMNIS"] = "test-geheimnis"
        cls.ordner = tempfile.mkdtemp(prefix="hallenbuch-leer-")
        cls.db = os.path.join(cls.ordner, "leer.db")
        verb = datenbank.verbinden(cls.db)
        datenbank.aufbauen(verb)
        for name, anmeldename, rolle in (("Achmed", "achmed", "buero"),
                                         ("Halle", "halle", "erfasser")):
            verb.execute(
                "INSERT INTO benutzer (name,anmeldename,passwort,rolle,angelegt_am)"
                " VALUES (?,?,?,?,?)",
                (name, anmeldename, sicherheit.passwort_hashen("geheim123"), rolle,
                 datenbank.jetzt()))
        verb.close()
        cls.port = freier_port()
        server.Griff.anwendung = server.Hallenbuch(cls.db, lexware.Attrappe())
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), server.Griff)
        cls.faden = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.faden.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.oeffner = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))

    def anmelden(self, name="achmed"):
        anfrage = urllib.request.Request(
            "http://127.0.0.1:%d/anmelden" % self.port,
            data=urllib.parse.urlencode(
                {"anmeldename": name, "passwort": "geheim123"}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": "http://127.0.0.1:%d" % self.port}, method="POST")
        return self.oeffner.open(anfrage, timeout=10)

    def holen(self, weg):
        return self.oeffner.open("http://127.0.0.1:%d%s" % (self.port, weg), timeout=10)

    def test_erfassen_ohne_stammdaten_ist_keine_sackgasse(self):
        """Ohne Autohaus und Leistung ist die Maske nicht absendbar. Wenn dann
        nirgends steht warum, sucht der Mitarbeiter den Fehler bei sich."""
        self.anmelden()
        seite = self.holen("/erfassen").read().decode()
        self.assertIn("Noch nicht eingerichtet", seite)
        self.assertIn("Autohäuser und Leistungen", seite)
        self.assertNotIn('name="fin"', seite)          # keine tote Maske
        self.assertIn('href="/verwaltung"', seite)     # Weg heraus fürs Büro

    def test_die_halle_bekommt_keinen_weg_ins_buero_gezeigt(self):
        """Der Erfasser darf gar nicht in die Stammdaten. Ein Knopf dorthin
        wäre eine zweite Sackgasse."""
        self.anmelden("halle")
        seite = self.holen("/erfassen").read().decode()
        self.assertIn("Noch nicht eingerichtet", seite)
        self.assertIn("Das legt das Büro an", seite)
        self.assertNotIn('class="knopf haupt" href="/verwaltung"', seite)

    def test_fahrzeugliste_am_ersten_tag(self):
        self.anmelden()
        seite = self.holen("/fahrzeuge").read().decode()
        self.assertIn("Noch kein Fahrzeug erfasst", seite)
        self.assertIn('href="/erfassen"', seite)

    def test_buero_am_ersten_tag(self):
        self.anmelden()
        seite = self.holen("/buero").read().decode()
        self.assertIn("Im Zeitraum ist nichts offen", seite)
        self.assertIn("Noch keine Rechnung erzeugt", seite)

    def test_stammdaten_erklaeren_ihre_leeren_tabellen(self):
        self.anmelden()
        seite = self.holen("/verwaltung").read().decode()
        self.assertIn("Noch kein Autohaus angelegt", seite)
        self.assertIn("Noch keine Leistung angelegt", seite)

    def test_protokoll_am_ersten_tag(self):
        self.anmelden()
        # Die Anmeldung selbst steht schon drin, also erst einmal ohne sie pruefen:
        seite = self.holen("/protokoll").read().decode()
        self.assertIn("<table>", seite)
        self.assertNotIn("<tbody></tbody>", seite)


class Konten(unittest.TestCase):
    """Passwort ändern, Zugänge stilllegen. Eigene Datenbank, weil diese Tests
    Passwörter umschreiben und sonst alle anderen mitreissen würden."""

    @classmethod
    def setUpClass(cls):
        os.environ["HALLENBUCH_GEHEIMNIS"] = "test-geheimnis"
        cls.ordner = tempfile.mkdtemp(prefix="hallenbuch-konten-")
        cls.db = os.path.join(cls.ordner, "konten.db")
        verb = datenbank.verbinden(cls.db)
        datenbank.aufbauen(verb)
        # Jeder Test bekommt seinen eigenen Zugang. Sonst ändert ein Test ein
        # Passwort und der nächste kommt nicht mehr hinein — und das Ergebnis
        # hängt dann an der Reihenfolge statt am Code.
        for name, anmeldename, rolle in (("Achmed", "achmed", "buero"),
                                         ("Zweitbuero", "zweit", "buero"),
                                         ("Tipper", "tipper", "erfasser"),
                                         ("Verloren", "verloren", "erfasser"),
                                         ("Stillzulegen", "stillzu", "erfasser"),
                                         ("Halle", "halle", "erfasser")):
            verb.execute(
                "INSERT INTO benutzer (name,anmeldename,passwort,rolle,angelegt_am)"
                " VALUES (?,?,?,?,?)",
                (name, anmeldename, sicherheit.passwort_hashen("geheim123"), rolle,
                 datenbank.jetzt()))
        verb.close()
        cls.port = freier_port()
        server.Griff.anwendung = server.Hallenbuch(cls.db, lexware.Attrappe())
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), server.Griff)
        cls.faden = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.faden.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def basis(self, weg=""):
        return "http://127.0.0.1:%d%s" % (self.port, weg)

    def sitzung(self, name="achmed", passwort="geheim123"):
        oeffner = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))
        anfrage = urllib.request.Request(
            self.basis("/anmelden"),
            data=urllib.parse.urlencode(
                {"anmeldename": name, "passwort": passwort}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": self.basis()}, method="POST")
        oeffner.open(anfrage, timeout=10)
        return oeffner

    def senden(self, oeffner, weg, felder):
        anfrage = urllib.request.Request(
            self.basis(weg), data=urllib.parse.urlencode(felder).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": self.basis()}, method="POST")
        return oeffner.open(anfrage, timeout=10)

    def nutzer_id(self, anmeldename):
        verb = datenbank.verbinden(self.db)
        try:
            return verb.execute("SELECT id FROM benutzer WHERE anmeldename=?",
                                (anmeldename,)).fetchone()[0]
        finally:
            verb.close()

    # --- eigenes Passwort ---

    def test_kontoseite_ist_ueber_den_namen_erreichbar(self):
        oeffner = self.sitzung()
        seite = oeffner.open(self.basis("/erfassen"), timeout=10).read().decode()
        self.assertIn('href="/konto"', seite)
        konto = oeffner.open(self.basis("/konto"), timeout=10).read().decode()
        self.assertIn("Passwort ändern", konto)

    def test_falsches_altes_passwort_wird_abgewiesen(self):
        oeffner = self.sitzung("tipper")
        antwort = self.senden(oeffner, "/konto", {
            "alt": "stimmtnicht", "neu": "nochgeheimer", "wiederholung": "nochgeheimer"})
        self.assertIn("bisherige Passwort stimmt nicht",
                      urllib.parse.unquote(antwort.geturl()))

    def test_zu_kurz_und_ungleich_werden_abgewiesen(self):
        oeffner = self.sitzung("tipper")
        kurz = self.senden(oeffner, "/konto", {
            "alt": "geheim123", "neu": "kurz", "wiederholung": "kurz"})
        self.assertIn("mindestens 8 Zeichen", urllib.parse.unquote(kurz.geturl()))
        ungleich = self.senden(oeffner, "/konto", {
            "alt": "geheim123", "neu": "langgenug1", "wiederholung": "langgenug2"})
        self.assertIn("nicht gleich", urllib.parse.unquote(ungleich.geturl()))

    def test_passwortwechsel_wirft_andere_geraete_hinaus(self):
        """Der eigentliche Zweck: Wer sein Passwort ändert, beendet damit die
        Sitzungen auf allen anderen Geräten."""
        handy = self.sitzung("zweit")
        rechner = self.sitzung("zweit")
        # Beide sind drin
        self.assertIn("Abrechnen", rechner.open(self.basis("/buero"), timeout=10)
                      .read().decode())
        self.senden(rechner, "/konto", {
            "alt": "geheim123", "neu": "ganzneuesding", "wiederholung": "ganzneuesding"})
        # Der Rechner bleibt drin
        self.assertIn("Abrechnen", rechner.open(self.basis("/buero"), timeout=10)
                      .read().decode())
        # Das Handy fliegt raus
        self.assertIn("Anmelden", handy.open(self.basis("/buero"), timeout=10)
                      .read().decode())
        # Und das neue Passwort gilt
        neu = self.sitzung("zweit", "ganzneuesding")
        self.assertIn("Abrechnen", neu.open(self.basis("/buero"), timeout=10)
                      .read().decode())

    # --- Büro setzt zurück und legt still ---

    def test_buero_setzt_passwort_zurueck_und_beendet_die_sitzung(self):
        """Der Fall 'Handy weg': Das alte Gerät muss sofort draussen sein."""
        verloren = self.sitzung("verloren")
        self.assertIn("Fahrzeug erfassen",
                      verloren.open(self.basis("/erfassen"), timeout=10).read().decode())
        buero = self.sitzung()
        self.senden(buero, "/verwaltung/passwort", {
            "benutzer_id": self.nutzer_id("verloren"),
            "passwort": "frischesding", "wiederholung": "frischesding"})
        self.assertIn("Anmelden",
                      verloren.open(self.basis("/erfassen"), timeout=10).read().decode())

    def test_eigenen_zugang_kann_man_nicht_stilllegen(self):
        buero = self.sitzung()
        antwort = self.senden(
            buero, "/verwaltung/benutzer/%d/stilllegen" % self.nutzer_id("achmed"), {})
        self.assertIn("eigenen Zugang", urllib.parse.unquote(antwort.geturl()))

    def test_der_letzte_buerozugang_bleibt(self):
        """Sonst kann niemand mehr abrechnen und auch niemand mehr etwas ändern."""
        verb = datenbank.verbinden(self.db)
        try:
            verb.execute("UPDATE benutzer SET aktiv=0 WHERE anmeldename='zweit'")
        finally:
            verb.close()
        zweit_id = self.nutzer_id("zweit")
        buero = self.sitzung()
        # Jetzt ist achmed der letzte aktive Bürozugang, ein zweiter Bürozugang
        # wird angelegt und gleich wieder stillgelegt: das muss gehen.
        self.senden(buero, "/verwaltung/benutzer", {
            "name": "Dritter", "anmeldename": "dritt", "passwort": "geheim123",
            "rolle": "buero"})
        dritt_id = self.nutzer_id("dritt")
        ok = self.senden(buero, "/verwaltung/benutzer/%d/stilllegen" % dritt_id, {})
        self.assertIn("stillgelegt", urllib.parse.unquote(ok.geturl()))
        # achmed selbst bleibt geschützt (eigener Zugang)
        selbst = self.senden(
            buero, "/verwaltung/benutzer/%d/stilllegen" % self.nutzer_id("achmed"), {})
        self.assertIn("eigenen Zugang", urllib.parse.unquote(selbst.geturl()))
        self.senden(buero, "/verwaltung/benutzer/%d/aktivieren" % zweit_id, {})

    def test_stillgelegter_zugang_kommt_sofort_nicht_mehr_hinein(self):
        opfer = self.sitzung("stillzu")
        buero = self.sitzung()
        self.senden(buero,
                    "/verwaltung/benutzer/%d/stilllegen" % self.nutzer_id("stillzu"), {})
        self.assertIn("Anmelden",
                      opfer.open(self.basis("/erfassen"), timeout=10).read().decode())
        # Und wieder öffnen geht auch
        self.senden(buero,
                    "/verwaltung/benutzer/%d/aktivieren" % self.nutzer_id("stillzu"), {})
        wieder = self.sitzung("stillzu")
        self.assertIn("Fahrzeug erfassen",
                      wieder.open(self.basis("/erfassen"), timeout=10).read().decode())

    def test_die_halle_darf_keine_zugaenge_stilllegen(self):
        halle = self.sitzung("halle")
        with self.assertRaises(urllib.error.HTTPError) as fall:
            self.senden(halle, "/verwaltung/benutzer/%d/stilllegen"
                        % self.nutzer_id("zweit"), {})
        self.assertEqual(fall.exception.code, 403)


class HttpSchicht(unittest.TestCase):
    """Die Schicht davor: Herkunft, Anfragekörper, Statuskennzahlen, Fehlertexte.

    Eigene Datenbank und eigener Port, weil hier absichtlich kaputte Anfragen
    geschickt werden und dabei Verbindungen zerbrechen dürfen.
    """

    @classmethod
    def setUpClass(cls):
        os.environ["HALLENBUCH_GEHEIMNIS"] = "test-geheimnis"
        cls.ordner = tempfile.mkdtemp(prefix="hallenbuch-http-")
        cls.db = os.path.join(cls.ordner, "http.db")
        verb = datenbank.verbinden(cls.db)
        datenbank.aufbauen(verb)
        verb.execute(
            "INSERT INTO benutzer (name,anmeldename,passwort,rolle,angelegt_am)"
            " VALUES ('Achmed','achmed',?,'buero',?)",
            (sicherheit.passwort_hashen("geheim123"), datenbank.jetzt()))
        verb.execute(
            "INSERT INTO autohaus (id,name,kurz,strasse,plz,ort)"
            " VALUES (1,'Autohaus Nordstadt','NORD','Industriestr. 12','38228','Salzgitter')")
        verb.execute(
            "INSERT INTO leistung (id,bezeichnung,netto_cent) VALUES (1,'Vollaufbereitung',14900)")
        verb.close()
        cls.fotos_vorher = server.FOTOS
        server.FOTOS = os.path.join(cls.ordner, "fotos")
        os.makedirs(server.FOTOS, exist_ok=True)
        cls.port = freier_port()
        server.Griff.anwendung = server.Hallenbuch(cls.db, lexware.Attrappe())
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), server.Griff)
        cls.faden = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.faden.start()

    @classmethod
    def tearDownClass(cls):
        server.FOTOS = cls.fotos_vorher
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.jar = CookieJar()
        self.oeffner = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.oeffner.open(urllib.request.Request(
            self.basis("/anmelden"),
            data=urllib.parse.urlencode(
                {"anmeldename": "achmed", "passwort": "geheim123"}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": self.basis()}, method="POST"), timeout=10)

    def basis(self, weg=""):
        return "http://127.0.0.1:%d%s" % (self.port, weg)

    def wirt(self):
        return "127.0.0.1:%d" % self.port

    def keks(self):
        return "; ".join("%s=%s" % (c.name, c.value) for c in self.jar)

    def roh(self, verfahren, weg, koerper=None, kopf=None, verbindung=None):
        """Eine Anfrage an der Bibliothek vorbei, damit Kopfzeilen exakt stimmen."""
        v = verbindung or http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        k = {"Host": self.wirt(), "Cookie": self.keks()}
        if koerper is not None:
            k["Content-Length"] = str(len(koerper))
            k["Content-Type"] = "application/json"
        k.update(kopf or {})
        v.request(verfahren, weg, body=koerper, headers=k)
        antwort = v.getresponse()
        text = antwort.read().decode("utf-8", "replace")
        if verbindung is None:
            v.close()
        return antwort.status, text

    def erfassung(self, fin, vorgang):
        return json.dumps({"fin": fin, "autohaus_id": 1, "leistung_id": 1,
                           "fertig_am": "2026-09-21", "vorgang": vorgang}).encode()

    # --- Herkunft ---------------------------------------------------------

    def test_fremde_seite_mit_unserem_namen_im_referer_wird_abgewiesen(self):
        # https://angreifer.example/?z=hallenbuch.fynaxa.de enthält unseren
        # Namen. Der frühere Teilstringvergleich liess das durch.
        kode, _ = self.roh("POST", "/api/erfassen",
                           self.erfassung(hilfe.fin_nummer(700), "csrf-referer"),
                           {"Referer": "https://angreifer.example/?z=%s" % self.wirt()})
        self.assertEqual(kode, 403)

    def test_fremde_seite_mit_unserem_namen_davor_wird_abgewiesen(self):
        # hallenbuch.fynaxa.de.angreifer.example ist eine fremde Adresse, die
        # unsere als Anfang enthält.
        kode, _ = self.roh("POST", "/api/erfassen",
                           self.erfassung(hilfe.fin_nummer(701), "csrf-origin"),
                           {"Origin": "https://%s.angreifer.example" % self.wirt()})
        self.assertEqual(kode, 403)

    def test_eigene_herkunft_und_gar_keine_gehen_weiter_durch(self):
        kode, _ = self.roh("POST", "/api/erfassen",
                           self.erfassung(hilfe.fin_nummer(702), "eigenes-origin"),
                           {"Origin": self.basis()})
        self.assertEqual(kode, 200)
        # Die Warteschlange am Handy schickt beim Nachladen keinen Referer mit.
        kode, _ = self.roh("POST", "/api/erfassen",
                           self.erfassung(hilfe.fin_nummer(703), "ohne-herkunft"))
        self.assertEqual(kode, 200)

    # --- Anfragekörper ----------------------------------------------------

    def test_unbenutzter_rumpf_zerstoert_die_verbindung_nicht(self):
        # /buero wertet bei POST kein Formular aus. Blieb der Rumpf stehen, las
        # der Server ihn als nächste Anfrage: gemessen 501 statt 200.
        v = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        koerper = urllib.parse.urlencode({"x": "y" * 200}).encode()
        kode, _ = self.roh("POST", "/buero", koerper,
                           {"Origin": self.basis(),
                            "Content-Type": "application/x-www-form-urlencoded"},
                           verbindung=v)
        self.assertEqual(kode, 200)
        kode, text = self.roh("GET", "/gesund", verbindung=v)
        v.close()
        self.assertEqual(kode, 200)
        self.assertEqual(text, "ok")

    def test_abgelehnte_herkunft_laesst_die_verbindung_heil(self):
        v = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        kode, _ = self.roh("POST", "/api/erfassen",
                           self.erfassung(hilfe.fin_nummer(704), "csrf-rumpf"),
                           {"Origin": "https://angreifer.example"}, verbindung=v)
        self.assertEqual(kode, 403)
        kode, text = self.roh("GET", "/gesund", verbindung=v)
        v.close()
        self.assertEqual((kode, text), (200, "ok"))

    def test_zu_grosse_anfrage_wird_abgewiesen_und_die_leitung_geschlossen(self):
        v = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        v.putrequest("POST", "/api/erfassen")
        v.putheader("Host", self.wirt())
        v.putheader("Cookie", self.keks())
        v.putheader("Origin", self.basis())
        v.putheader("Content-Type", "application/json")
        v.putheader("Content-Length", str(server.GROESSTE_ANFRAGE + 1))
        v.endheaders()
        antwort = v.getresponse()
        text = antwort.read().decode()
        v.close()
        self.assertEqual(antwort.status, 400)
        self.assertIn("zu groß", json.loads(text)["fehler"])
        self.assertEqual(antwort.getheader("Connection"), "close")

    # --- Statuskennzahlen und Fehlertexte ---------------------------------

    def test_ohne_anmeldung_antwortet_die_schnittstelle_mit_401(self):
        # Kein Keks: die Warteschlange darf KEINE 200 mit der Anmeldeseite
        # bekommen, sonst haelt sie den Eintrag fuer gespeichert.
        kode, text = self.roh("POST", "/api/erfassen",
                              self.erfassung(hilfe.fin_nummer(707), "abgelaufen"),
                              {"Origin": self.basis(), "Cookie": ""})
        self.assertEqual(kode, 401)
        self.assertFalse(json.loads(text)["ok"])
        # Der Browser bekommt weiterhin die Umleitung auf die Anmeldeseite.
        kode, _ = self.roh("GET", "/erfassen", kopf={"Cookie": ""})
        self.assertEqual(kode, 303)

    def test_unbekannte_rechnung_ist_nicht_gefunden(self):
        kode, text = self.roh("GET", "/rechnung/99999")
        self.assertEqual(kode, 404)
        self.assertIn("gibt es nicht", text)

    def test_eine_riesige_kennung_ist_nicht_gefunden_statt_kaputt(self):
        """`/rechnung/99999999999999999999` kippte SQLite um
        (`Python int too large`) und endete als Serverfehler."""
        for weg in ("/rechnung/99999999999999999999",
                    "/gutschrift/99999999999999999999"):
            kode, _ = self.roh("GET", weg)
            self.assertIn(kode, (404, 303), weg)

    def test_unlesbares_json_wird_auf_deutsch_abgelehnt(self):
        """Die Meldung von `json` ist englisch und nennt Spaltennummern. In der
        Warteschlange am Handy stünde sie als Grund der Ablehnung."""
        kode, text = self.roh("POST", "/api/erfassen", b'{"fin": "a"a}',
                              {"Origin": self.basis()})
        self.assertEqual(kode, 400)
        grund = json.loads(text)["fehler"]
        self.assertIn("beschädigt", grund)
        self.assertNotIn("Expecting", grund)

    def test_kaputtes_bild_wird_auf_deutsch_abgelehnt(self):
        koerper = json.dumps({"fahrzeug_id": 1, "art": "aussen",
                              "daten": "nicht base64!!"}).encode()
        kode, text = self.roh("POST", "/api/foto", koerper, {"Origin": self.basis()})
        self.assertEqual(kode, 400)
        grund = json.loads(text)["fehler"]
        self.assertIn("noch einmal aufnehmen", grund)
        self.assertNotIn("padding", grund)

    def test_unmoegliches_datum_in_der_ansicht_wird_benannt(self):
        """Vorher kam eine leere Liste zurück, und das liest sich wie
        „nichts da" statt wie „so geht das nicht"."""
        kode, text = self.roh("GET", "/fahrzeuge?von=2026-13-45&bis=2026-09-20")
        self.assertEqual(kode, 400)
        self.assertIn("kein gültiges Datum", text)
        kode, text = self.roh("GET", "/buero?von=morgen&bis=gestern")
        self.assertEqual(kode, 400)
        self.assertIn("kein gültiges Datum", text)

    def test_kaputte_zahl_in_der_adresse_ist_kein_serverfehler(self):
        kode, text = self.roh("GET", "/fahrzeuge?autohaus_id=abc")
        self.assertEqual(kode, 400)
        self.assertIn("keine Zahl", text)

    def test_liste_statt_objekt_wird_verworfen_statt_ewig_wiederholt(self):
        # 400 ist hier wichtig: die Warteschlange am Handy wirft 400er weg und
        # wiederholt alles andere. Vorher gab es 500, der Eintrag wäre geblieben.
        kode, text = self.roh("POST", "/api/erfassen", json.dumps([1, 2, 3]).encode(),
                              {"Origin": self.basis()})
        self.assertEqual(kode, 400)
        self.assertFalse(json.loads(text)["ok"])

    def test_verschachteltes_feld_wird_verworfen(self):
        koerper = json.dumps({"fin": hilfe.fin_nummer(705), "autohaus_id": {"a": 1},
                              "leistung_id": 1, "vorgang": "verschachtelt"}).encode()
        kode, text = self.roh("POST", "/api/erfassen", koerper, {"Origin": self.basis()})
        self.assertEqual(kode, 400)
        self.assertIn("falsche Form", json.loads(text)["fehler"])

    def test_fehlendes_formularfeld_wird_deutsch_beantwortet(self):
        anfrage = urllib.request.Request(
            self.basis("/erfassen"),
            data=urllib.parse.urlencode({"fin": hilfe.fin_nummer(706)}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": self.basis()}, method="POST")
        ziel = urllib.parse.unquote(self.oeffner.open(anfrage, timeout=10).geturl())
        self.assertIn("keine Zahl", ziel)

    def test_serverfehler_nennt_nur_eine_nummer_keine_ausnahme(self):
        def platzt(*args, **kwargs):
            raise RuntimeError("SELECT geheim FROM benutzer")
        vorher = server.seiten.protokoll_seite
        server.seiten.protokoll_seite = platzt
        try:
            kode, text = self.roh("GET", "/protokoll")
        finally:
            server.seiten.protokoll_seite = vorher
        self.assertEqual(kode, 500)
        self.assertNotIn("geheim", text)
        self.assertNotIn("RuntimeError", text)
        self.assertIn("Nummer", text)

    def test_doppeltes_kuerzel_wird_auf_deutsch_abgelehnt(self):
        anfrage = urllib.request.Request(
            self.basis("/verwaltung/autohaus"),
            data=urllib.parse.urlencode(
                {"name": "Zweites Nordstadt", "kurz": "NORD"}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": self.basis()}, method="POST")
        ziel = urllib.parse.unquote(self.oeffner.open(anfrage, timeout=10).geturl())
        self.assertIn("Kürzel gibt es schon", ziel)
        self.assertNotIn("UNIQUE", ziel)

    # --- Was erfasst wird, muss auch jemand sehen -------------------------

    def test_suchbegriffe_landen_nicht_im_systemprotokoll(self):
        """FIN und Kennzeichen stehen in der Adresszeile. Im Journal des
        Servers haben sie nichts verloren: dort gilt weder unsere
        Aufbewahrungsfrist noch unser Löschweg."""
        self.assertEqual(server.nur_weg("/suche?q=WVWZZZ1JZ3W386752"), "/suche")
        self.assertEqual(server.nur_weg("/fahrzeuge?autohaus_id=3&von=2026-09-01"),
                         "/fahrzeuge")
        self.assertEqual(server.nur_weg("/erfassen"), "/erfassen")
        self.assertEqual(server.nur_weg(""), "")
        self.assertLessEqual(len(server.nur_weg("/x" + "y" * 500)), 200)

    def test_eine_krumme_portangabe_wird_benannt(self):
        """Vorher endete ein Tippfehler in der Umgebung in einem Rückverfolg.
        Wer den Dienst einrichtet, liest dann Python statt einer Anweisung."""
        self.assertEqual(server.port_lesen(None), 8080)
        self.assertEqual(server.port_lesen(""), 8080, "leer heisst Voreinstellung")
        self.assertEqual(server.port_lesen("9000"), 9000)
        self.assertEqual(server.port_lesen(" 8080 "), 8080)
        for krumm in ("achtzig", "0", "70000", "-1", "80.5"):
            with self.assertRaises(SystemExit) as fall:
                server.port_lesen(krumm)
            self.assertIn("HALLENBUCH_PORT", str(fall.exception))

    def test_das_formular_laesst_sich_zweimal_lesen(self):
        """Ein zweiter Aufruf kann den Rumpf nicht noch einmal aus dem Puffer
        holen. Ohne Merken lieferte er stillschweigend ein leeres Formular —
        und die Eingaben wären ohne Fehlermeldung weg."""
        class Doppelt(server.Griff):
            gesehen = []

            def _felder(self):
                erste = server.Griff._felder(self)
                zweite = server.Griff._felder(self)
                Doppelt.gesehen.append((dict(erste), dict(zweite)))
                return erste

        vorher = server.Griff.anwendung
        koerper = json.dumps({"fin": hilfe.fin_nummer(715), "autohaus_id": 1,
                              "leistung_id": 1, "fertig_am": "2026-09-18",
                              "vorgang": "doppelt-1"}).encode()
        # Der Weg laeuft ueber den echten Server, nur die Klasse liest doppelt.
        self.httpd.RequestHandlerClass = Doppelt
        Doppelt.anwendung = vorher
        try:
            kode, _ = self.roh("POST", "/api/erfassen", koerper, {"Origin": self.basis()})
        finally:
            self.httpd.RequestHandlerClass = server.Griff
        self.assertEqual(kode, 200)
        self.assertTrue(Doppelt.gesehen, "der Weg wurde nicht durchlaufen")
        erste, zweite = Doppelt.gesehen[-1]
        self.assertEqual(erste, zweite)
        self.assertEqual(zweite.get("fin"), hilfe.fin_nummer(715))

    def test_eine_umleitung_bleibt_immer_reines_ascii(self):
        """Kopfzeilen gehen als Latin-1 hinaus. Ein Umlaut, der nicht durch
        `quote` gegangen ist, kam beim Browser als Fragezeichen an — einmal
        passiert. Die Notbremse in `_umleiten` fängt jeden Rest ab."""
        class MitUmlaut(server.Griff):
            def _bedienen(self, art):
                return self._umleiten("/verwaltung?gut=Geändert.")

        MitUmlaut.anwendung = server.Griff.anwendung
        self.httpd.RequestHandlerClass = MitUmlaut
        try:
            v = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            v.request("GET", "/irgendwas", headers={"Host": self.wirt()})
            antwort = v.getresponse()
            antwort.read()
            ort = antwort.getheader("Location") or ""
            v.close()
        finally:
            self.httpd.RequestHandlerClass = server.Griff
        self.assertEqual(antwort.status, 303)
        ort.encode("ascii")          # wirft, wenn ein Umlaut durchgerutscht ist
        self.assertNotIn("ä", ort)

    def test_stammdaten_lassen_sich_ueber_die_oberflaeche_aendern(self):
        """Der Weg muss auch wirklich erreichbar sein, nicht nur die Funktion."""
        anfrage = urllib.request.Request(
            self.basis("/verwaltung/autohaus/1"),
            data=urllib.parse.urlencode({
                "name": "Autohaus Nordstadt", "kurz": "NORD",
                "strasse": "Industriestr. 12a", "plz": "38228", "ort": "Salzgitter",
                "zahlungsziel": "21", "aktiv": "on"}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": self.basis()}, method="POST")
        ziel = urllib.parse.unquote(self.oeffner.open(anfrage, timeout=10).geturl())
        self.assertIn("Geändert", ziel)
        kode, seite = self.roh("GET", "/verwaltung")
        self.assertEqual(kode, 200)
        self.assertIn("Industriestr. 12a", seite)
        self.assertIn("21 Tage", seite)

    def test_die_notiz_steht_auf_der_fahrzeugkarte(self):
        """Ein Feld, das nach einer Angabe fragt und sie niemandem zeigt, ist
        ein leeres Versprechen. Die Notiz wurde erfasst und nirgends angezeigt."""
        koerper = json.dumps({
            "fin": hilfe.fin_nummer(710), "autohaus_id": 1, "leistung_id": 1,
            "fertig_am": "2026-09-18", "vorgang": "notiz-1",
            "notiz": "Teppich stark verschmutzt"}).encode()
        kode, _ = self.roh("POST", "/api/erfassen", koerper, {"Origin": self.basis()})
        self.assertEqual(kode, 200)
        kode, seite = self.roh("GET", "/fahrzeuge")
        self.assertIn("Teppich stark verschmutzt", seite)

    def test_die_notiz_landet_als_text_nicht_als_markup(self):
        koerper = json.dumps({
            "fin": hilfe.fin_nummer(711), "autohaus_id": 1, "leistung_id": 1,
            "fertig_am": "2026-09-18", "vorgang": "notiz-2",
            "notiz": "<b>Achtung</b> Lack"}).encode()
        self.roh("POST", "/api/erfassen", koerper, {"Origin": self.basis()})
        kode, seite = self.roh("GET", "/fahrzeuge")
        self.assertIn("&lt;b&gt;Achtung&lt;/b&gt; Lack", seite)

    def test_schadenfoto_ist_am_fahrzeug_erkennbar(self):
        import base64
        koerper = json.dumps({
            "fin": hilfe.fin_nummer(712), "autohaus_id": 1, "leistung_id": 1,
            "fertig_am": "2026-09-18", "vorgang": "schaden-1"}).encode()
        kode, text = self.roh("POST", "/api/erfassen", koerper, {"Origin": self.basis()})
        kennung = json.loads(text)["id"]
        bild = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 80).decode()
        foto = json.dumps({"fahrzeug_id": kennung, "art": "schaden",
                           "daten": "data:image/jpeg;base64," + bild}).encode()
        kode, _ = self.roh("POST", "/api/foto", foto, {"Origin": self.basis()})
        self.assertEqual(kode, 200)
        kode, seite = self.roh("GET", "/fahrzeuge")
        # Die Wahl zwischen Aussen, Innen und Schaden stand vorher nur im
        # alt-Text. Jetzt hat sie eine sichtbare Folge.
        self.assertIn('class="schaden"', seite)

    def test_nachbarordner_mit_gleichem_anfang_bleibt_zu(self):
        nachbar = server.FOTOS + "-alt"
        os.makedirs(nachbar, exist_ok=True)
        with open(os.path.join(nachbar, "geheim.txt"), "w") as datei:
            datei.write("nicht fuer draussen")
        kode, text = self.roh("GET", "/fotos/../%s/geheim.txt"
                              % os.path.basename(nachbar))
        self.assertEqual(kode, 404)
        self.assertNotIn("draussen", text)

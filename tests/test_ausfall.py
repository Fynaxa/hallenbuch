"""Was passiert, wenn das Rechnungsprogramm nicht mitspielt."""
import io
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hallenbuch import datenbank, lexware, rechnung, seiten  # noqa: E402
from tests import hilfe  # noqa: E402


class Fehlerlage(unittest.TestCase):
    """Die Unterscheidung, an der Geld hängt: Ist drüben sicher nichts
    entstanden, oder kann schon ein Beleg liegen?

    Alles gestellt — ein Test ruft nicht das echte Rechnungsprogramm an.
    """

    def _mit_antwort(self, fehler):
        """Lässt urlopen den übergebenen Fehler werfen und ruft dann auf."""
        import urllib.request
        echt = urllib.request.urlopen

        def streik(*args, **kwargs):
            raise fehler

        urllib.request.urlopen = streik
        try:
            dienst = lexware.Lexware(schluessel="gestellt")
            with self.assertRaises(lexware.LexwareFehler) as fall:
                dienst._rufen("/v1/invoices", {}, "POST", versuche=1)
            return fall.exception
        finally:
            urllib.request.urlopen = echt

    def _http(self, kode):
        return urllib.error.HTTPError(
            "https://api.lexware.io/v1/invoices", kode, "Fehler", {},
            io.BytesIO(b'{"message":"so nicht"}'))

    def test_abgelehnte_anfrage_ist_sicher_nichts_entstanden(self):
        """4xx heisst: zurückgewiesen, drüben ist nichts angelegt."""
        for kode in (400, 401, 404, 422):
            fehler = self._mit_antwort(self._http(kode))
            self.assertTrue(fehler.sicher_nicht_angelegt, "HTTP %d" % kode)
            self.assertIn(str(kode), str(fehler))

    def test_serverfehler_laesst_die_lage_offen(self):
        for kode in (429, 500, 503):
            fehler = self._mit_antwort(self._http(kode))
            self.assertFalse(fehler.sicher_nicht_angelegt, "HTTP %d" % kode)

    def test_abgebrochene_verbindung_laesst_die_lage_offen(self):
        """Der teure Fall: Die Anfrage war vielleicht schon durch, als die
        Leitung abriss. Ein blinder zweiter Versuch legt dann doppelt an."""
        fehler = self._mit_antwort(urllib.error.URLError("Leitung weg"))
        self.assertFalse(fehler.sicher_nicht_angelegt)

    def test_zeitablauf_laesst_die_lage_offen(self):
        fehler = self._mit_antwort(TimeoutError("zu lange"))
        self.assertFalse(fehler.sicher_nicht_angelegt)
        self.assertIn("Zeit", str(fehler))

    def test_ohne_schluessel_ist_sicher_nichts_entstanden(self):
        """Im Probebetrieb geht gar keine Anfrage hinaus, also kann auch nichts
        entstanden sein."""
        leer = lexware.Lexware(schluessel="")
        with self.assertRaises(lexware.LexwareFehler) as fall:
            leer._rufen("/v1/invoices", {}, "POST")
        self.assertTrue(fall.exception.sicher_nicht_angelegt)
        self.assertIn("Probebetrieb", str(fall.exception))


class RechnungBeiAusfall(unittest.TestCase):

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)
        hilfe.erfassen_viele(self.verb, self.halle, 4, autohaus_id=1)
        self.r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        rechnung.freigeben(self.verb, self.buero, self.r["id"])

    def _streik(self, sicher):
        fehler = lexware.LexwareFehler("HTTP 500: kaputt", sicher)

        class Streik:
            bereit = True

            def rechnung_ausstellen(self, kopf, posten):
                raise fehler
        return Streik()

    def _stand(self):
        return self.verb.execute("SELECT * FROM rechnung WHERE id=?",
                                 (self.r["id"],)).fetchone()

    def test_rechnung_bleibt_freigegeben_und_der_grund_steht_dran(self):
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                  self._streik(True))
        zeile = self._stand()
        self.assertEqual(zeile["status"], "freigegeben")
        self.assertIn("kaputt", zeile["letzter_fehler"])
        self.assertTrue(zeile["letzter_fehler_am"])
        self.assertEqual(zeile["lexware_nummer"], "")

    def test_die_fahrzeuge_bleiben_der_rechnung_zugeordnet(self):
        """Nichts wird halb zurückgedreht — sonst tauchen die Fahrzeuge in der
        nächsten Sammelrechnung noch einmal auf."""
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                  self._streik(True))
        offen = self.verb.execute(
            "SELECT COUNT(*) FROM fahrzeug WHERE status='offen'").fetchone()[0]
        self.assertEqual(offen, 0)
        _, posten = rechnung.mit_positionen(self.verb, self.r["id"])
        self.assertEqual(len(posten), 4)

    def test_unklare_lage_wird_als_solche_vermerkt(self):
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                  self._streik(False))
        self.assertEqual(self._stand()["fehler_unklar"], 1)

    def test_abgelehnte_anfrage_wird_als_unbedenklich_vermerkt(self):
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                  self._streik(True))
        self.assertEqual(self._stand()["fehler_unklar"], 0)

    def test_der_fehlversuch_steht_im_protokoll(self):
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                  self._streik(False))
        eintraege = [z["was"] for z in self.verb.execute(
            "SELECT was FROM protokoll ORDER BY id DESC LIMIT 3")]
        self.assertIn("Ausstellen fehlgeschlagen", eintraege)

    def test_zweiter_versuch_raeumt_den_fehler_weg(self):
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                  self._streik(False))
        fertig = rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                       lexware.Attrappe())
        self.assertEqual(fertig["status"], "finalisiert")
        self.assertEqual(fertig["letzter_fehler"], "")
        self.assertEqual(fertig["fehler_unklar"], 0)
        self.assertTrue(fertig["lexware_nummer"])


if __name__ == "__main__":
    unittest.main()


class ZweiKlicks(unittest.TestCase):
    """Zwei schnelle Klicks auf denselben Knopf.

    Ohne Absicherung entstehen dabei ZWEI Belege mit zwei Nummern bei Lexware,
    und das Autohaus bekommt dieselbe Leistung zweimal berechnet. Genau davor
    warnt das Büroblatt — es darf nicht von unserer Seite passieren.
    """

    def setUp(self):
        from hallenbuch import datenbank, fahrzeuge
        self.ordner = tempfile.mkdtemp(prefix="hallenbuch-rennen-")
        self.db = os.path.join(self.ordner, "rennen.db")
        verb = datenbank.verbinden(self.db)
        datenbank.aufbauen(verb)
        verb.execute("INSERT INTO benutzer (id,name,anmeldename,passwort,rolle,angelegt_am)"
                     " VALUES (1,'Achmed','achmed','x','buero',?)", (datenbank.jetzt(),))
        verb.execute("INSERT INTO benutzer (id,name,anmeldename,passwort,rolle,angelegt_am)"
                     " VALUES (2,'Halle','halle','x','erfasser',?)", (datenbank.jetzt(),))
        verb.execute("INSERT INTO autohaus (id,name,kurz,strasse,plz,ort)"
                     " VALUES (1,'Nord','NORD','Industriestr. 12','38228','Salzgitter')")
        verb.execute("INSERT INTO leistung (id,bezeichnung,netto_cent)"
                     " VALUES (1,'Vollaufbereitung',14900)")
        halle = verb.execute("SELECT * FROM benutzer WHERE id=2").fetchone()
        for i in range(3):
            fahrzeuge.erfassen(verb, halle, hilfe.fin_nummer(i), 1, 1, "2026-09-21",
                               vorgang="rennen-%d" % i)
        self.buero = verb.execute("SELECT * FROM benutzer WHERE id=1").fetchone()
        self.r = rechnung.erzeugen(verb, self.buero, 1, "2026-09-14", "2026-09-21")
        rechnung.freigeben(verb, self.buero, self.r["id"])
        verb.close()

    def tearDown(self):
        shutil.rmtree(self.ordner, ignore_errors=True)

    def _zaehlender_dienst(self, beide_da):
        """Der erste Aufruf haelt an, bis der zweite Versuch durch ist."""
        class Langsam:
            bereit = True

            def __init__(self):
                self.aufrufe = 0
                self.sperre = threading.Lock()

            def _zaehlen(self):
                with self.sperre:
                    self.aufrufe += 1
                    erster = self.aufrufe == 1
                if erster:
                    beide_da.wait(timeout=5)
                return {"id": "x", "nummer": "RE-0001", "uri": ""}

            def rechnung_ausstellen(self, kopf, posten):
                return self._zaehlen()

            def gutschrift_ausstellen(self, zeile, grund):
                return self._zaehlen()
        return Langsam()

    def _parallel(self, arbeit):
        from hallenbuch import datenbank
        beide_da = threading.Event()
        dienst = self._zaehlender_dienst(beide_da)
        ergebnisse = []

        def lauf(verzoegern):
            verb = datenbank.verbinden(self.db)
            buero = verb.execute("SELECT * FROM benutzer WHERE id=1").fetchone()
            try:
                if verzoegern:
                    threading.Event().wait(0.25)     # klar nach dem ersten
                arbeit(verb, buero, dienst)
                ergebnisse.append("ok")
            except Exception as fehler:
                ergebnisse.append(fehler)
            finally:
                if verzoegern:
                    beide_da.set()
                verb.close()

        faeden = [threading.Thread(target=lauf, args=(i == 1,)) for i in range(2)]
        for f in faeden:
            f.start()
        for f in faeden:
            f.join(timeout=10)
        return dienst, ergebnisse

    def test_zweimal_ausstellen_ruft_lexware_nur_einmal(self):
        kennung = self.r["id"]

        def arbeit(verb, buero, dienst):
            rechnung.finalisieren(verb, buero, kennung, dienst)

        dienst, ergebnisse = self._parallel(arbeit)
        self.assertEqual(dienst.aufrufe, 1,
                         "Lexware darf genau einmal gerufen werden, sonst gibt es "
                         "zwei Rechnungen mit zwei Nummern")
        self.assertEqual(ergebnisse.count("ok"), 1)
        fehler = [e for e in ergebnisse if e != "ok"]
        self.assertEqual(len(fehler), 1)
        self.assertIn("wird gerade ausgestellt", str(fehler[0]))

    def test_zweimal_gutschrift_ruft_lexware_nur_einmal(self):
        from hallenbuch import datenbank, lexware as lx
        verb = datenbank.verbinden(self.db)
        try:
            buero = verb.execute("SELECT * FROM benutzer WHERE id=1").fetchone()
            rechnung.finalisieren(verb, buero, self.r["id"], lx.Attrappe())
            fahrzeug_id = verb.execute(
                "SELECT fahrzeug_id FROM rechnungsposition LIMIT 1").fetchone()[0]
        finally:
            verb.close()

        def arbeit(verb, buero, dienst):
            rechnung.gutschrift(verb, buero, fahrzeug_id, "Lackkratzer", dienst)

        dienst, ergebnisse = self._parallel(arbeit)
        self.assertEqual(dienst.aufrufe, 1,
                         "Sonst entstehen zwei Gutschriften mit zwei Nummern")
        self.assertEqual(ergebnisse.count("ok"), 1)

        verb = datenbank.verbinden(self.db)
        try:
            anzahl = verb.execute(
                "SELECT COUNT(*) FROM gutschrift WHERE fahrzeug_id=?",
                (fahrzeug_id,)).fetchone()[0]
            nummer = verb.execute(
                "SELECT lexware_nummer FROM gutschrift WHERE fahrzeug_id=?",
                (fahrzeug_id,)).fetchone()[0]
        finally:
            verb.close()
        self.assertEqual(anzahl, 1)
        self.assertEqual(nummer, "RE-0001")   # Belegnummer wurde nachgetragen


class GegenseitigesAussperren(unittest.TestCase):
    """Zwei Bürozugänge, die sich im selben Moment gegenseitig stilllegen.

    Ohne Transaktion sähen beide den jeweils anderen als aktiv, beide kämen
    durch — und danach käme niemand mehr hinein. Das liesse sich nur noch von
    Hand in der Datenbank reparieren.
    """

    def setUp(self):
        from hallenbuch import datenbank, sicherheit
        self.ordner = tempfile.mkdtemp(prefix="hallenbuch-aussperren-")
        self.db = os.path.join(self.ordner, "aussperren.db")
        verb = datenbank.verbinden(self.db)
        datenbank.aufbauen(verb)
        for kennung, name in ((1, "Achmed"), (2, "Zweitbüro")):
            verb.execute(
                "INSERT INTO benutzer (id,name,anmeldename,passwort,rolle,angelegt_am)"
                " VALUES (?,?,?,?,'buero',?)",
                (kennung, name, "b%d" % kennung,
                 sicherheit.passwort_hashen("geheim123"), datenbank.jetzt()))
        verb.close()

    def tearDown(self):
        shutil.rmtree(self.ordner, ignore_errors=True)

    def test_der_letzte_buerozugang_ueberlebt_auch_im_wettlauf(self):
        from hallenbuch import datenbank, konten
        los = threading.Event()
        ergebnisse = []

        def lauf(ich, ziel):
            verb = datenbank.verbinden(self.db)
            try:
                mein = verb.execute("SELECT * FROM benutzer WHERE id=?", (ich,)).fetchone()
                los.wait(timeout=5)
                konten.stilllegen(verb, mein, ziel)
                ergebnisse.append("still: %d" % ziel)
            except Exception as fehler:
                ergebnisse.append(fehler)
            finally:
                verb.close()

        faeden = [threading.Thread(target=lauf, args=(1, 2)),
                  threading.Thread(target=lauf, args=(2, 1))]
        for f in faeden:
            f.start()
        los.set()
        for f in faeden:
            f.join(timeout=10)

        verb = datenbank.verbinden(self.db)
        try:
            aktiv = verb.execute(
                "SELECT COUNT(*) FROM benutzer WHERE rolle='buero' AND aktiv=1"
            ).fetchone()[0]
        finally:
            verb.close()
        self.assertGreaterEqual(
            aktiv, 1,
            "Nach dem Wettlauf ist kein Bürozugang mehr aktiv — niemand kommt "
            "mehr hinein. Ergebnisse: %s" % ergebnisse)


class ExportStand(unittest.TestCase):
    """Der Export liest viele Tabellen. Entsteht zwischendurch eine
    Sammelrechnung, darf im Export keine Position ohne ihre Rechnung landen."""

    def setUp(self):
        from hallenbuch import datenbank, fahrzeuge
        self.ordner = tempfile.mkdtemp(prefix="hallenbuch-exportstand-")
        self.db = os.path.join(self.ordner, "stand.db")
        verb = datenbank.verbinden(self.db)
        datenbank.aufbauen(verb)
        verb.execute("INSERT INTO benutzer (id,name,anmeldename,passwort,rolle,angelegt_am)"
                     " VALUES (1,'Achmed','achmed','x','buero',?)", (datenbank.jetzt(),))
        verb.execute("INSERT INTO benutzer (id,name,anmeldename,passwort,rolle,angelegt_am)"
                     " VALUES (2,'Halle','halle','x','erfasser',?)", (datenbank.jetzt(),))
        verb.execute("INSERT INTO autohaus (id,name,kurz,strasse,plz,ort)"
                     " VALUES (1,'Nord','NORD','Industriestr. 12','38228','Salzgitter')")
        verb.execute("INSERT INTO leistung (id,bezeichnung,netto_cent)"
                     " VALUES (1,'Vollaufbereitung',14900)")
        halle = verb.execute("SELECT * FROM benutzer WHERE id=2").fetchone()
        for i in range(4):
            fahrzeuge.erfassen(verb, halle, hilfe.fin_nummer(i), 1, 1, "2026-09-21",
                               vorgang="stand-%d" % i)
        verb.close()

    def tearDown(self):
        shutil.rmtree(self.ordner, ignore_errors=True)

    def test_export_bleibt_in_sich_stimmig(self):
        import io as _io
        import zipfile
        from hallenbuch import datenbank, export, rechnung

        verb = datenbank.verbinden(self.db)
        echt = export._zeilen
        dazwischen = []

        def stoerend(v, tabelle, geheim=()):
            ergebnis = echt(v, tabelle, geheim)
            if tabelle == "rechnung" and not dazwischen:
                # Genau zwischen Rechnung und Position: jemand rechnet ab.
                zweite = datenbank.verbinden(self.db)
                try:
                    buero = zweite.execute(
                        "SELECT * FROM benutzer WHERE id=1").fetchone()
                    rechnung.erzeugen(zweite, buero, 1, "2026-09-14", "2026-09-21")
                finally:
                    zweite.close()
                dazwischen.append(True)
            return ergebnis

        ziel = os.path.join(self.ordner, "export.zip")
        export._zeilen = stoerend
        try:
            export.schreiben(verb, ziel, os.path.join(self.ordner, "fotos"))
        finally:
            export._zeilen = echt
            verb.close()

        self.assertTrue(dazwischen, "Die Störung hat gar nicht stattgefunden")
        zip_datei = zipfile.ZipFile(ziel)

        def spalten(name):
            roh = zip_datei.read(name).decode("utf-8-sig").splitlines()
            kopf = roh[0].split(";")
            return kopf, [dict(zip(kopf, z.split(";"))) for z in roh[1:] if z.strip()]

        _, rechnungen = spalten("rechnung.csv")
        _, positionen = spalten("rechnungsposition.csv")
        bekannt = {r["id"] for r in rechnungen}
        verwaist = [p for p in positionen if p["rechnung_id"] not in bekannt]
        self.assertEqual(
            verwaist, [],
            "Im Export stehen Positionen, deren Rechnung fehlt — der Export ist "
            "in sich widersprüchlich")


class GutschriftFaelltAus(unittest.TestCase):
    """Bricht der Aufruf bei einer Gutschrift ab, hinterliess das früher **keine
    Spur**: Die Zeile wurde gelöscht, nichts protokolliert. Eine Gutschrift, die
    drüben vielleicht doch entstanden ist, war damit unsichtbar — und der
    nächste Klick hätte dem Autohaus zweimal Geld gutgeschrieben."""

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1, tag="2026-09-18")
        r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-20")
        rechnung.freigeben(self.verb, self.buero, r["id"])
        rechnung.finalisieren(self.verb, self.buero, r["id"], lexware.Attrappe())
        self.rechnung_id = r["id"]
        self.fahrzeug_id = self.verb.execute(
            "SELECT fahrzeug_id FROM rechnungsposition WHERE rechnung_id=?",
            (r["id"],)).fetchone()[0]

    def _streik(self, sicher):
        class Streik:
            bereit = True

            def gutschrift_ausstellen(self, zeile, grund):
                raise lexware.LexwareFehler("HTTP 503: Wartungsarbeiten", sicher)
        return Streik()

    def _letzter_eintrag(self):
        return self.verb.execute(
            "SELECT * FROM protokoll ORDER BY id DESC LIMIT 1").fetchone()

    def test_der_fehlversuch_steht_im_protokoll(self):
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.gutschrift(self.verb, self.buero, self.fahrzeug_id, "Kratzer",
                                self._streik(True))
        eintrag = self._letzter_eintrag()
        self.assertEqual(eintrag["was"], "Gutschrift fehlgeschlagen")
        self.assertIn("Wartungsarbeiten", eintrag["einzelheiten"])

    def test_sicher_nichts_angelegt_erlaubt_den_zweiten_versuch(self):
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.gutschrift(self.verb, self.buero, self.fahrzeug_id, "Kratzer",
                                self._streik(True))
        self.assertEqual(self.verb.execute(
            "SELECT COUNT(*) FROM gutschrift").fetchone()[0], 0)
        # Zweiter Versuch geht durch.
        rechnung.gutschrift(self.verb, self.buero, self.fahrzeug_id, "Kratzer",
                            lexware.Attrappe())
        self.assertEqual(self.verb.execute(
            "SELECT COUNT(*) FROM gutschrift WHERE lexware_id <> ''").fetchone()[0], 1)

    def test_unklare_lage_blockiert_den_blinden_zweiten_versuch(self):
        """Der gefährliche Fall: Drüben ist vielleicht schon eine Gutschrift.
        Ein zweiter Klick wäre die zweite — Geld zweimal weg."""
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.gutschrift(self.verb, self.buero, self.fahrzeug_id, "Kratzer",
                                self._streik(False))
        zeile = self.verb.execute("SELECT * FROM gutschrift").fetchone()
        self.assertIsNotNone(zeile, "die Zeile bleibt als Sperre stehen")
        self.assertEqual(zeile["lexware_id"], "")
        self.assertEqual(zeile["fehler_unklar"], 1)
        self.assertIn("Wartungsarbeiten", zeile["letzter_fehler"])
        self.assertIn("in Lexware nachsehen", self._letzter_eintrag()["einzelheiten"])

        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.gutschrift(self.verb, self.buero, self.fahrzeug_id, "Kratzer",
                                lexware.Attrappe())
        self.assertIn("schon eine Gutschrift", str(fall.exception))

    def test_die_rechnungsseite_zeigt_die_offene_gutschrift(self):
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.gutschrift(self.verb, self.buero, self.fahrzeug_id, "Kratzer",
                                self._streik(False))
        kopf, posten = rechnung.mit_positionen(self.verb, self.rechnung_id)
        betroffen = [p for p in posten if p["fahrzeug_id"] == self.fahrzeug_id][0]
        self.assertEqual(betroffen["gutgeschrieben"], 0, "sie ist nicht fertig")
        self.assertEqual(betroffen["gutschrift_offen"], 1)
        seite = seiten.rechnung_seite(self.buero, kopf, posten, probebetrieb=True)
        self.assertIn("Gutschrift offen", seite)
        self.assertIn("in Lexware nachsehen", seite)
        # Der Knopf verschwindet NUR in der betroffenen Zeile; das zweite
        # Fahrzeug derselben Rechnung behaelt ihn.
        zeilen = seite.split("<tr>")
        betroffen_html = [z for z in zeilen if "Gutschrift offen" in z]
        self.assertEqual(len(betroffen_html), 1)
        self.assertNotIn("<form", betroffen_html[0])
        self.assertEqual(sum("<form" in z for z in zeilen), 1,
                         "die andere Zeile behaelt ihren Knopf")


class AbgestuerzterVersuch(unittest.TestCase):
    """Der Anspruch (`ausstellung_seit`) verhindert, dass zwei Klicks zwei
    Rechnungen bei Lexware erzeugen. Was passiert, wenn der Vorgang **mitten im
    Aufruf** stirbt, hat die Suite bis zum 23.09. nicht geprüft — in keinem
    Test kam das Feld überhaupt vor.

    Daran hängen zwei verschiedene Gefahren, die beide Geld kosten."""

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)
        hilfe.erfassen_viele(self.verb, self.halle, 3, autohaus_id=1)
        self.r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        rechnung.freigeben(self.verb, self.buero, self.r["id"])

    def _anspruch_setzen(self, wann):
        self.verb.execute("UPDATE rechnung SET ausstellung_seit=? WHERE id=?",
                          (wann, self.r["id"]))

    def _stand(self):
        return self.verb.execute("SELECT * FROM rechnung WHERE id=?",
                                 (self.r["id"],)).fetchone()

    def _streik(self, sicher, text="HTTP 400: Anschrift unvollständig"):
        fehler = lexware.LexwareFehler(text, sicher)

        class Streik:
            bereit = True

            def rechnung_ausstellen(self, kopf, posten):
                raise fehler
        return Streik()

    def test_ein_frischer_anspruch_sperrt(self):
        """Die Gegenrichtung: Solange wirklich jemand ausstellt, bleibt zu."""
        self._anspruch_setzen(datenbank.jetzt())
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.finalisieren(self.verb, self.buero, self.r["id"], lexware.Attrappe())
        self.assertIn("wird gerade ausgestellt", str(fall.exception))

    def test_ein_alter_anspruch_sperrt_nicht_fuer_immer(self):
        """Stirbt der Vorgang mitten im Aufruf, bleibt die Marke stehen. Ohne
        Verfall wäre die Rechnung dauerhaft unausstellbar — und die Meldung
        („bitte einen Moment warten") würde für immer lügen."""
        self._anspruch_setzen("2026-09-21T10:00:00+02:00")
        ergebnis = rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                         lexware.Attrappe())
        self.assertEqual(ergebnis["status"], "finalisiert")

    def test_nach_einem_abbruch_gilt_die_lage_als_unklar(self):
        """Der zweite Versuch weiß nicht, ob der erste drüben schon einen Beleg
        erzeugt hat. Das muss am Beleg stehen, bevor er losläuft."""
        self._anspruch_setzen("2026-09-21T10:00:00+02:00")

        gesehen = {}

        class Mitleser:
            bereit = True

            def rechnung_ausstellen(zelf, kopf, posten):
                zeile = self.verb.execute("SELECT * FROM rechnung WHERE id=?",
                                          (self.r["id"],)).fetchone()
                gesehen["unklar"] = zeile["fehler_unklar"]
                gesehen["grund"] = zeile["letzter_fehler"]
                return lexware.Attrappe().rechnung_ausstellen(kopf, posten)

        rechnung.finalisieren(self.verb, self.buero, self.r["id"], Mitleser())
        self.assertEqual(gesehen["unklar"], 1)
        self.assertIn("abgebrochen", gesehen["grund"])
        # Gelingt der zweite Versuch, ist der Zweifel beantwortet.
        self.assertEqual(self._stand()["fehler_unklar"], 0)

    def test_ein_sauberes_nein_loescht_den_zweifel_des_ersten_versuchs_nicht(self):
        """Der teure Fall. Erster Versuch stirbt mitten im Aufruf (drüben liegt
        vielleicht ein Beleg). Zweiter Versuch wird von Lexware sauber
        abgelehnt — „sicher nichts angelegt" gilt aber nur für DIESEN Versuch.
        Vorher wurde `fehler_unklar` dabei auf 0 zurückgesetzt: Das Büro las
        „gefahrlos wiederholen" und hätte dem Autohaus dieselbe Leistung ein
        zweites Mal berechnet."""
        self._anspruch_setzen("2026-09-21T10:00:00+02:00")
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                  self._streik(True))
        self.assertEqual(self._stand()["fehler_unklar"], 1,
                         "der Zweifel aus dem Abbruch bleibt stehen")

    def test_auch_ueber_zwei_aufrufe_hinweg_bleibt_der_zweifel(self):
        """Derselbe Fehler ohne alten Anspruch: Versuch 1 bricht unklar ab (die
        Marke wird dabei freigegeben), Versuch 2 wird sauber abgelehnt."""
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                  self._streik(False, "Zeitüberschreitung"))
        self.assertEqual(self._stand()["fehler_unklar"], 1)
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                  self._streik(True))
        self.assertEqual(self._stand()["fehler_unklar"], 1,
                         "ein spaeteres sauberes Nein beweist nichts ueber den ersten Versuch")

    def test_ohne_vorgeschichte_bleibt_ein_sauberes_nein_sauber(self):
        """Die Gegenrichtung: Ohne jeden Zweifel vorher darf ein 4xx auch als
        gefahrlos gelten, sonst wäre die Unterscheidung wertlos."""
        with self.assertRaises(lexware.LexwareFehler):
            rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                  self._streik(True))
        self.assertEqual(self._stand()["fehler_unklar"], 0)


class ZweiSendungenGleichzeitig(unittest.TestCase):
    """Die Warteschlange im Handy sendet erneut, waehrend die erste Sendung noch
    unterwegs ist — nach einem Funkloch der Normalfall. Beide kommen durch die
    Vorprüfung („kenne ich den Vorgang schon?"), denn zu dem Zeitpunkt kennt ihn
    keiner. Eine der beiden gewinnt das INSERT, die andere laeuft in den
    eindeutigen Schluessel.

    Die Vorprüfung deckt nur den ruhigen Fall ab; dieser hier ist der, bei dem
    ein Fahrzeug doppelt auf der Rechnung landen wuerde."""

    def setUp(self):
        from hallenbuch import datenbank, fahrzeuge
        self.ordner = tempfile.mkdtemp(prefix="hallenbuch-doppelsendung-")
        self.db = os.path.join(self.ordner, "doppelt.db")
        verb = datenbank.verbinden(self.db)
        datenbank.aufbauen(verb)
        verb.execute("INSERT INTO benutzer (id,name,anmeldename,passwort,rolle,angelegt_am)"
                     " VALUES (2,'Halle','halle','x','erfasser',?)", (datenbank.jetzt(),))
        verb.execute("INSERT INTO autohaus (id,name,kurz,strasse,plz,ort)"
                     " VALUES (1,'Nord','NORD','Industriestr. 12','38228','Salzgitter')")
        verb.execute("INSERT INTO leistung (id,bezeichnung,netto_cent)"
                     " VALUES (1,'Vollaufbereitung',14900)")
        verb.close()

    def tearDown(self):
        shutil.rmtree(self.ordner, ignore_errors=True)

    def _blind(self, verb):
        """Eine Verbindung, deren Vorprüfung den Vorgang NICHT sieht — genau die
        Lage zweier gleichzeitiger Sendungen, bevor eine von beiden geschrieben
        hat. Nur die eine Abfrage wird geblendet, alles andere bleibt echt."""
        echt = verb.execute

        class Blind:
            def __init__(zelf):
                zelf.geblendet = 0

            def execute(zelf, sql, *rest):
                if sql.startswith("SELECT * FROM fahrzeug WHERE vorgang=?") and not zelf.geblendet:
                    zelf.geblendet = 1
                    return echt("SELECT * FROM fahrzeug WHERE 0", ())
                return echt(sql, *rest)

            def __getattr__(zelf, name):
                return getattr(verb, name)
        return Blind()

    def test_die_verliererin_bekommt_das_vorhandene_fahrzeug(self):
        """Der harte Fall: Beide Sendungen sind durch die Vorprüfung, eine hat
        geschrieben. Die andere laeuft in den eindeutigen Schluessel und muss
        daraus „kenne ich schon" machen — nicht einen Fehler, den das Handy als
        „nicht angekommen" liest und ein drittes Mal sendet."""
        from hallenbuch import datenbank, fahrzeuge
        verb = datenbank.verbinden(self.db)
        try:
            halle = verb.execute("SELECT * FROM benutzer WHERE id=2").fetchone()
            erste, _ = fahrzeuge.erfassen(verb, halle, hilfe.fin_nummer(1), 1, 1,
                                          "2026-09-18", vorgang="funkloch-1")
            zweite, hinweise = fahrzeuge.erfassen(
                self._blind(verb), halle, hilfe.fin_nummer(2), 1, 1,
                "2026-09-19", vorgang="funkloch-1")
            self.assertEqual(zweite["id"], erste["id"])
            self.assertTrue(any("schon erfasst" in h for h in hinweise), hinweise)
            self.assertEqual(verb.execute(
                "SELECT COUNT(*) FROM fahrzeug").fetchone()[0], 1)
        finally:
            verb.close()

    def test_aus_zwei_gleichzeitigen_sendungen_wird_ein_fahrzeug(self):
        from hallenbuch import datenbank, fahrzeuge
        los = threading.Barrier(2, timeout=10)
        ergebnisse = []

        def lauf():
            verb = datenbank.verbinden(self.db)
            try:
                halle = verb.execute("SELECT * FROM benutzer WHERE id=2").fetchone()
                los.wait()
                zeile, _ = fahrzeuge.erfassen(verb, halle, hilfe.fin_nummer(1), 1, 1,
                                              "2026-09-18", vorgang="funkloch-1")
                ergebnisse.append(zeile["id"])
            except Exception as fehler:
                ergebnisse.append(fehler)
            finally:
                verb.close()

        faeden = [threading.Thread(target=lauf) for _ in range(2)]
        for f in faeden:
            f.start()
        for f in faeden:
            f.join(timeout=15)

        self.assertEqual(len(ergebnisse), 2)
        fehler = [e for e in ergebnisse if isinstance(e, Exception)]
        self.assertEqual(fehler, [], "keine der beiden Sendungen darf scheitern")
        self.assertEqual(ergebnisse[0], ergebnisse[1], "beide meinen dasselbe Fahrzeug")

        verb = datenbank.verbinden(self.db)
        try:
            self.assertEqual(verb.execute(
                "SELECT COUNT(*) FROM fahrzeug").fetchone()[0], 1)
        finally:
            verb.close()


class LangeFehlermeldungenWerdenNichtZerschnitten(unittest.TestCase):
    """Am 23.09. im echten Lauf: Lexware antwortete mit einer Liste fehlender
    Firmenangaben, und unsere eigene Kürzung auf 400 Zeichen schnitt genau die
    Liste ab. Übrig blieb „Es fehlen wichtige Firmenangaben für die Erstellung
    ein" — ein Satz, der mitten im Wort endet und nicht sagt, was fehlt."""

    def test_die_meldung_bleibt_lesbar(self):
        import urllib.error
        from hallenbuch import lexware as lx

        lang = ("Validation failed: [organizationBankData: Es fehlen "
                "Bankinformationen für die Erstellung einer XRechnung., "
                "organizationData: " + "x" * 500 + ", ENDE_DER_LISTE]")

        class Antwort(io.BytesIO):
            def read(self, *rest):
                return lang.encode("utf-8")

        dienst = lx.Lexware("schluessel")
        echtes_oeffnen = lx.urllib.request.urlopen

        def wirft(*rest, **mehr):
            raise urllib.error.HTTPError("u", 406, "Not Acceptable", {}, Antwort())

        lx.urllib.request.urlopen = wirft
        try:
            with self.assertRaises(lx.LexwareFehler) as fall:
                dienst._rufen("/v1/invoices", {}, "POST", versuche=1)
        finally:
            lx.urllib.request.urlopen = echtes_oeffnen
        self.assertIn("Bankinformationen", str(fall.exception))
        self.assertIn("ENDE_DER_LISTE", str(fall.exception),
                      "das Ende der Liste ist abgeschnitten worden")

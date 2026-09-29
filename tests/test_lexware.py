"""Der Beleg, den wir an das Rechnungsprogramm schicken, wird ohne Netz geprueft."""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hallenbuch import fahrzeuge, lexware, rechnung  # noqa: E402
from tests import hilfe  # noqa: E402


class Belegform(unittest.TestCase):

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)
        hilfe.erfassen_viele(self.verb, self.halle, 3, autohaus_id=1)
        r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        self.kopf, self.posten = rechnung.mit_positionen(self.verb, r["id"])

    def test_ohne_anschrift_geht_auch_keine_gutschrift_raus(self):
        """Für eine Rechnungskorrektur gelten dieselben Pflichtangaben. Die
        Anschrift kann zwischen Ausstellen und Gutschrift verschwinden — im
        Stammdatenformular sind Strasse, PLZ und Ort nicht als Pflicht
        markiert, damit sich ein falscher Name allein korrigieren lässt."""
        rechnung.freigeben(self.verb, self.buero, self.kopf["id"])
        rechnung.finalisieren(self.verb, self.buero, self.kopf["id"], lexware.Attrappe())
        fahrzeug_id = self.posten[0]["fahrzeug_id"]

        self.verb.execute("UPDATE autohaus SET strasse='', plz='', ort='', lexware_kontakt='' WHERE id=1")
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.gutschrift(self.verb, self.buero, fahrzeug_id, "Reklamation",
                                lexware.Attrappe())
        self.assertIn("fehlt die Anschrift", str(fall.exception))
        # Und es wurde auch keine Zeile angelegt.
        self.assertEqual(self.verb.execute(
            "SELECT COUNT(*) FROM gutschrift").fetchone()[0], 0)

    def test_ohne_anschrift_geht_keine_rechnung_raus(self):
        """§ 14 Abs. 4 Nr. 1 UStG verlangt die Anschrift des Empfängers. Ein
        Autohaus liess sich mit Name und Kürzel allein anlegen und danach ganz
        normal abrechnen — der Beleg wäre formal defekt gewesen."""
        self.verb.execute("UPDATE autohaus SET strasse='', plz='', ort='', lexware_kontakt='' WHERE id=1")
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.freigeben(self.verb, self.buero, self.kopf["id"])
        self.assertIn("fehlt die Anschrift", str(fall.exception))
        self.assertIn("Strasse, PLZ, Ort", str(fall.exception))

    def test_ein_hinterlegter_kontakt_ersetzt_die_anschrift(self):
        """Steht der Empfänger schon im Rechnungsprogramm, liegt die Anschrift
        dort. Dann darf das Hallenbuch sie nicht noch einmal verlangen."""
        self.verb.execute("UPDATE autohaus SET strasse='', plz='', ort='',"
                          " lexware_kontakt='abc-123' WHERE id=1")
        kopf, _ = rechnung.mit_positionen(self.verb, self.kopf["id"])
        rechnung.anschrift_pruefen(kopf)      # wirft nicht

    def test_auch_beim_ausstellen_wird_die_anschrift_verlangt(self):
        rechnung.freigeben(self.verb, self.buero, self.kopf["id"])
        self.verb.execute("UPDATE autohaus SET strasse='', plz='', ort='', lexware_kontakt='' WHERE id=1")
        with self.assertRaises(rechnung.Abgelehnt):
            rechnung.finalisieren(self.verb, self.buero, self.kopf["id"], lexware.Attrappe())

    def test_rechnungsdatum_ist_der_ausstellungstag_nicht_das_periodenende(self):
        """§ 14 Abs. 4 Nr. 3 UStG will das Ausstellungsdatum. Vorher stand hier
        das Ende des Leistungszeitraums: jede Rechnung war rückdatiert, und über
        einen Monatswechsel landete sie im falschen Voranmeldungszeitraum."""
        from datetime import date
        k = lexware.rechnungskoerper(self.kopf, self.posten)
        self.assertTrue(k["voucherDate"].startswith(date.today().isoformat()),
                        k["voucherDate"])
        self.assertFalse(k["voucherDate"].startswith(str(self.kopf["bis"])[:10]))
        # Der Leistungszeitraum steht weiterhin drin, nur woanders.
        self.assertEqual(k["shippingConditions"]["shippingType"], "serviceperiod")
        self.assertTrue(k["shippingConditions"]["shippingDate"]
                        .startswith(str(self.kopf["von"])[:10]))

    def test_zahlungsziel_wird_gesetzt_und_nicht_nur_behauptet(self):
        """Am echten Testkonto gemessen: die Voreinstellung dort ist „Zahlbar
        sofort, rein netto" mit null Tagen. Stünde das Ziel nur im Bemerkungs-
        feld, verspräche der Beleg 14 Tage und mahnte nach null."""
        k = lexware.rechnungskoerper(self.kopf, self.posten)
        self.assertEqual(k["paymentConditions"]["paymentTermDuration"],
                         int(self.kopf["zahlungsziel"]))
        self.assertIn("%d Tagen" % int(self.kopf["zahlungsziel"]),
                      k["paymentConditions"]["paymentTermLabel"])
        self.assertNotIn("Tagen", k.get("remark", ""))

    def test_kleinunternehmer_wird_steuerfrei_gestellt(self):
        self.assertEqual(lexware._steuer(0)["taxType"], "vatfree")
        self.assertIn("§ 19", lexware._steuer(0)["taxTypeNote"])
        self.assertEqual(lexware._steuer(19), {"taxType": "net"})

    def test_mandant_und_beleg_muessen_beim_steuersatz_zusammenpassen(self):
        """Ist der Mandant als Kleinunternehmer eingerichtet und der Beleg weist
        19 % aus, ist die Rechnung um 19 % falsch. Das darf nicht rausgehen."""
        gerufen = []

        class Klein(lexware.Lexware):
            def _rufen(self, weg, koerper=None, methode="GET", versuche=3):
                gerufen.append((methode, weg))
                if weg == "/v1/profile":
                    return {"smallBusiness": True, "taxType": "net"}
                return {"id": "x", "resourceUri": "", "voucherNumber": "RE-1"}

        dienst = Klein("test-schluessel")
        with self.assertRaises(lexware.LexwareFehler) as fall:
            dienst.rechnung_ausstellen(self.kopf, self.posten)
        self.assertTrue(fall.exception.sicher_nicht_angelegt)
        self.assertIn("Kleinunternehmer", str(fall.exception))
        # Entscheidend: Es wurde NICHTS angelegt.
        self.assertNotIn(("POST", "/v1/invoices?finalize=true"), gerufen)

    def test_umgekehrt_ebenso_kein_beleg_ohne_umsatzsteuer(self):
        class Gross(lexware.Lexware):
            def _rufen(self, weg, koerper=None, methode="GET", versuche=3):
                if weg == "/v1/profile":
                    return {"smallBusiness": False, "taxType": "net"}
                return {"id": "x", "resourceUri": "", "voucherNumber": "RE-1"}

        self.verb.execute("UPDATE rechnung SET steuersatz=0 WHERE id=?", (self.kopf["id"],))
        kopf, posten = rechnung.mit_positionen(self.verb, self.kopf["id"])
        with self.assertRaises(lexware.LexwareFehler) as fall:
            Gross("test-schluessel").rechnung_ausstellen(kopf, posten)
        self.assertIn("umsatzsteuerpflichtig", str(fall.exception))

    def test_koerper_hat_die_von_lexware_erwarteten_felder(self):
        k = lexware.rechnungskoerper(self.kopf, self.posten)
        self.assertEqual(k["taxConditions"], {"taxType": "net"})
        self.assertEqual(k["totalPrice"], {"currency": "EUR"})
        self.assertEqual(len(k["lineItems"]), 3)
        erste = k["lineItems"][0]
        self.assertEqual(erste["type"], "custom")
        self.assertEqual(erste["quantity"], 1)
        self.assertEqual(erste["unitPrice"]["currency"], "EUR")
        self.assertEqual(erste["unitPrice"]["netAmount"], 149.0)
        self.assertEqual(erste["unitPrice"]["taxRatePercentage"], 19)

    def test_sammelrechnung_traegt_einen_leistungszeitraum(self):
        k = lexware.rechnungskoerper(self.kopf, self.posten)
        self.assertEqual(k["shippingConditions"]["shippingType"], "serviceperiod")
        self.assertTrue(k["shippingConditions"]["shippingDate"].startswith("2026-09-14"))
        self.assertTrue(k["shippingConditions"]["shippingEndDate"].startswith("2026-09-21"))

    def test_kaeuferreferenz_landet_in_der_erechnung(self):
        k = lexware.rechnungskoerper(self.kopf, self.posten)
        self.assertEqual(k["xRechnung"]["buyerReference"], "04011000-1234512345-01")

    def test_ohne_kaeuferreferenz_kein_leeres_xrechnung_feld(self):
        self.verb.execute("UPDATE autohaus SET kaeuferreferenz='' WHERE id=1")
        kopf, posten = rechnung.mit_positionen(self.verb, self.kopf["id"])
        self.assertNotIn("xRechnung", lexware.rechnungskoerper(kopf, posten))

    def test_kleinunternehmer_bekommt_den_hinweis_statt_steuer(self):
        self.verb.execute("UPDATE rechnung SET steuersatz=0 WHERE id=?", (self.kopf["id"],))
        kopf, posten = rechnung.mit_positionen(self.verb, self.kopf["id"])
        k = lexware.rechnungskoerper(kopf, posten)
        self.assertIn("§ 19 UStG", k["taxConditions"]["taxTypeNote"])
        self.assertEqual(k["lineItems"][0]["unitPrice"]["taxRatePercentage"], 0)

    def test_betraege_werden_in_euro_nicht_in_cent_geschickt(self):
        self.verb.execute(
            "UPDATE rechnungsposition SET netto_cent=12345 WHERE rechnung_id=?",
            (self.kopf["id"],))
        kopf, posten = rechnung.mit_positionen(self.verb, self.kopf["id"])
        betraege = [p["unitPrice"]["netAmount"]
                    for p in lexware.rechnungskoerper(kopf, posten)["lineItems"]]
        self.assertEqual(betraege, [123.45, 123.45, 123.45])

    def test_preisaenderung_am_fahrzeug_aendert_die_rechnung_nicht_mehr(self):
        """Eine erzeugte Rechnung ist eingefroren. Sonst waere sie nicht nachvollziehbar."""
        self.verb.execute("UPDATE fahrzeug SET netto_cent=99900")
        self.verb.execute("UPDATE leistung SET netto_cent=99900 WHERE id=1")
        kopf, posten = rechnung.mit_positionen(self.verb, self.kopf["id"])
        self.assertEqual([p["netto_cent"] for p in posten], [14900, 14900, 14900])
        self.assertEqual(kopf["netto_cent"], 3 * 14900)

    def test_ohne_schluessel_stellt_nichts_aus(self):
        echt = lexware.Lexware(schluessel="")
        self.assertFalse(echt.bereit)
        with self.assertRaises(lexware.LexwareFehler) as fall:
            echt.rechnung_ausstellen(self.kopf, self.posten)
        self.assertIn("Probebetrieb", str(fall.exception))

    def test_attrappe_vergibt_verschiedene_belegnummern(self):
        attrappe = lexware.Attrappe()
        a = attrappe.rechnung_ausstellen(self.kopf, self.posten)
        b = attrappe.rechnung_ausstellen(self.kopf, self.posten)
        self.assertNotEqual(a["nummer"], b["nummer"])
        self.assertEqual(len(attrappe.belege), 2)


if __name__ == "__main__":
    unittest.main()


class DerEchteAufruf(unittest.TestCase):
    """Der Aufrufpfad selbst — bis zum 22.09. **nie** von einem Test berührt.
    Gemessen: Die Zeilen, die eine erfolgreiche Antwort lesen und auswerten,
    wurden von der ganzen Suite nicht ein einziges Mal ausgeführt. Getestet
    wurde immer nur gegen die Attrappe oder mit ersetztem `_rufen`.

    Hier wird stattdessen `urlopen` ersetzt: Damit läuft der echte Code, ohne
    dass eine Anfrage ins Netz geht.
    """

    def aufbauen(self, antworten, echt_warten=False):
        """antworten: eine je Versuch. Gewartet wird nur, wo es geprüft wird —
        sonst dauert die ganze Suite Minuten für nichts."""
        import urllib.request

        dienst = lexware.Lexware("test-schluessel")
        if not echt_warten:
            echtes_schlafen = lexware.time.sleep
            lexware.time.sleep = lambda _s: None
            self.addCleanup(setattr, lexware.time, "sleep", echtes_schlafen)
        versuche = {"n": 0}

        class Antwort:
            def __init__(self, koerper):
                self._koerper = koerper

            def read(self):
                return self._koerper

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def gestellt(anfrage, timeout=None):
            n = versuche["n"]
            versuche["n"] += 1
            was = antworten[min(n, len(antworten) - 1)]
            if isinstance(was, Exception):
                raise was
            return Antwort(was)

        self.echtes_urlopen = urllib.request.urlopen
        urllib.request.urlopen = gestellt
        self.addCleanup(setattr, urllib.request, "urlopen", self.echtes_urlopen)
        return dienst, versuche

    def test_eine_gute_antwort_wird_gelesen_und_ausgewertet(self):
        dienst, versuche = self.aufbauen([b'{"id": "abc", "voucherNumber": "RE-1"}'])
        self.assertEqual(dienst._rufen("/v1/profile"),
                         {"id": "abc", "voucherNumber": "RE-1"})
        self.assertEqual(versuche["n"], 1)

    def test_eine_leere_antwort_ist_ein_leeres_ergebnis(self):
        dienst, _ = self.aufbauen([b""])
        self.assertEqual(dienst._rufen("/v1/profile"), {})

    def test_ein_vierhunderter_heisst_nichts_angelegt(self):
        import urllib.error
        fehler = urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(b"kaputt"))
        dienst, versuche = self.aufbauen([fehler])
        with self.assertRaises(lexware.LexwareFehler) as fall:
            dienst._rufen("/v1/invoices", {}, "POST")
        self.assertTrue(fall.exception.sicher_nicht_angelegt)
        self.assertEqual(versuche["n"], 1, "ein 400 wird nicht wiederholt")

    def test_ein_fuenfhunderter_laesst_es_offen_und_wird_wiederholt(self):
        import urllib.error
        fehler = urllib.error.HTTPError("u", 500, "Serverfehler", {}, io.BytesIO(b"oh"))
        dienst, versuche = self.aufbauen([fehler])
        with self.assertRaises(lexware.LexwareFehler) as fall:
            dienst._rufen("/v1/invoices", {}, "POST")
        self.assertFalse(fall.exception.sicher_nicht_angelegt)
        self.assertEqual(versuche["n"], 3, "dreimal versucht")

    def test_nach_einem_bremser_klappt_der_zweite_versuch(self):
        import urllib.error
        dienst, versuche = self.aufbauen([
            urllib.error.HTTPError("u", 429, "Zu viel", {}, io.BytesIO(b"")),
            b'{"id": "spaeter"}'])
        self.assertEqual(dienst._rufen("/v1/invoices", {}, "POST"), {"id": "spaeter"})
        self.assertEqual(versuche["n"], 2)

    def test_kein_netz_laesst_es_offen(self):
        import urllib.error
        dienst, versuche = self.aufbauen([urllib.error.URLError("kein Netz")])
        with self.assertRaises(lexware.LexwareFehler) as fall:
            dienst._rufen("/v1/invoices", {}, "POST")
        self.assertFalse(fall.exception.sicher_nicht_angelegt)
        self.assertIn("Keine Verbindung", str(fall.exception))
        self.assertEqual(versuche["n"], 3)

    def test_eine_zeitueberschreitung_laesst_es_offen(self):
        dienst, _ = self.aufbauen([TimeoutError("zu lange")])
        with self.assertRaises(lexware.LexwareFehler) as fall:
            dienst._rufen("/v1/invoices", {}, "POST")
        self.assertFalse(fall.exception.sicher_nicht_angelegt)
        self.assertIn("Zeitüberschreitung", str(fall.exception))

    def test_ohne_schluessel_geht_nichts_hinaus(self):
        dienst = lexware.Lexware("")
        with self.assertRaises(lexware.LexwareFehler) as fall:
            dienst._rufen("/v1/profile")
        self.assertTrue(fall.exception.sicher_nicht_angelegt)

    def test_die_bremse_haelt_zwei_anfragen_je_sekunde_ein(self):
        import time as uhr
        dienst, _ = self.aufbauen([b'{}'], echt_warten=True)
        begonnen = uhr.time()
        for _ in range(3):
            dienst._rufen("/v1/profile")
        gedauert = uhr.time() - begonnen
        self.assertGreaterEqual(gedauert, 1.0,
                                "drei Anfragen brauchen mindestens eine Sekunde")


class Belegteile(unittest.TestCase):
    """Zwei Zweige im Belegaufbau, die kein Test je betreten hat."""

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)
        hilfe.erfassen_viele(self.verb, self.halle, 1, autohaus_id=1)
        r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        self.kopf, self.posten = rechnung.mit_positionen(self.verb, r["id"])

    def test_ein_hinterlegter_kontakt_ersetzt_die_adresse_im_beleg(self):
        self.verb.execute("UPDATE autohaus SET lexware_kontakt='k-1' WHERE id=1")
        kopf, posten = rechnung.mit_positionen(self.verb, self.kopf["id"])
        self.assertEqual(lexware.rechnungskoerper(kopf, posten)["address"],
                         {"contactId": "k-1"})

    def test_die_lieferantennummer_geht_in_die_erechnung(self):
        self.verb.execute("UPDATE autohaus SET lieferantennummer='L-4711' WHERE id=1")
        kopf, posten = rechnung.mit_positionen(self.verb, self.kopf["id"])
        self.assertEqual(
            lexware.rechnungskoerper(kopf, posten)["xRechnung"]["vendorNumberAtCustomer"],
            "L-4711")


class KaeuferreferenzBrauchtEinenKontakt(unittest.TestCase):
    """Am 23.09. im ersten echten Ausstelllauf gegen Lexware gefunden, nicht
    ausgedacht: Wird eine Käuferreferenz ohne `contactId` mitgeschickt,
    antwortet Lexware mit HTTP 406, „Referenced customer does not have
    XRechnung attributes set".

    Die Käuferreferenz ist BT-10 der EN 16931, also die Nummer, unter der das
    Autohaus die Rechnung in seiner Buchhaltung erwartet. Ohne sie bleibt die
    E-Rechnung dort liegen und wird nicht bezahlt."""

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1, tag="2026-09-18")
        self.r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-20")

    def test_ohne_kontakt_wird_die_freigabe_abgelehnt(self):
        self.verb.execute("UPDATE autohaus SET lexware_kontakt='' WHERE id=1")
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.freigeben(self.verb, self.buero, self.r["id"])
        self.assertIn("Käuferreferenz", str(fall.exception))
        self.assertIn("Lexware-Kontakt", str(fall.exception))

    def test_mit_hinterlegtem_kontakt_geht_es_durch(self):
        rechnung.freigeben(self.verb, self.buero, self.r["id"])
        fertig = rechnung.finalisieren(self.verb, self.buero, self.r["id"],
                                       lexware.Attrappe())
        self.assertEqual(fertig["status"], "finalisiert")

    def test_ohne_kaeuferreferenz_geht_es_auch_ohne_kontakt(self):
        self.verb.execute("UPDATE autohaus SET kaeuferreferenz='', lexware_kontakt=''"
                          " WHERE id=1")
        rechnung.freigeben(self.verb, self.buero, self.r["id"])
        kopf, _ = rechnung.mit_positionen(self.verb, self.r["id"])
        self.assertEqual(kopf["status"], "freigegeben")

    def test_die_regel_steht_auch_am_ausstellen(self):
        """Nicht nur an der Freigabe: Die Käuferreferenz kann danach noch
        eingetragen werden."""
        self.verb.execute("UPDATE autohaus SET kaeuferreferenz='', lexware_kontakt=''"
                          " WHERE id=1")
        rechnung.freigeben(self.verb, self.buero, self.r["id"])
        self.verb.execute("UPDATE autohaus SET kaeuferreferenz='04011000-1-99' WHERE id=1")
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.finalisieren(self.verb, self.buero, self.r["id"], lexware.Attrappe())
        self.assertIn("Käuferreferenz", str(fall.exception))

    def test_und_an_der_gutschrift(self):
        rechnung.freigeben(self.verb, self.buero, self.r["id"])
        rechnung.finalisieren(self.verb, self.buero, self.r["id"], lexware.Attrappe())
        self.verb.execute("UPDATE autohaus SET lexware_kontakt='' WHERE id=1")
        fahrzeug_id = self.verb.execute(
            "SELECT fahrzeug_id FROM rechnungsposition WHERE rechnung_id=?",
            (self.r["id"],)).fetchone()[0]
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.gutschrift(self.verb, self.buero, fahrzeug_id, "Kratzer",
                                lexware.Attrappe())
        self.assertIn("Käuferreferenz", str(fall.exception))


class KeinDoppelterTextInDenPositionen(unittest.TestCase):
    """Am 23.09. am zurückgeholten echten Beleg gesehen: Jede Position trug die
    FIN und das Datum zweimal, einmal als Überschrift und einmal in der
    Beschreibung. Bei 95 Fahrzeugen sind das 95 doppelte Zeilen auf einem
    Beleg, den ein fremdes Autohaus durchsieht."""

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)
        fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(1), 1, 1,
                           "2026-09-18", kennzeichen="SZ-AB 100", vorgang="doppel-1")
        self.r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-20")
        self.kopf, self.posten = rechnung.mit_positionen(self.verb, self.r["id"])

    def test_die_fin_steht_genau_einmal_in_der_position(self):
        zeile = lexware.rechnungskoerper(self.kopf, self.posten)["lineItems"][0]
        fin = self.posten[0]["fin"]
        self.assertEqual(zeile["name"], fin)
        self.assertNotIn(fin, zeile["description"])

    def test_das_uebrige_bleibt_aber_stehen(self):
        """Nicht kuerzen um des Kuerzens willen: Datum, Leistung und
        Kennzeichen muessen auf dem Beleg bleiben."""
        zeile = lexware.rechnungskoerper(self.kopf, self.posten)["lineItems"][0]
        self.assertIn("2026-09-18", zeile["description"])
        self.assertIn("Vollaufbereitung", zeile["description"])
        self.assertIn("SZ-AB 100", zeile["description"])

    def test_auch_auf_der_gutschrift(self):
        rechnung.freigeben(self.verb, self.buero, self.r["id"])
        rechnung.finalisieren(self.verb, self.buero, self.r["id"], lexware.Attrappe())
        zeile = self.verb.execute(
            "SELECT f.*, p.netto_cent AS positionsbetrag, p.text AS positionstext,"
            " r.steuersatz, r.lexware_nummer, a.name AS autohaus, a.strasse, a.plz,"
            " a.ort, a.land, a.kaeuferreferenz, a.lieferantennummer, a.lexware_kontakt"
            " FROM fahrzeug f JOIN rechnungsposition p ON p.fahrzeug_id=f.id"
            " JOIN rechnung r ON r.id=p.rechnung_id"
            " JOIN autohaus a ON a.id=r.autohaus_id WHERE f.id=?",
            (self.posten[0]["fahrzeug_id"],)).fetchone()
        position = lexware.gutschriftskoerper(zeile, "Kratzer")["lineItems"][0]
        self.assertIn(zeile["fin"], position["name"])
        self.assertNotIn(zeile["fin"], position["description"])
        self.assertIn("Grund: Kratzer", position["description"])

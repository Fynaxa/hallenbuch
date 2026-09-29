"""Stammdaten ändern. Bis zum 22.09. ging das gar nicht: Im ganzen Code stand
kein `UPDATE autohaus`, und die Filial-Abrechnung war zwar gebaut und
dokumentiert, aber über die Oberfläche nicht einstellbar."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re  # noqa: E402

from hallenbuch import fahrzeuge, rechnung, seiten, stammdaten  # noqa: E402
from tests import hilfe  # noqa: E402


class Autohaeuser(unittest.TestCase):

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)

    def haus(self, kennung=1):
        return self.verb.execute("SELECT * FROM autohaus WHERE id=?", (kennung,)).fetchone()

    def felder(self, **mehr):
        h = self.haus(mehr.pop("kennung", 1))
        grund = {"name": h["name"], "kurz": h["kurz"], "strasse": h["strasse"],
                 "plz": h["plz"], "ort": h["ort"], "kaeuferreferenz": h["kaeuferreferenz"],
                 "zahlungsziel": h["zahlungsziel"], "aktiv": "on"}
        grund.update(mehr)
        return grund

    def test_eine_falsche_anschrift_laesst_sich_korrigieren(self):
        """Der Fall, um den es geht: Die Anschrift steht auf jeder Rechnung an
        dieses Haus, und § 14 Abs. 4 UStG verlangt dort die richtige."""
        stammdaten.autohaus_aendern(self.verb, self.buero, 1, self.felder(
            strasse="Industriestr. 12a", plz="38228", ort="Salzgitter"))
        self.assertEqual(self.haus()["strasse"], "Industriestr. 12a")
        self.assertEqual(self.haus()["plz"], "38228")

    def test_jedes_feld_kommt_auch_wirklich_an(self):
        """Drei Felder gingen bis zum 23.09. durch keinen Test: die
        Käuferreferenz (auf der E-Rechnung die Leitweg-/Bestellnummer des
        Autohauses), die Lieferantennummer und der hinterlegte Lexware-Kontakt.
        Ein Tippfehler, der sie beim Speichern verschluckt, wäre unbemerkt
        geblieben — und dann geht die E-Rechnung im Wareneingang des Autohauses
        unter oder Lexware legt den Kunden ein zweites Mal an."""
        stammdaten.autohaus_aendern(self.verb, self.buero, 1, self.felder(
            name="Autohaus Nord GmbH", kurz="AHN",
            strasse="Hafenstr. 3", plz="38112", ort="Braunschweig",
            kaeuferreferenz="04011000-9999988888-33",
            lieferantennummer="LF-4711",
            lexware_kontakt="e9f1c0aa-1111-2222-3333-444455556666",
            zahlungsziel=21))
        h = self.haus()
        self.assertEqual(h["name"], "Autohaus Nord GmbH")
        self.assertEqual(h["kurz"], "AHN")
        self.assertEqual(h["strasse"], "Hafenstr. 3")
        self.assertEqual(h["plz"], "38112")
        self.assertEqual(h["ort"], "Braunschweig")
        self.assertEqual(h["kaeuferreferenz"], "04011000-9999988888-33")
        self.assertEqual(h["lieferantennummer"], "LF-4711")
        self.assertEqual(h["lexware_kontakt"], "e9f1c0aa-1111-2222-3333-444455556666")
        self.assertEqual(h["zahlungsziel"], 21)

    def test_und_das_protokoll_nennt_beide_staende(self):
        stammdaten.autohaus_aendern(self.verb, self.buero, 1, self.felder(
            lieferantennummer="LF-4711"))
        eintrag = self.verb.execute(
            "SELECT * FROM protokoll ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIn("lieferantennummer: leer -> LF-4711", eintrag["einzelheiten"])

    def test_die_filiale_laesst_sich_ueberhaupt_erst_einstellen(self):
        self.verb.execute("INSERT INTO autohaus (id,name,kurz) VALUES (9,'Süd','SUED')")
        stammdaten.autohaus_aendern(self.verb, self.buero, 9, self.felder(
            kennung=9, name="Süd", kurz="SUED",
            filiale_von="1", sammeln_mit_hauptsitz="on"))
        self.assertEqual(self.haus(9)["filiale_von"], 1)
        self.assertEqual(self.haus(9)["sammeln_mit_hauptsitz"], 1)
        # Und jetzt rechnet der Hauptsitz sie wirklich mit ab.
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=9, tag="2026-09-18")
        beleg = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-20")
        self.assertEqual(beleg["netto_cent"], 2 * 14900)

    def test_keine_filiale_von_sich_selbst(self):
        with self.assertRaises(stammdaten.Abgelehnt):
            stammdaten.autohaus_aendern(self.verb, self.buero, 1,
                                        self.felder(filiale_von="1"))

    def test_keine_kette_ueber_zwei_stufen(self):
        self.verb.execute("INSERT INTO autohaus (id,name,kurz,filiale_von)"
                          " VALUES (9,'Süd','SUED',1)")
        with self.assertRaises(stammdaten.Abgelehnt) as fall:
            stammdaten.autohaus_aendern(self.verb, self.buero, 2,
                                        self.felder(kennung=2, filiale_von="9"))
        self.assertIn("selbst eine Filiale", str(fall.exception))

    def test_stilllegen_mit_offenen_fahrzeugen_wird_abgelehnt(self):
        """Ein stillgelegtes Autohaus fällt aus jeder Übersicht heraus. Wären
        dort noch offene Fahrzeuge, wären sie unauffindbar und unabrechenbar."""
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1, tag="2026-09-18")
        felder = self.felder()
        del felder["aktiv"]
        with self.assertRaises(stammdaten.Abgelehnt) as fall:
            stammdaten.autohaus_aendern(self.verb, self.buero, 1, felder)
        self.assertIn("noch 2 Fahrzeuge offen", str(fall.exception))
        self.assertEqual(self.haus()["aktiv"], 1)

    def test_stilllegen_faellt_durch_wenn_zwischendurch_erfasst_wird(self):
        """Der Wächter steckt im Schreibbefehl. Hier wird mitten in der
        Funktion ein Fahrzeug erfasst — genau der Fall, den eine Vorabprüfung
        verpasst. Ein Fahrzeug bei einem stillgelegten Autohaus fällt aus jeder
        Übersicht und ist nicht mehr abzurechnen."""
        echt = stammdaten.offene_fahrzeuge
        dazwischen = []

        def gezaehlt(verb, autohaus_id):
            zahl = echt(verb, autohaus_id)
            if not dazwischen:      # nach dem Zählen erfasst die Halle
                dazwischen.append(fahrzeuge.erfassen(
                    self.verb, self.halle, hilfe.fin_nummer(77), 1, 1,
                    "2026-09-18", vorgang="dazwischen"))
            return zahl

        stammdaten.offene_fahrzeuge = gezaehlt
        felder = self.felder()
        del felder["aktiv"]
        try:
            with self.assertRaises(stammdaten.Abgelehnt) as fall:
                stammdaten.autohaus_aendern(self.verb, self.buero, 1, felder)
        finally:
            stammdaten.offene_fahrzeuge = echt
        self.assertIn("zwischenzeitlich", str(fall.exception))
        self.assertEqual(self.haus()["aktiv"], 1, "nichts wurde geändert")

    def test_hauptsitz_bleibt_offen_solange_die_filiale_fahrzeuge_hat(self):
        """Gemessen: Der Hauptsitz liess sich stilllegen, während bei seiner
        Filiale drei Fahrzeuge offen waren. Danach zeigte die Bürotafel gar
        nichts mehr davon — weder den Hauptsitz (stillgelegt) noch die Filiale
        (die wird beim Hauptsitz gezählt). 447 EUR standen nirgends."""
        self.verb.execute(
            "INSERT INTO autohaus (id,name,kurz,strasse,plz,ort,filiale_von,"
            "sammeln_mit_hauptsitz) VALUES (9,'Süd','SUED','W 1','38228','SZ',1,1)")
        hilfe.erfassen_viele(self.verb, self.halle, 3, autohaus_id=9, tag="2026-09-18")
        self.assertEqual(stammdaten.offene_fahrzeuge(self.verb, 1), 3)

        felder = self.felder()
        del felder["aktiv"]
        with self.assertRaises(stammdaten.Abgelehnt) as fall:
            stammdaten.autohaus_aendern(self.verb, self.buero, 1, felder)
        self.assertIn("3 Fahrzeuge offen", str(fall.exception))
        self.assertEqual(self.haus()["aktiv"], 1)
        # Und die Tafel zeigt sie weiterhin beim Hauptsitz.
        tafel = {z["id"]: z for z in rechnung.je_haus(self.verb, "2026-09-14", "2026-09-20")}
        self.assertEqual(tafel[1]["anzahl"], 3)

    def test_ohne_hauptsitz_erfasst_die_filiale_nichts(self):
        """Die Gegenrichtung: Ist der Hauptsitz stillgelegt, darf die Filiale
        nicht weiter erfassen — ihre Fahrzeuge stünden auf keiner Übersicht."""
        self.verb.execute(
            "INSERT INTO autohaus (id,name,kurz,strasse,plz,ort,filiale_von,"
            "sammeln_mit_hauptsitz) VALUES (9,'Süd','SUED','W 1','38228','SZ',1,1)")
        felder = self.felder()
        del felder["aktiv"]
        stammdaten.autohaus_aendern(self.verb, self.buero, 1, felder)   # geht, nichts offen
        self.assertEqual(self.haus()["aktiv"], 0)
        with self.assertRaises(fahrzeuge.Abgelehnt) as fall:
            fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(66), 9, 1,
                               "2026-09-18", vorgang="ohne-sitz")
        self.assertIn("Hauptsitz", str(fall.exception))

    def test_doppeltes_kuerzel_wird_abgelehnt(self):
        with self.assertRaises(stammdaten.Abgelehnt) as fall:
            stammdaten.autohaus_aendern(self.verb, self.buero, 2, self.felder(
                kennung=2, kurz=self.haus(1)["kurz"]))
        self.assertIn("Kürzel", str(fall.exception))

    def test_die_halle_darf_keine_stammdaten_aendern(self):
        with self.assertRaises(stammdaten.Abgelehnt):
            stammdaten.autohaus_aendern(self.verb, self.halle, 1, self.felder(name="X"))

    def test_die_aenderung_steht_im_protokoll(self):
        stammdaten.autohaus_aendern(self.verb, self.buero, 1, self.felder(ort="Lebenstedt"))
        eintrag = self.verb.execute(
            "SELECT * FROM protokoll ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(eintrag["was"], "Autohaus geändert")
        # Nicht nur WELCHES Feld, sondern von was auf was.
        self.assertIn("ort: Ort 1 -> Lebenstedt", eintrag["einzelheiten"])


class Leistungen(unittest.TestCase):

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)

    def test_der_preis_laesst_sich_aendern(self):
        stammdaten.leistung_aendern(self.verb, self.buero, 1, {
            "bezeichnung": "Vollaufbereitung", "preis": "159,90", "aktiv": "on"})
        self.assertEqual(self.verb.execute(
            "SELECT netto_cent FROM leistung WHERE id=1").fetchone()[0], 15990)

    def test_erfasste_fahrzeuge_behalten_ihren_preis(self):
        """Der Preis wird beim Erfassen eingefroren. Eine spätere Änderung darf
        keine alte Rechnung und kein erfasstes Fahrzeug anfassen."""
        zeile, _ = fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(1), 1, 1,
                                      "2026-09-18", vorgang="preis-1")
        stammdaten.leistung_aendern(self.verb, self.buero, 1, {
            "bezeichnung": "Vollaufbereitung", "preis": "999,00", "aktiv": "on"})
        self.assertEqual(self.verb.execute(
            "SELECT netto_cent FROM fahrzeug WHERE id=?", (zeile["id"],)).fetchone()[0],
            14900)

    def test_stillgelegte_leistung_verschwindet_aus_der_auswahl(self):
        stammdaten.leistung_aendern(self.verb, self.buero, 2, {
            "bezeichnung": "Innenreinigung", "preis": "89,00"})
        aktiv = [z["id"] for z in self.verb.execute(
            "SELECT id FROM leistung WHERE aktiv=1")]
        self.assertNotIn(2, aktiv)
        self.assertIn(1, aktiv)

    def test_die_preisaenderung_nennt_den_alten_preis(self):
        """„Preis geändert" beantwortet die Frage nicht, die später gestellt
        wird: von was auf was, und wer war das."""
        stammdaten.leistung_aendern(self.verb, self.buero, 1, {
            "bezeichnung": "Vollaufbereitung", "preis": "169,00", "aktiv": "on"})
        eintrag = self.verb.execute(
            "SELECT * FROM protokoll ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(eintrag["was"], "Leistung geändert")
        self.assertIn("Preis: 149,00 -> 169,00 EUR", eintrag["einzelheiten"])
        self.assertEqual(eintrag["benutzer_name"], "Achmed")

    def test_ein_krummer_preis_wird_abgelehnt(self):
        with self.assertRaises(ValueError):
            stammdaten.leistung_aendern(self.verb, self.buero, 1, {
                "bezeichnung": "X", "preis": "abc", "aktiv": "on"})


class Sonderpreise(unittest.TestCase):
    """Gesetzte Sonderpreise waren unsichtbar und nicht zu entfernen. Steigt
    später der Standardpreis, zahlt das Autohaus still weiter den alten."""

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)
        self.verb.execute("INSERT INTO preis (autohaus_id,leistung_id,netto_cent)"
                          " VALUES (1,1,12900)")

    def test_gesetzte_sonderpreise_sind_sichtbar(self):
        liste = stammdaten.sonderpreise(self.verb)
        self.assertEqual(len(liste), 1)
        self.assertEqual(liste[0]["netto_cent"], 12900)
        self.assertEqual(liste[0]["standard_cent"], 14900)
        self.assertEqual(liste[0]["autohaus"], "Autohaus 1")

    def test_ein_sonderpreis_laesst_sich_entfernen(self):
        stammdaten.sonderpreis_entfernen(self.verb, self.buero, 1, 1)
        self.assertEqual(stammdaten.sonderpreise(self.verb), [])
        # Und ab jetzt gilt wieder der Standardpreis.
        zeile, _ = fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(4), 1, 1,
                                      "2026-09-18", vorgang="sp-1")
        self.assertEqual(zeile["netto_cent"], 14900)

    def test_vorher_gilt_der_sonderpreis(self):
        zeile, _ = fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(5), 1, 1,
                                      "2026-09-18", vorgang="sp-2")
        self.assertEqual(zeile["netto_cent"], 12900)

    def test_ein_vergessener_sonderpreis_ueberlebt_die_preiserhoehung(self):
        """Genau der Grund, warum er sichtbar sein muss: Der Standardpreis
        steigt, der Sonderpreis bleibt, und niemand sieht es."""
        stammdaten.leistung_aendern(self.verb, self.buero, 1, {
            "bezeichnung": "Vollaufbereitung", "preis": "169,00", "aktiv": "on"})
        zeile, _ = fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(6), 1, 1,
                                      "2026-09-18", vorgang="sp-3")
        self.assertEqual(zeile["netto_cent"], 12900)
        liste = stammdaten.sonderpreise(self.verb)
        self.assertEqual(liste[0]["standard_cent"], 16900)   # sichtbar daneben

    def test_die_halle_darf_keinen_preis_entfernen(self):
        with self.assertRaises(stammdaten.Abgelehnt):
            stammdaten.sonderpreis_entfernen(self.verb, self.halle, 1, 1)

    def test_das_entfernen_steht_im_protokoll(self):
        stammdaten.sonderpreis_entfernen(self.verb, self.buero, 1, 1)
        eintrag = self.verb.execute(
            "SELECT * FROM protokoll ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(eintrag["was"], "Sonderpreis entfernt")
        self.assertIn("129,00", eintrag["einzelheiten"])


if __name__ == "__main__":
    unittest.main()


class ZweiImBueroGleichzeitig(unittest.TestCase):
    """Im Büro sitzen zwei: Achmed und seine Frau. Beide haben die Stammdaten
    offen. Sie erhöht den Preis, er korrigiert danach einen Tippfehler im
    Namen — sein Formular trägt aber noch den ALTEN Preis.

    Bis zum 23.09. schrieb sein Speichern den alten Preis zurück. Die Erhöhung
    war still weg, und jedes weitere Fahrzeug wurde zum alten Preis erfasst.
    Nichts auf dem Bildschirm sagte das; nur im Protokoll stand eine Zeile, die
    niemand liest."""

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)

    def _formular(self, kennung=1, art="leistung"):
        """Baut die Stammdatenseite und liest EIN Formular so aus, wie der
        Browser es zurückschicken würde."""
        haeuser = self.verb.execute("SELECT * FROM autohaus").fetchall()
        leistungen = self.verb.execute("SELECT * FROM leistung").fetchall()
        seite = seiten.verwaltung(self.buero, haeuser, leistungen, [], probebetrieb=True)
        stueck = seite.split('action="/verwaltung/%s/%d"' % (art, kennung))[1].split("</form>")[0]
        felder = dict(re.findall(r'name="(alt_[a-z_]+)" value="([^"]*)"', stueck))
        self.assertTrue(felder, "das Formular schickt keinen Stand mit")
        return felder

    def _leistung(self, kennung=1):
        return self.verb.execute("SELECT * FROM leistung WHERE id=?",
                                 (kennung,)).fetchone()

    def test_das_formular_traegt_den_stand_der_zeile(self):
        """Der Rundlauf muss passen: Was die Seite mitschickt, muss der
        Datenbank Wort für Wort entsprechen. Stimmt die Schreibweise nicht,
        wäre jede Speicherung gesperrt statt nur die gefährliche."""
        felder = self._formular()
        zeile = self._leistung()
        for spalte in stammdaten.STAND_LEISTUNG:
            self.assertEqual(felder["alt_" + spalte], str(zeile[spalte]), spalte)

    def test_ein_normales_speichern_geht_weiter_durch(self):
        felder = self._formular()
        felder.update(bezeichnung="Vollaufbereitung XL", preis="149,00",
                      sortierung="1", aktiv="on")
        stammdaten.leistung_aendern(self.verb, self.buero, 1, felder)
        self.assertEqual(self._leistung()["bezeichnung"], "Vollaufbereitung XL")

    def test_die_zweite_speicherung_ueberschreibt_die_erste_nicht(self):
        seins = self._formular()                      # er lädt die Seite
        ihres = self._formular()                      # sie auch

        ihres.update(bezeichnung="Vollaufbereitung", preis="159,00",
                     sortierung="1", aktiv="on")
        stammdaten.leistung_aendern(self.verb, self.buero, 1, ihres)
        self.assertEqual(self._leistung()["netto_cent"], 15900)

        seins.update(bezeichnung="Vollaufbereitung", preis="149,00",
                     sortierung="1", aktiv="on")
        with self.assertRaises(stammdaten.Abgelehnt) as fall:
            stammdaten.leistung_aendern(self.verb, self.buero, 1, seins)
        self.assertIn("inzwischen", str(fall.exception))
        self.assertEqual(self._leistung()["netto_cent"], 15900,
                         "die Preiserhöhung steht noch")

    def test_und_der_preis_wirkt_danach_auch_beim_erfassen(self):
        """Der eigentliche Schaden lag nicht in der Tabelle, sondern in jedem
        Fahrzeug, das danach erfasst wurde."""
        ihres = self._formular()
        ihres.update(bezeichnung="Vollaufbereitung", preis="159,00",
                     sortierung="1", aktiv="on")
        stammdaten.leistung_aendern(self.verb, self.buero, 1, ihres)
        zeile, _ = fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(1),
                                      1, 1, "2026-09-18", vorgang="preisstand-1")
        self.assertEqual(zeile["netto_cent"], 15900)

    def test_ohne_mitgeschickten_stand_bleibt_es_wie_bisher(self):
        """Aufrufe ohne Formular (Skript, Test) dürfen nicht gesperrt sein."""
        stammdaten.leistung_aendern(self.verb, self.buero, 1, {
            "bezeichnung": "Ohne Stand", "preis": "99,00",
            "sortierung": "1", "aktiv": "on"})
        self.assertEqual(self._leistung()["bezeichnung"], "Ohne Stand")

    def test_beim_autohaus_ebenso(self):
        seins = self._formular(1, "autohaus")
        ihres = self._formular(1, "autohaus")
        grund = {"name": "Autohaus 1", "kurz": "AH1", "strasse": "Musterweg 1",
                 "plz": "40001", "ort": "Ort 1", "zahlungsziel": "14", "aktiv": "on"}
        ihres.update(grund, ort="Salzgitter")
        stammdaten.autohaus_aendern(self.verb, self.buero, 1, ihres)
        seins.update(grund, name="Autohaus Eins")
        with self.assertRaises(stammdaten.Abgelehnt) as fall:
            stammdaten.autohaus_aendern(self.verb, self.buero, 1, seins)
        self.assertIn("inzwischen", str(fall.exception))
        haus = self.verb.execute("SELECT * FROM autohaus WHERE id=1").fetchone()
        self.assertEqual(haus["ort"], "Salzgitter", "ihre Anschrift steht noch")

    def test_die_beiden_gruende_werden_unterschieden(self):
        """Ein UPDATE, das nichts trifft, hat zwei mögliche Gründe. Das Büro
        muss wissen, welcher es war: neu laden oder erst abrechnen."""
        felder = self._formular(1, "autohaus")
        felder.update(name="Autohaus 1", kurz="AH1", strasse="Musterweg 1",
                      plz="40001", ort="Ort 1", zahlungsziel="14")   # ohne aktiv
        fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(2), 1, 1,
                           "2026-09-18", vorgang="grund-1")
        with self.assertRaises(stammdaten.Abgelehnt) as fall:
            stammdaten.autohaus_aendern(self.verb, self.buero, 1, felder)
        self.assertIn("Fahrzeuge offen", str(fall.exception))

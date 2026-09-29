"""Die Abnahmekriterien aus dem Lieferschnitt, als Test formuliert.

Jeder Test hier entspricht einem Satz, der Achmed gegenueber zugesagt ist.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hallenbuch import fahrzeuge, lexware, rechnung  # noqa: E402
from tests import hilfe  # noqa: E402


class Abrechnung(unittest.TestCase):

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)
        self.dienst = lexware.Attrappe()

    # --- Abnahme: mindestens 20 Fahrzeuge, je Autohaus eine Sammelrechnung ---

    def test_zwanzig_fahrzeuge_zwei_autohaeuser_zwei_rechnungen(self):
        hilfe.erfassen_viele(self.verb, self.halle, 12, autohaus_id=1, ab=0)
        hilfe.erfassen_viele(self.verb, self.halle, 8, autohaus_id=2, ab=100)

        eins = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        zwei = rechnung.erzeugen(self.verb, self.buero, 2, "2026-09-14", "2026-09-21")

        self.assertEqual(eins["netto_cent"], 12 * 14900)
        self.assertEqual(zwei["netto_cent"], 8 * 14900)
        _, posten_eins = rechnung.mit_positionen(self.verb, eins["id"])
        self.assertEqual(len(posten_eins), 12)
        self.assertEqual(
            self.verb.execute("SELECT COUNT(*) FROM fahrzeug WHERE status='offen'").fetchone()[0],
            0)

    # --- Abnahme: keine Rechnung doppelt erzeugt ---

    def test_zweite_rechnung_fuer_denselben_zeitraum_wird_abgelehnt(self):
        hilfe.erfassen_viele(self.verb, self.halle, 5, autohaus_id=1)
        rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        hilfe.erfassen_viele(self.verb, self.halle, 3, autohaus_id=1, ab=200)
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        self.assertIn("schon eine Rechnung", str(fall.exception))

    # --- Abnahme: kein Fahrzeug doppelt auf einer Rechnung ---

    def test_fahrzeug_kann_nie_auf_zwei_rechnungen_stehen(self):
        hilfe.erfassen_viele(self.verb, self.halle, 6, autohaus_id=1)
        erste = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        # Ueberlappender Zeitraum, dieselben Fahrzeuge: darf nichts mehr finden.
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-20", "2026-09-28")
        self.assertIn("kein offenes Fahrzeug", str(fall.exception))
        doppelt = self.verb.execute(
            "SELECT fahrzeug_id, COUNT(*) AS n FROM rechnungsposition"
            " GROUP BY fahrzeug_id HAVING n > 1").fetchall()
        self.assertEqual(doppelt, [])
        self.assertEqual(erste["netto_cent"], 6 * 14900)

    def test_positionen_sind_auf_datenbankebene_eindeutig(self):
        import sqlite3
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1)
        r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        _, posten = rechnung.mit_positionen(self.verb, r["id"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.verb.execute(
                "INSERT INTO rechnungsposition (rechnung_id, fahrzeug_id, text, netto_cent)"
                " VALUES (?,?,?,?)", (r["id"], posten[0]["fahrzeug_id"], "x", 1))

    # --- Bauliste: Woche mit 95 Fahrzeugen und 8 Autohaeusern ---

    def test_echte_woche_95_fahrzeuge_8_autohaeuser(self):
        verb = hilfe.frisch(anzahl_autohaeuser=8)
        buero = hilfe.benutzer(verb, 1)
        halle = hilfe.benutzer(verb, 2)
        verteilung = [12, 15, 9, 14, 11, 13, 10, 11]  # Summe 95
        self.assertEqual(sum(verteilung), 95)
        versatz = 0
        for haus, menge in enumerate(verteilung, start=1):
            hilfe.erfassen_viele(verb, halle, menge, autohaus_id=haus, ab=versatz)
            versatz += menge

        belege = []
        for haus in range(1, 9):
            r = rechnung.erzeugen(verb, buero, haus, "2026-09-14", "2026-09-21")
            rechnung.freigeben(verb, buero, r["id"])
            belege.append(rechnung.finalisieren(verb, buero, r["id"], self.dienst))

        self.assertEqual(len(belege), 8)
        self.assertEqual(len({b["lexware_nummer"] for b in belege}), 8)
        self.assertEqual(
            verb.execute("SELECT COUNT(*) FROM rechnungsposition").fetchone()[0], 95)
        self.assertEqual(
            sum(b["netto_cent"] for b in belege), 95 * 14900)

    # --- Abnahme: Gutschrift je Fahrzeug, Rest unberuehrt ---

    def test_gutschrift_fuer_fahrzeug_sieben_von_zwanzig(self):
        autos = hilfe.erfassen_viele(self.verb, self.halle, 20, autohaus_id=1)
        r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        rechnung.freigeben(self.verb, self.buero, r["id"])
        fertig = rechnung.finalisieren(self.verb, self.buero, r["id"], self.dienst)

        gut = rechnung.gutschrift(
            self.verb, self.buero, autos[6]["id"], "Lackkratzer uebersehen", self.dienst)

        self.assertEqual(gut["netto_cent"], 14900)
        unveraendert = self.verb.execute(
            "SELECT * FROM rechnung WHERE id=?", (r["id"],)).fetchone()
        self.assertEqual(unveraendert["netto_cent"], fertig["netto_cent"])
        self.assertEqual(unveraendert["status"], "finalisiert")
        _, posten = rechnung.mit_positionen(self.verb, r["id"])
        self.assertEqual(len(posten), 20)
        self.assertEqual(sum(1 for p in posten if p["gutgeschrieben"]), 1)
        self.assertEqual(self.dienst.belege[-1][0], "gutschrift")

    def test_zweite_gutschrift_fuer_dasselbe_fahrzeug_wird_abgelehnt(self):
        autos = hilfe.erfassen_viele(self.verb, self.halle, 3, autohaus_id=1)
        r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        rechnung.freigeben(self.verb, self.buero, r["id"])
        rechnung.finalisieren(self.verb, self.buero, r["id"], self.dienst)
        rechnung.gutschrift(self.verb, self.buero, autos[0]["id"], "Grund", self.dienst)
        with self.assertRaises(rechnung.Abgelehnt):
            rechnung.gutschrift(self.verb, self.buero, autos[0]["id"], "nochmal", self.dienst)

    def test_gutschrift_nur_nach_ausstellung(self):
        autos = hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1)
        rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.gutschrift(self.verb, self.buero, autos[0]["id"], "zu frueh", self.dienst)
        self.assertIn("noch nicht ausgestellt", str(fall.exception))

    # --- Unveraenderbarkeit nach Ausstellung ---

    def test_ausgestellte_rechnung_laesst_sich_nicht_stornieren(self):
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1)
        r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        rechnung.freigeben(self.verb, self.buero, r["id"])
        rechnung.finalisieren(self.verb, self.buero, r["id"], self.dienst)
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.stornieren(self.verb, self.buero, r["id"], "Versehen")
        self.assertIn("Gutschrift", str(fall.exception))

    def test_doppeltes_ausstellen_wird_abgelehnt(self):
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1)
        r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        rechnung.freigeben(self.verb, self.buero, r["id"])
        rechnung.finalisieren(self.verb, self.buero, r["id"], self.dienst)
        with self.assertRaises(rechnung.Abgelehnt):
            rechnung.finalisieren(self.verb, self.buero, r["id"], self.dienst)
        self.assertEqual(len([b for b in self.dienst.belege if b[0] == "rechnung"]), 1)

    def test_ohne_freigabe_kein_ausstellen(self):
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1)
        r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.finalisieren(self.verb, self.buero, r["id"], self.dienst)
        self.assertIn("Erst freigeben", str(fall.exception))

    # --- Storno vor Ausstellung gibt die Fahrzeuge frei ---

    def test_storno_des_entwurfs_gibt_fahrzeuge_zurueck(self):
        hilfe.erfassen_viele(self.verb, self.halle, 4, autohaus_id=1)
        r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        rechnung.stornieren(self.verb, self.buero, r["id"], "falscher Zeitraum")
        self.assertEqual(
            self.verb.execute("SELECT COUNT(*) FROM fahrzeug WHERE status='offen'").fetchone()[0],
            4)
        neu = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        self.assertEqual(neu["netto_cent"], 4 * 14900)

    # --- Abbruch mitten im Speichern ---

    def test_abbruch_mitten_im_erzeugen_hinterlaesst_nichts(self):
        hilfe.erfassen_viele(self.verb, self.halle, 5, autohaus_id=1)
        echte_notiz = rechnung.protokoll.notieren

        def platzt(*args, **kwargs):
            raise RuntimeError("Stromausfall")

        rechnung.protokoll.notieren = platzt
        try:
            with self.assertRaises(RuntimeError):
                rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        finally:
            rechnung.protokoll.notieren = echte_notiz

        self.assertEqual(self.verb.execute("SELECT COUNT(*) FROM rechnung").fetchone()[0], 0)
        self.assertEqual(
            self.verb.execute("SELECT COUNT(*) FROM rechnungsposition").fetchone()[0], 0)
        self.assertEqual(
            self.verb.execute("SELECT COUNT(*) FROM fahrzeug WHERE status='offen'").fetchone()[0],
            5)

    # --- Randfaelle aus der Bauliste ---

    def test_filiale_wird_mit_dem_hauptsitz_abgerechnet(self):
        self.verb.execute(
            "INSERT INTO autohaus (id,name,kurz,filiale_von,sammeln_mit_hauptsitz)"
            " VALUES (9,'Autohaus 1 Filiale Sued','AH1S',1,1)")
        hilfe.erfassen_viele(self.verb, self.halle, 3, autohaus_id=1, ab=0)
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=9, ab=50)

        with self.assertRaises(rechnung.Abgelehnt):
            rechnung.erzeugen(self.verb, self.buero, 9, "2026-09-14", "2026-09-21")

        r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        self.assertEqual(r["netto_cent"], 5 * 14900)

    def test_filiale_mit_eigener_rechnung_bleibt_getrennt(self):
        self.verb.execute(
            "INSERT INTO autohaus (id,name,kurz,filiale_von,sammeln_mit_hauptsitz)"
            " VALUES (9,'Autohaus 1 Filiale Nord','AH1N',1,0)")
        hilfe.erfassen_viele(self.verb, self.halle, 3, autohaus_id=1, ab=0)
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=9, ab=50)
        haupt = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")
        filiale = rechnung.erzeugen(self.verb, self.buero, 9, "2026-09-14", "2026-09-21")
        self.assertEqual(haupt["netto_cent"], 3 * 14900)
        self.assertEqual(filiale["netto_cent"], 2 * 14900)

    def test_fahrzeug_ueber_den_wochenwechsel_zaehlt_nach_fertigstellung(self):
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1, tag="2026-09-19", ab=0)
        hilfe.erfassen_viele(self.verb, self.halle, 3, autohaus_id=1, tag="2026-09-22", ab=50)
        woche = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-20")
        self.assertEqual(woche["netto_cent"], 2 * 14900)
        self.assertEqual(
            self.verb.execute("SELECT COUNT(*) FROM fahrzeug WHERE status='offen'").fetchone()[0],
            3)

    def test_nachzuegler_aus_abgerechneter_woche_geht_nicht_verloren(self):
        """Der Fall, der Geld gekostet hätte: Ein Auto wird nachträglich
        eingetragen, sein Fertigstellungstag liegt in einer Woche, die schon
        abgerechnet ist. Vorher war es über keinen Weg mehr abrechenbar."""
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1, tag="2026-09-15", ab=0)
        rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-20")
        spaet, _ = fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(90), 1, 1,
                                      "2026-09-17", vorgang="nachzuegler")

        zweite = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-21", "2026-09-27")
        self.assertEqual(zweite["netto_cent"], 14900)
        self.assertEqual(
            self.verb.execute("SELECT status FROM fahrzeug WHERE id=?",
                              (spaet["id"],)).fetchone()["status"], "abgerechnet")
        # Der Leistungszeitraum auf der Rechnung deckt den Nachzügler mit ab.
        self.assertEqual(zweite["von"], "2026-09-17")
        self.assertEqual(zweite["bis"], "2026-09-27")

    def test_was_noch_nicht_fertig_ist_kommt_nicht_mit(self):
        # Die Gegenrichtung: Der Stichtag bleibt ein Stichtag.
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1, tag="2026-09-15", ab=0)
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1, tag="2026-09-21", ab=60)
        erste = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-20")
        self.assertEqual(erste["netto_cent"], 2 * 14900)

    def test_krummes_datum_wird_beim_namen_genannt(self):
        hilfe.erfassen_viele(self.verb, self.halle, 1, autohaus_id=1, tag="2026-09-15")
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.erzeugen(self.verb, self.buero, 1, "2026-9-14", "2026-09-20")
        self.assertIn("kein gültiges Datum", str(fall.exception))

    def test_uebersicht_zeigt_genau_das_was_abgerechnet_wird(self):
        """Die Zahl auf der Bürotafel und die Summe auf der Rechnung müssen
        dieselbe sein. Sie waren es zweimal nicht."""
        self.verb.execute(
            "INSERT INTO autohaus (id,name,kurz,filiale_von,sammeln_mit_hauptsitz)"
            " VALUES (9,'Filiale Süd','FSUED',1,1)")
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1, tag="2026-09-15", ab=0)
        hilfe.erfassen_viele(self.verb, self.halle, 3, autohaus_id=9, tag="2026-09-16", ab=20)
        fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(95), 1, 1,
                           "2026-08-31", vorgang="alt-1")      # Nachzügler

        tafel = {z["id"]: z for z in rechnung.je_haus(self.verb, "2026-09-14", "2026-09-20")}
        self.assertEqual(tafel[1]["anzahl"], 6)                # 2 + 3 Filiale + 1 alter
        self.assertEqual(tafel[1]["frueher"], 1)
        self.assertNotIn(9, tafel)                             # Filiale hat keine eigene Zeile

        beleg = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-20")
        self.assertEqual(beleg["netto_cent"], tafel[1]["summe"])

    def test_zu_viele_positionen_werden_vor_dem_versand_abgefangen(self):
        verb = hilfe.frisch()
        buero = hilfe.benutzer(verb, 1)
        halle = hilfe.benutzer(verb, 2)
        hilfe.erfassen_viele(verb, halle, 301, autohaus_id=1)
        r = rechnung.erzeugen(verb, buero, 1, "2026-09-14", "2026-09-21")
        rechnung.freigeben(verb, buero, r["id"])
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.finalisieren(verb, buero, r["id"], self.dienst)
        self.assertIn("300", str(fall.exception))

    def test_der_storno_haelt_fest_was_auf_dem_entwurf_stand(self):
        """Nach dem Storno sind die Positionen gelöscht. Was daraufstand, muss
        trotzdem nachlesbar bleiben — sonst ist eine Lücke nicht erklärbar."""
        hilfe.erfassen_viele(self.verb, self.halle, 3, autohaus_id=1, tag="2026-09-18")
        beleg = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-20")
        rechnung.stornieren(self.verb, self.buero, beleg["id"], "Falsches Autohaus")
        eintrag = self.verb.execute(
            "SELECT * FROM protokoll ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(eintrag["was"], "Rechnungsentwurf storniert")
        self.assertIn("3 Fahrzeuge", eintrag["einzelheiten"])
        self.assertIn("447,00 EUR", eintrag["einzelheiten"])
        self.assertIn("Falsches Autohaus", eintrag["einzelheiten"])

    def test_freigabe_und_protokoll_gehoeren_zusammen(self):
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1, tag="2026-09-18")
        beleg = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-20")
        rechnung.freigeben(self.verb, self.buero, beleg["id"])
        eintrag = self.verb.execute(
            "SELECT * FROM protokoll ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(eintrag["was"], "Rechnung freigegeben")
        self.assertIn("298,00 EUR", eintrag["einzelheiten"])

    def test_erfasser_darf_nicht_abrechnen(self):
        hilfe.erfassen_viele(self.verb, self.halle, 2, autohaus_id=1)
        with self.assertRaises(rechnung.Abgelehnt):
            rechnung.erzeugen(self.verb, self.halle, 1, "2026-09-14", "2026-09-21")


if __name__ == "__main__":
    unittest.main()


class NebenlaeufigeAenderungen(unittest.TestCase):
    """Zwischen „ist noch offen?" und dem Schreiben kann das Büro die
    Sammelrechnung erzeugen. Dann darf die Änderung nicht mehr durchgehen."""

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)
        self.autos = hilfe.erfassen_viele(self.verb, self.halle, 3, autohaus_id=1)

    def _abrechnen(self):
        from hallenbuch import rechnung
        return rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-14", "2026-09-21")

    def test_erfassen_faellt_durch_wenn_das_autohaus_zwischendurch_stillgelegt_wird(self):
        """Die Gegenrichtung: Zwischen der Prüfung und dem Einfügen legt das
        Büro das Autohaus still. Ohne Wächter im Schreibbefehl stünde danach
        ein Fahrzeug bei einem stillgelegten Haus — unsichtbar in jeder
        Übersicht und nicht abzurechnen."""
        from hallenbuch import fahrzeuge as modul
        echt = modul.preis_fuer
        gemacht = []

        def dazwischen(verb, autohaus_id, leistung_id):
            preis = echt(verb, autohaus_id, leistung_id)
            if not gemacht:
                gemacht.append(1)
                verb.execute("UPDATE autohaus SET aktiv=0 WHERE id=?", (autohaus_id,))
            return preis

        modul.preis_fuer = dazwischen
        try:
            with self.assertRaises(modul.Abgelehnt) as fall:
                modul.erfassen(self.verb, self.halle, hilfe.fin_nummer(88), 1, 1,
                               "2026-09-18", vorgang="stillgelegt-1")
        finally:
            modul.preis_fuer = echt
        self.assertIn("stillgelegt", str(fall.exception))
        self.assertEqual(
            self.verb.execute("SELECT COUNT(*) FROM fahrzeug WHERE vorgang=?",
                              ("stillgelegt-1",)).fetchone()[0], 0)

    def test_aendern_faellt_durch_wenn_zwischendurch_abgerechnet_wird(self):
        """Der Wächter steckt im Schreibbefehl. Hier wird mitten in der
        Funktion abgerechnet — genau der Fall, den eine Vorabprüfung verpasst."""
        from hallenbuch import fahrzeuge as modul
        echt = modul.preis_fuer
        gerechnet = []

        def dazwischen(verb, autohaus_id, leistung_id):
            if not gerechnet:
                gerechnet.append(self._abrechnen())
            return echt(verb, autohaus_id, leistung_id)

        modul.preis_fuer = dazwischen
        try:
            with self.assertRaises(modul.Abgelehnt) as fall:
                modul.aendern(self.verb, self.buero, self.autos[0]["id"],
                              leistung_id=2)
        finally:
            modul.preis_fuer = echt
        self.assertIn("inzwischen abgerechnet", str(fall.exception))
        # Und das Fahrzeug ist unveraendert
        zeile = self.verb.execute("SELECT * FROM fahrzeug WHERE id=?",
                                  (self.autos[0]["id"],)).fetchone()
        self.assertEqual(zeile["leistung_id"], 1)
        self.assertEqual(zeile["netto_cent"], 14900)

    def test_verworfenes_fahrzeug_kann_nicht_zweimal_verworfen_werden(self):
        from hallenbuch import fahrzeuge as modul
        modul.verwerfen(self.verb, self.buero, self.autos[0]["id"], "Doppelt erfasst")
        with self.assertRaises(modul.Abgelehnt) as fall:
            modul.verwerfen(self.verb, self.buero, self.autos[0]["id"])
        self.assertIn("schon verworfen", str(fall.exception))

    def test_abgerechnetes_fahrzeug_laesst_sich_nicht_verwerfen(self):
        from hallenbuch import fahrzeuge as modul
        self._abrechnen()
        with self.assertRaises(modul.Abgelehnt) as fall:
            modul.verwerfen(self.verb, self.buero, self.autos[0]["id"])
        self.assertIn("Gutschrift", str(fall.exception))
        zeile = self.verb.execute("SELECT status FROM fahrzeug WHERE id=?",
                                  (self.autos[0]["id"],)).fetchone()
        self.assertEqual(zeile["status"], "abgerechnet")

    def test_unbekanntes_fahrzeug_wird_als_solches_gemeldet(self):
        from hallenbuch import fahrzeuge as modul
        with self.assertRaises(modul.Abgelehnt) as fall:
            modul.verwerfen(self.verb, self.buero, 999999)
        self.assertIn("nicht gefunden", str(fall.exception))

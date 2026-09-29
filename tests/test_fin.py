import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hallenbuch import fin  # noqa: E402


class FinTest(unittest.TestCase):

    def test_normalisiert_trenner_und_kleinschreibung(self):
        self.assertEqual(fin.normalisieren(" wvw zzz1jz-3w386752 "), "WVWZZZ1JZ3W386752")

    def test_ersetzt_verbotene_zeichen(self):
        # I, O und Q gibt es in einer FIN nicht, also sind 1 und 0 gemeint.
        self.assertEqual(fin.normalisieren("WVWZZZ1JZ3WO86752"), "WVWZZZ1JZ3W086752")
        self.assertEqual(fin.normalisieren("IVWZZZ1JZ3W386752"), "1VWZZZ1JZ3W386752")

    def test_laenge_wird_abgelehnt(self):
        with self.assertRaises(fin.FinFehler):
            fin.normalisieren("WVWZZZ1JZ3W3867")

    def test_europaeische_fin_ohne_gueltige_pruefziffer_geht_durch(self):
        """Die Kernregel der Bauliste: Pruefziffer ist NIE eine Sperre."""
        wert = fin.normalisieren("WVWZZZ1JZ3W386752")
        self.assertFalse(fin.ist_nordamerikanisch(wert))
        self.assertEqual(fin.hinweis(wert), "")

    def test_us_fin_mit_falscher_pruefziffer_gibt_nur_hinweis(self):
        wert = fin.normalisieren("1HGCM82633A004352")
        self.assertTrue(fin.ist_nordamerikanisch(wert))
        # Richtige Pruefziffer dieser bekannten Beispiel-FIN ist 3 an Stelle 9.
        self.assertTrue(fin.pruefziffer_stimmt(wert))
        falsch = fin.normalisieren("1HGCM82643A004352")
        self.assertNotEqual(fin.hinweis(falsch), "")

    def test_gruppierung(self):
        self.assertEqual(fin.gruppiert("WVWZZZ1JZ3W386752"), "WVW ZZZ1JZ 3W386752")

    def test_aus_texterkennung(self):
        self.assertEqual(
            fin.aus_text("FIN: WVW ZZZ1JZ 3W386752 Typ 1J"), "WVWZZZ1JZ3W386752")
        self.assertIsNone(fin.aus_text("kein Treffer hier"))

    def test_aus_texterkennung_mit_typischem_rauschen(self):
        """So sieht ein Typenschild aus, wenn eine Erkennung es ausliest:
        Zeilenumbrüche, Beschriftungen ringsum, und I/O/Q statt 1/0."""
        roh = ("MERCEDES-BENZ\n"
               "FAHRZEUG-IDENT-NR.\n"
               "WDD I69O32 1J1OOOOO\n"
               "ZUL. GESAMTGEW. 2100 KG")
        self.assertEqual(fin.aus_text(roh), "WDD1690321J100000")

    def test_aus_texterkennung_erfindet_nichts(self):
        """Der Kern der Sache: lieber nichts als eine erfundene FIN."""
        self.assertIsNone(fin.aus_text("MERCEDES-BENZ\nFAHRZEUG-IDENT-NR."))
        # 18 erlaubte Zeichen in einer Zeile duerfen keinen 17er-Ausschnitt liefern
        self.assertIsNone(fin.aus_text("ZUL. GESAMTGEW. 2100 KG"))
        # Woerter ueber Zeilengrenzen werden nicht zusammengeklebt
        self.assertIsNone(fin.aus_text("WVWZZZ1JZ\n3W386752"))

    def test_aus_texterkennung_nimmt_den_ersten_vollstaendigen_block(self):
        self.assertIsNone(fin.aus_text("WVWZZZ1JZ3W3867"))       # nur 15 Zeichen
        self.assertIsNone(fin.aus_text(""))
        self.assertIsNone(fin.aus_text(None))


if __name__ == "__main__":
    unittest.main()

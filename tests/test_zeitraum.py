"""Die Schnellwahl rechnet Zeiträume aus. Das sind die Fälle, die weh tun."""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hallenbuch import zeitraum  # noqa: E402


class Zeitraeume(unittest.TestCase):

    def test_woche_laeuft_montag_bis_sonntag(self):
        montag = date(2026, 9, 21)
        self.assertEqual(zeitraum.aufloesen("diese-woche", montag),
                         ("2026-09-21", "2026-09-27"))

    def test_mitten_in_der_woche_dieselbe_woche(self):
        for tag in (22, 23, 24, 25, 26, 27):
            self.assertEqual(zeitraum.aufloesen("diese-woche", date(2026, 9, tag)),
                             ("2026-09-21", "2026-09-27"), "am %d." % tag)

    def test_sonntag_gehoert_noch_zur_laufenden_woche(self):
        """Wer sonntagabends abrechnet, meint die Woche, die gerade endet."""
        self.assertEqual(zeitraum.aufloesen("diese-woche", date(2026, 9, 27)),
                         ("2026-09-21", "2026-09-27"))

    def test_letzte_woche(self):
        self.assertEqual(zeitraum.aufloesen("letzte-woche", date(2026, 9, 21)),
                         ("2026-09-14", "2026-09-20"))

    def test_letzte_woche_ueber_den_monatswechsel(self):
        self.assertEqual(zeitraum.aufloesen("letzte-woche", date(2026, 10, 1)),
                         ("2026-09-21", "2026-09-27"))

    def test_monat_kennt_seine_laenge(self):
        self.assertEqual(zeitraum.aufloesen("dieser-monat", date(2026, 9, 15)),
                         ("2026-09-01", "2026-09-30"))
        self.assertEqual(zeitraum.aufloesen("dieser-monat", date(2026, 7, 1)),
                         ("2026-07-01", "2026-07-31"))

    def test_letzter_monat_vom_monatsletzten_aus(self):
        """Der Klassiker: am 31. März auf den Februar zurückrechnen."""
        self.assertEqual(zeitraum.aufloesen("letzter-monat", date(2026, 3, 31)),
                         ("2026-02-01", "2026-02-28"))

    def test_letzter_monat_im_schaltjahr(self):
        self.assertEqual(zeitraum.aufloesen("letzter-monat", date(2024, 3, 15)),
                         ("2024-02-01", "2024-02-29"))

    def test_letzter_monat_ueber_den_jahreswechsel(self):
        self.assertEqual(zeitraum.aufloesen("letzter-monat", date(2026, 1, 15)),
                         ("2025-12-01", "2025-12-31"))

    def test_unbekannter_schluessel_gibt_nichts_zurueck(self):
        """Lieber nichts als ein geratener Zeitraum auf einer Rechnung."""
        for unsinn in ("gestern", "", None, "letzte-Woche", "diese_woche"):
            self.assertIsNone(zeitraum.aufloesen(unsinn, date(2026, 9, 21)))

    def test_alle_liefert_vier_knoepfe_in_fester_reihenfolge(self):
        knoepfe = zeitraum.alle(date(2026, 9, 21))
        self.assertEqual([k[0] for k in knoepfe], list(zeitraum.SCHLUESSEL))
        for _, beschriftung, von, bis in knoepfe:
            self.assertTrue(beschriftung and von <= bis)

    def test_passend_erkennt_den_aktiven_knopf(self):
        heute = date(2026, 9, 21)
        self.assertEqual(zeitraum.passend("2026-09-14", "2026-09-20", heute), "letzte-woche")
        self.assertEqual(zeitraum.passend("2026-09-01", "2026-09-30", heute), "dieser-monat")
        # Eigener Zeitraum: kein Knopf leuchtet
        self.assertEqual(zeitraum.passend("2026-09-03", "2026-09-11", heute), "")


if __name__ == "__main__":
    unittest.main()

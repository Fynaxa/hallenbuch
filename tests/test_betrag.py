"""Getippte Beträge in Cent. Die Funktion hatte bis zum 22.09. keinen Test —
und darin steckte der teuerste Fehler des ganzen Projekts: `1.299` wurde zu
1,29 EUR, hundertfach zu wenig, ohne Fehlermeldung."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hallenbuch import datenbank  # noqa: E402


class Betraege(unittest.TestCase):

    RICHTIG = [
        ("149,90", 14990),
        ("149", 14900),
        ("149.90", 14990),          # jemand tippt mit dem Ziffernblock
        ("0,50", 50),
        ("0", 0),
        ("149,9", 14990),           # eine Stelle wird aufgefüllt
        ("129,90 €", 12990),
        ("1 299,90", 129990),       # mit Leerzeichen getrennt
        ("1.299,90", 129990),       # deutscher Tausenderpunkt
        ("1.299", 129900),          # DER Fall: 1299 Euro, nicht 1,29
        ("1.500", 150000),
        ("12.345", 1234500),
        ("1.234.567,89", 123456789),
        ("1,299.90", 129990),       # englische Schreibweise
        ("12.50", 1250),            # zwei Stellen: Dezimalpunkt
        ("-5,00", -500),
    ]

    ABGELEHNT = ["", "   ", None, "abc", "1.29.9", "149,999", "12,3456", "1.2345", "€"]

    def test_getippte_betraege_werden_richtig_gelesen(self):
        for text, erwartet in self.RICHTIG:
            self.assertEqual(datenbank.cent(text), erwartet,
                             "%r sollte %d Cent sein" % (text, erwartet))

    def test_der_tausenderpunkt_wird_nicht_zum_komma(self):
        """Der eigentliche Fehler, einzeln festgehalten: Ein Preis von 1.299
        Euro darf nicht als 1,29 Euro in der Datenbank landen."""
        self.assertEqual(datenbank.cent("1.299"), 129900)
        self.assertEqual(datenbank.euro(datenbank.cent("1.299")), "1299,00")

    def test_unklares_wird_abgelehnt_statt_geraten(self):
        for text in self.ABGELEHNT:
            with self.assertRaises(ValueError, msg="%r haette abgelehnt gehoeren" % text):
                datenbank.cent(text)

    def test_hin_und_zurueck(self):
        for text, cent in self.RICHTIG:
            if cent >= 0:
                self.assertEqual(datenbank.cent(datenbank.euro(cent)), cent, text)


if __name__ == "__main__":
    unittest.main()

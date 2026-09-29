"""Die Bremse gegen das Durchprobieren von Passwörtern, mit gestellter Uhr."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hallenbuch import anmeldeschutz, datenbank  # noqa: E402

JETZT = 1_700_000_000


class Bremse(unittest.TestCase):

    def setUp(self):
        self.verb = datenbank.verbinden(":memory:")
        datenbank.aufbauen(self.verb)

    def daneben(self, anzahl, name="achmed", herkunft="1.2.3.4", ab=0):
        for i in range(anzahl):
            anmeldeschutz.vermerken(self.verb, name, herkunft, False, JETZT + ab + i)

    def test_vier_fehlversuche_sind_noch_frei(self):
        self.daneben(4)
        self.assertIsNone(anmeldeschutz.gesperrt(self.verb, "achmed", "1.2.3.4", JETZT + 5))

    def test_beim_fuenften_kommt_die_pause(self):
        self.daneben(5)
        pause = anmeldeschutz.gesperrt(self.verb, "achmed", "1.2.3.4", JETZT + 6)
        self.assertEqual(pause, 15)

    def test_die_pause_schrumpft_mit_der_zeit(self):
        self.daneben(5)
        self.assertEqual(anmeldeschutz.gesperrt(self.verb, "achmed", "1.2.3.4",
                                                JETZT + 10 * 60), 5)

    def test_nach_dem_fenster_geht_es_von_selbst_weiter(self):
        """Kein harter Riegel: In der Halle kann niemand warten, bis jemand im
        Büro einen Zugang freischaltet."""
        self.daneben(5)
        self.assertIsNone(anmeldeschutz.gesperrt(self.verb, "achmed", "1.2.3.4",
                                                 JETZT + 15 * 60 + 1))

    def test_ein_anderer_name_ist_nicht_betroffen(self):
        self.daneben(5)
        self.assertIsNone(anmeldeschutz.gesperrt(self.verb, "halle", "9.9.9.9", JETZT + 6))

    def test_erfolg_loescht_die_fehlversuche(self):
        self.daneben(4)
        anmeldeschutz.vermerken(self.verb, "achmed", "1.2.3.4", True, JETZT + 5)
        self.assertEqual(anmeldeschutz.fehlversuche(
            self.verb, anmeldename="achmed", jetzt=JETZT + 6), 0)
        self.assertIsNone(anmeldeschutz.gesperrt(self.verb, "achmed", "1.2.3.4", JETZT + 6))

    def test_viele_namen_von_einer_herkunft_werden_auch_gebremst(self):
        """Wer 20 verschiedene Namen durchprobiert, umgeht die Zählung je Name."""
        for i in range(anmeldeschutz.VERSUCHE_JE_HERKUNFT):
            anmeldeschutz.vermerken(self.verb, "name%d" % i, "5.5.5.5", False, JETZT + i)
        self.assertIsNotNone(
            anmeldeschutz.gesperrt(self.verb, "noch-einer", "5.5.5.5", JETZT + 30))
        # Von woanders geht es weiter
        self.assertIsNone(
            anmeldeschutz.gesperrt(self.verb, "noch-einer", "7.7.7.7", JETZT + 30))

    def test_alte_versuche_werden_weggeraeumt(self):
        self.daneben(3, ab=-8 * 24 * 3600)
        self.daneben(2, name="halle")
        weg = anmeldeschutz.aufraeumen(self.verb, JETZT)
        self.assertEqual(weg, 3)
        self.assertEqual(self.verb.execute(
            "SELECT COUNT(*) FROM anmeldeversuch").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()

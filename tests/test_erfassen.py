"""Die Regeln des Erfassens — und die Grenzen, an denen sie gelten.

Diese Datei entstand am 23.09. aus einem Mutationslauf: Wer `>` in `>=`
tauschte, wer die Wiedererkennung der Vorgangsnummer abschaltete oder wer aus
`und` ein `oder` machte, bekam von 286 Tests keinen einzigen roten. Die Regeln
waren beschrieben, ihre Ränder nicht.
"""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hallenbuch import fahrzeuge, rechnung  # noqa: E402
from tests import hilfe  # noqa: E402


def _tag(versatz: int) -> str:
    return (date.today() + timedelta(days=versatz)).isoformat()


class GrenzenDerDatumspruefung(unittest.TestCase):
    """`TAGE_ZUKUNFT` und `TAGE_VERGANGENHEIT` sind keine Zierde: Ein Fahrzeug
    mit falschem Datum landet in keiner Sammelrechnung und wird nie berechnet.
    Geprüft wird deshalb beides — dass die Grenze hält und dass sie nicht einen
    Tag zu früh zuschlägt."""

    def setUp(self):
        self.verb = hilfe.frisch()
        self.halle = hilfe.benutzer(self.verb, 2)
        self.zaehler = 0

    def _erfassen(self, tag):
        self.zaehler += 1
        return fahrzeuge.erfassen(
            self.verb, self.halle, hilfe.fin_nummer(self.zaehler), 1, 1, tag,
            vorgang="grenze-%d" % self.zaehler)

    def test_genau_an_der_zukunftsgrenze_geht_noch(self):
        zeile, _ = self._erfassen(_tag(fahrzeuge.TAGE_ZUKUNFT))
        self.assertEqual(zeile["status"], "offen")

    def test_einen_tag_weiter_nicht_mehr(self):
        with self.assertRaises(fahrzeuge.Abgelehnt) as fall:
            self._erfassen(_tag(fahrzeuge.TAGE_ZUKUNFT + 1))
        self.assertIn("Zukunft", str(fall.exception))

    def test_genau_an_der_rueckwaertsgrenze_kommt_noch_kein_hinweis(self):
        _, hinweise = self._erfassen(_tag(-fahrzeuge.TAGE_VERGANGENHEIT))
        self.assertEqual(hinweise, [])

    def test_einen_tag_weiter_zurueck_kommt_der_hinweis(self):
        _, hinweise = self._erfassen(_tag(-fahrzeuge.TAGE_VERGANGENHEIT - 1))
        self.assertTrue(any("Tage zurück" in h for h in hinweise), hinweise)
        # Ein Hinweis ist kein Nein: Das Fahrzeug ist erfasst.
        self.assertEqual(self.verb.execute(
            "SELECT COUNT(*) FROM fahrzeug").fetchone()[0], 1)


class VorgangsnummerWirdWiedererkannt(unittest.TestCase):
    """Der Schlüssel, an dem die Warteschlange im Handy hängt. Sendet das Handy
    denselben Vorgang zweimal — weil die Antwort auf dem Rückweg verloren ging —,
    darf daraus kein zweites Fahrzeug und keine zweite Rechnungsposition werden.

    Bis zum 23.09. sendete kein einziger Test dieselbe Vorgangsnummer zweimal."""

    def setUp(self):
        self.verb = hilfe.frisch()
        self.halle = hilfe.benutzer(self.verb, 2)

    def test_zweimal_derselbe_vorgang_ergibt_ein_fahrzeug(self):
        erste, _ = fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(1),
                                      1, 1, "2026-09-18", vorgang="doppelt-1")
        zweite, hinweise = fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(2),
                                              1, 2, "2026-09-19", vorgang="doppelt-1")
        self.assertEqual(zweite["id"], erste["id"], "dieselbe Zeile, kein zweites Fahrzeug")
        self.assertTrue(any("schon erfasst" in h for h in hinweise), hinweise)
        self.assertEqual(self.verb.execute(
            "SELECT COUNT(*) FROM fahrzeug").fetchone()[0], 1)

    def test_die_zweite_sendung_aendert_nichts_am_bestand(self):
        """Wichtig fuer das Geld: Die Wiederholung darf die erste Erfassung
        nicht ueberschreiben — sonst wanderte der Preis auf eine andere
        Leistung, nachdem die Rechnung schon lief."""
        erste, _ = fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(3),
                                      1, 1, "2026-09-18", vorgang="doppelt-2")
        fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(4), 2, 3,
                           "2026-09-19", vorgang="doppelt-2")
        jetzt = self.verb.execute("SELECT * FROM fahrzeug WHERE id=?",
                                  (erste["id"],)).fetchone()
        self.assertEqual(jetzt["leistung_id"], 1)
        self.assertEqual(jetzt["autohaus_id"], 1)
        self.assertEqual(jetzt["netto_cent"], erste["netto_cent"])

    def test_ohne_vorgangsnummer_entstehen_zwei(self):
        """Die Gegenrichtung: Die Wiedererkennung haengt am Schluessel, nicht
        an der FIN — dasselbe Fahrzeug kann zweimal aufbereitet werden."""
        fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(5), 1, 1,
                           "2026-09-18", vorgang="a")
        fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(5), 1, 2,
                           "2026-09-20", vorgang="b")
        self.assertEqual(self.verb.execute(
            "SELECT COUNT(*) FROM fahrzeug").fetchone()[0], 2)


class FilialeMitEigenerAbrechnung(unittest.TestCase):
    """Eine Filiale, die NICHT mit dem Hauptsitz sammelt, rechnet selbst ab.
    Ihr Hauptsitz ist dann nur noch eine Zeile im Stammblatt. Wird er
    stillgelegt, darf das die Filiale nicht treffen — sonst bleibt wieder Geld
    liegen, diesmal an einem Betrieb, der gar nichts damit zu tun hat."""

    def setUp(self):
        self.verb = hilfe.frisch()
        self.halle = hilfe.benutzer(self.verb, 2)
        self.buero = hilfe.benutzer(self.verb, 1)
        self.verb.execute(
            "INSERT INTO autohaus (id,name,kurz,strasse,plz,ort,zahlungsziel,"
            " filiale_von,sammeln_mit_hauptsitz)"
            " VALUES (9,'Filiale Süd','FS','Südweg 1','38100','Braunschweig',14,1,0)")
        self.verb.execute("UPDATE autohaus SET aktiv=0 WHERE id=1")

    def test_sie_erfasst_weiter(self):
        zeile, _ = fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(7),
                                      9, 1, "2026-09-18", vorgang="eigen-1")
        self.assertEqual(zeile["autohaus_id"], 9)

    def test_und_sie_laesst_sich_abrechnen(self):
        fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(8), 9, 1,
                           "2026-09-18", vorgang="eigen-2")
        r = rechnung.erzeugen(self.verb, self.buero, 9, "2026-09-14", "2026-09-21")
        self.assertEqual(r["netto_cent"], 14900)

    def test_eine_sammelnde_filiale_dagegen_nicht(self):
        """Die Gegenrichtung, damit der Test nicht einfach alles durchlaesst."""
        self.verb.execute("UPDATE autohaus SET sammeln_mit_hauptsitz=1 WHERE id=9")
        with self.assertRaises(fahrzeuge.Abgelehnt):
            fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(9), 9, 1,
                               "2026-09-18", vorgang="eigen-3")


class GrenzenDerSuche(unittest.TestCase):

    def setUp(self):
        self.verb = hilfe.frisch()
        self.halle = hilfe.benutzer(self.verb, 2)
        self.zeile, _ = fahrzeuge.erfassen(self.verb, self.halle, hilfe.fin_nummer(11),
                                           1, 1, "2026-09-18", vorgang="such-1")

    def test_genau_die_mindestlaenge_sucht_schon(self):
        stueck = self.zeile["fin"][-fahrzeuge.MINDESTLAENGE_SUCHE:]
        self.assertTrue(fahrzeuge.suchen(self.verb, stueck))

    def test_ein_zeichen_weniger_sucht_nicht(self):
        stueck = self.zeile["fin"][-(fahrzeuge.MINDESTLAENGE_SUCHE - 1):]
        self.assertEqual(fahrzeuge.suchen(self.verb, stueck), [])


class GrenzenDerRechnung(unittest.TestCase):

    def setUp(self):
        self.verb = hilfe.frisch()
        self.buero = hilfe.benutzer(self.verb, 1)
        self.halle = hilfe.benutzer(self.verb, 2)

    def test_ein_einzelner_tag_ist_ein_gueltiger_zeitraum(self):
        """Wer heute abrechnet, traegt zweimal dasselbe Datum ein. Das ist kein
        rueckwaerts laufender Zeitraum."""
        hilfe.erfassen_viele(self.verb, self.halle, 1, autohaus_id=1, tag="2026-09-18")
        r = rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-18", "2026-09-18")
        self.assertEqual(r["von"], "2026-09-18")
        self.assertEqual(r["bis"], "2026-09-18")

    def test_ein_tag_rueckwaerts_ist_keiner(self):
        with self.assertRaises(rechnung.Abgelehnt) as fall:
            rechnung.erzeugen(self.verb, self.buero, 1, "2026-09-19", "2026-09-18")
        self.assertIn("rückwärts", str(fall.exception))


if __name__ == "__main__":
    unittest.main()

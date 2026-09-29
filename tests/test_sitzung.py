"""Der Sitzungs-Keks. Bis zum 22.09. prüfte kein einziger Test, ob ein
gefälschter Keks wirklich abgewiesen wird — die Ablehnungspfade wurden von der
Testsuite nie ausgeführt. Gemessen, nicht vermutet."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["HALLENBUCH_GEHEIMNIS"] = "test-geheimnis"

from hallenbuch import sicherheit  # noqa: E402


class Sitzungskeks(unittest.TestCase):

    def test_ein_echter_keks_wird_angenommen(self):
        keks = sicherheit.sitzung_ausstellen(7, 3)
        self.assertEqual(sicherheit.sitzung_pruefen(keks), (7, 3))

    def test_eine_veraenderte_benutzernummer_wird_abgewiesen(self):
        """Der Angriff, um den es geht: aus dem eigenen Keks den des Büros
        machen, indem man die Nummer hochzählt."""
        keks = sicherheit.sitzung_ausstellen(7, 1)
        nummer, stand, ablauf, unterschrift = keks.split(":")
        gefaelscht = "%s:%s:%s:%s" % (1, stand, ablauf, unterschrift)
        self.assertIsNone(sicherheit.sitzung_pruefen(gefaelscht))

    def test_ein_veraenderter_sitzungsstand_wird_abgewiesen(self):
        keks = sicherheit.sitzung_ausstellen(7, 1)
        nummer, stand, ablauf, unterschrift = keks.split(":")
        self.assertIsNone(sicherheit.sitzung_pruefen(
            "%s:%s:%s:%s" % (nummer, int(stand) + 1, ablauf, unterschrift)))

    def test_eine_verlaengerte_gueltigkeit_wird_abgewiesen(self):
        keks = sicherheit.sitzung_ausstellen(7, 1)
        nummer, stand, ablauf, unterschrift = keks.split(":")
        self.assertIsNone(sicherheit.sitzung_pruefen(
            "%s:%s:%s:%s" % (nummer, stand, int(ablauf) + 99999, unterschrift)))

    def test_eine_gefaelschte_unterschrift_wird_abgewiesen(self):
        keks = sicherheit.sitzung_ausstellen(7, 1)
        nummer, stand, ablauf, _ = keks.split(":")
        self.assertIsNone(sicherheit.sitzung_pruefen(
            "%s:%s:%s:%s" % (nummer, stand, ablauf, "0" * 64)))

    def test_ein_keks_mit_fremdem_geheimnis_wird_abgewiesen(self):
        keks = sicherheit.sitzung_ausstellen(7, 1)
        vorher = os.environ["HALLENBUCH_GEHEIMNIS"]
        os.environ["HALLENBUCH_GEHEIMNIS"] = "ein-anderes-geheimnis"
        try:
            self.assertIsNone(sicherheit.sitzung_pruefen(keks))
        finally:
            os.environ["HALLENBUCH_GEHEIMNIS"] = vorher
        self.assertEqual(sicherheit.sitzung_pruefen(keks), (7, 1))

    def test_ein_abgelaufener_keks_wird_abgewiesen(self):
        import hashlib
        import hmac
        nutzlast = "7:1:%d" % (int(time.time()) - 60)
        unterschrift = hmac.new(b"test-geheimnis", nutzlast.encode(),
                                hashlib.sha256).hexdigest()
        self.assertIsNone(sicherheit.sitzung_pruefen("%s:%s" % (nutzlast, unterschrift)))

    def test_muell_wird_abgewiesen(self):
        for keks in ("", None, "abc", "1:2:3", "1:2:3:4:5", ":::", "a:b:c:d"):
            self.assertIsNone(sicherheit.sitzung_pruefen(keks), repr(keks))


if __name__ == "__main__":
    unittest.main()

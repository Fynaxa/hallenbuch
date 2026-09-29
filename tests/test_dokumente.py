"""Stimmen die Dokumente noch mit dem Produkt überein?

Die Tabelle im README nennt für jede Zusage den Test, der sie beweist. Eine
solche Liste altert still: Ein Test wird umbenannt, die Zeile bleibt stehen,
und die Zusage sieht weiter belegt aus, ohne es zu sein. Am 23.09. stand dort
„270 Tests plus 40 Prüfungen" — tatsächlich waren es 310 und 49.
"""
import ast
import os
import re
import subprocess
import sys
import unittest

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WURZEL)


def _readme():
    with open(os.path.join(WURZEL, "README.md"), encoding="utf-8") as datei:
        return datei.read()


def _alle_testnamen():
    namen = set()
    ordner = os.path.join(WURZEL, "tests")
    for datei in os.listdir(ordner):
        if not (datei.startswith("test_") and datei.endswith(".py")):
            continue
        with open(os.path.join(ordner, datei), encoding="utf-8") as offen:
            baum = ast.parse(offen.read())
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.FunctionDef) and knoten.name.startswith("test"):
                namen.add(knoten.name)
    return namen


class DasReadmeZeigtAufEchteTests(unittest.TestCase):

    def test_jeder_genannte_test_existiert(self):
        vorhanden = _alle_testnamen()
        genannt = set(re.findall(r"`(test_[a-z0-9_]*\*?)`", _readme()))
        self.assertTrue(genannt, "im README steht keine einzige Zusage mehr")
        fehlend = []
        for name in sorted(genannt):
            if name.endswith("*"):
                if not any(t.startswith(name[:-1]) for t in vorhanden):
                    fehlend.append(name)
            elif name not in vorhanden:
                fehlend.append(name)
        self.assertEqual(fehlend, [], "das README verspricht Tests, die es nicht gibt")

    def test_die_genannte_anzahl_stimmt(self):
        """Eine Zahl im Dokument ist eine Zusage wie jede andere."""
        behauptet = re.search(r"(\d+) Tests plus (\d+) Prüfungen", _readme())
        self.assertIsNotNone(behauptet, "die Zahl fehlt im README")
        gezaehlt = 0
        lader = unittest.defaultTestLoader.discover(os.path.join(WURZEL, "tests"))
        for fall in _flach(lader):
            gezaehlt += 1
        self.assertEqual(int(behauptet.group(1)), gezaehlt,
                         "das README nennt eine andere Zahl als der Lauf")

    def test_die_pruefungen_der_warteschlange_werden_mitgezaehlt(self):
        """Der Node-Teil läuft nicht in unittest mit und wird sonst vergessen."""
        behauptet = re.search(r"(\d+) Tests plus (\d+) Prüfungen", _readme())
        knoten = _node()
        if not knoten:
            self.skipTest("node ist hier nicht im Pfad")
        lauf = subprocess.run([knoten, os.path.join(WURZEL, "tests", "warteschlange.mjs")],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(lauf.returncode, 0, lauf.stdout[-800:] + lauf.stderr[-400:])
        gezaehlt = len(re.findall(r"^\s+ok ", lauf.stdout, re.M))
        self.assertEqual(int(behauptet.group(2)), gezaehlt,
                         "das README nennt eine andere Zahl als der Node-Lauf")


def _flach(menge):
    for teil in menge:
        if isinstance(teil, unittest.TestSuite):
            for weiter in _flach(teil):
                yield weiter
        else:
            yield teil


def _node():
    for kandidat in ("node",
                     os.path.expanduser("~/.nvm/versions/node/v24.16.0/bin/node")):
        pfad = kandidat if os.path.sep in kandidat else _im_pfad(kandidat)
        if pfad and os.path.exists(pfad):
            return pfad
    return ""


def _im_pfad(name):
    for teil in os.environ.get("PATH", "").split(os.pathsep):
        ziel = os.path.join(teil, name)
        if os.path.exists(ziel):
            return ziel
    return ""


if __name__ == "__main__":
    unittest.main()

"""Fuehrt die Warteschlangen-Pruefung in Node aus.

Die Warteschlange ist der Teil, an dem Datenverlust haengt, und sie lief bis
jetzt voellig ungeprueft auf den Handys der Mitarbeiter.
"""
import os
import shutil
import subprocess
import sys
import unittest

HIER = os.path.dirname(os.path.abspath(__file__))
NODE_PFADE = ["node", os.path.expanduser("~/.nvm/versions/node/v24.16.0/bin/node")]


def _node():
    for kandidat in NODE_PFADE:
        gefunden = shutil.which(kandidat) or (
            kandidat if os.path.isfile(kandidat) else None)
        if gefunden:
            return gefunden
    return None


class Warteschlange(unittest.TestCase):

    def test_warteschlange_haelt_was_sie_verspricht(self):
        node = _node()
        if not node:
            self.skipTest("node nicht gefunden — Warteschlange ungeprueft")
        lauf = subprocess.run(
            [node, os.path.join(HIER, "warteschlange.mjs")],
            capture_output=True, text=True, timeout=60)
        sys.stdout.write(lauf.stdout)
        self.assertEqual(lauf.returncode, 0,
                         lauf.stdout + "\n" + lauf.stderr)
        self.assertIn("alle Pruefungen bestanden", lauf.stdout)


if __name__ == "__main__":
    unittest.main()

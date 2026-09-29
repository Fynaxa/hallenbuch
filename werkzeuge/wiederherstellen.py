#!/usr/bin/env python3
"""Eine Sicherung wieder einspielen.

  python3 werkzeuge/wiederherstellen.py                      zeigt die Sicherungen
  python3 werkzeuge/wiederherstellen.py sicherung-... --ja    spielt sie ein

**Vorher den Dienst anhalten**, sonst schreibt er weiter in die Datei, die
gerade ersetzt wird:

  sudo systemctl stop hallenbuch
  python3 werkzeuge/wiederherstellen.py sicherung-2026-09-22T03-00-00.db --ja
  sudo systemctl start hallenbuch

Der vorhandene Stand wird nicht weggeworfen, sondern als `.vorher-<Zeit>`
danebengelegt. Fotos liegen NICHT in der Sicherung, die stehen in betrieb/fotos.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hallenbuch import datenbank, sicherung  # noqa: E402


def main():
    zerleger = argparse.ArgumentParser(description="Sicherung einspielen")
    zerleger.add_argument("datei", nargs="?", help="Name der Sicherung")
    zerleger.add_argument("--ja", action="store_true",
                          help="wirklich einspielen (ohne das passiert nichts)")
    args = zerleger.parse_args()

    ziel = os.environ.get("HALLENBUCH_DB", datenbank.PFAD)
    ordner = os.path.join(os.path.dirname(ziel), "sicherungen")
    vorhanden = sicherung.vorhandene(ordner)

    if not args.datei:
        if not vorhanden:
            print("Keine Sicherungen in %s" % ordner, file=sys.stderr)
            return 1
        print("Sicherungen in %s, neueste zuerst:" % ordner)
        for eintrag in vorhanden:
            print("  %s  %d kB" % (eintrag["datei"], eintrag["groesse"] // 1024))
        print("\nEinspielen mit: python3 werkzeuge/wiederherstellen.py %s --ja"
              % vorhanden[0]["datei"])
        return 0

    quelle = args.datei if os.path.isfile(args.datei) else os.path.join(ordner, args.datei)
    if not os.path.isfile(quelle):
        print("Sicherung nicht gefunden: %s" % quelle, file=sys.stderr)
        return 1

    laeuft = sicherung.dienst_laeuft(os.path.dirname(ziel) or ".")
    if laeuft:
        print("Der Dienst läuft noch (Prozess %d). Erst anhalten:" % laeuft, file=sys.stderr)
        print("  sudo systemctl stop hallenbuch", file=sys.stderr)
        print("Sonst schreibt er weiter in eine Datei, die es nicht mehr gibt, "
              "und alles aus diesem Moment ist verloren.", file=sys.stderr)
        return 1

    try:
        befund = sicherung.pruefen(quelle)
    except sicherung.SicherungFehler as fehler:
        print("Diese Sicherung taugt nichts: %s" % fehler, file=sys.stderr)
        return 1

    print("Sicherung %s, %d kB, geprüft." % (befund["datei"], befund["groesse"] // 1024))
    print("  " + ", ".join("%s %d" % (k, v) for k, v in befund["zeilen"].items()))
    if not args.ja:
        print("\nEs wurde NICHTS verändert. Zum Einspielen noch einmal mit --ja aufrufen.")
        print("Vorher den Dienst anhalten: sudo systemctl stop hallenbuch")
        return 0

    try:
        ergebnis = sicherung.zurueckspielen(quelle, ziel)
    except sicherung.SicherungFehler as fehler:
        print("EINSPIELEN FEHLGESCHLAGEN: %s" % fehler, file=sys.stderr)
        return 1

    print("\nEingespielt nach %s." % ergebnis["ziel"])
    print("Das System steht jetzt auf dem Stand vom %s. Der Vorgang steht im "
          "Protokoll." % ergebnis["stand"])
    if ergebnis["beiseite"]:
        print("Der Stand von vorher liegt als %s daneben. Er enthält "
              "personenbezogene Daten — nach der Prüfung löschen."
              % os.path.basename(ergebnis["beiseite"]))
    print("Fotos sind nicht Teil der Sicherung, die stehen weiterhin in betrieb/fotos.")
    print("Jetzt wieder starten: sudo systemctl start hallenbuch")
    return 0


if __name__ == "__main__":
    sys.exit(main())

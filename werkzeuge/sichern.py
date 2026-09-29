#!/usr/bin/env python3
"""Sicherung anlegen und prüfen. Für den nächtlichen Cron-Eintrag.

  0 3 * * * cd /opt/hallenbuch && /usr/bin/python3 werkzeuge/sichern.py

Endet mit Rückgabewert 1, wenn die Sicherung nicht angelegt oder nicht gelesen
werden konnte. Cron schickt dann eine Mail — und genau das soll es auch.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hallenbuch import datenbank, sicherung  # noqa: E402


def main():
    quelle = os.environ.get("HALLENBUCH_DB", datenbank.PFAD)
    ordner = os.path.join(os.path.dirname(quelle), "sicherungen")
    if not os.path.isfile(quelle):
        print("Keine Datenbank unter %s" % quelle, file=sys.stderr)
        return 1
    try:
        ergebnis = sicherung.anlegen_und_pruefen(quelle, ordner)
    except sicherung.SicherungFehler as fehler:
        print("SICHERUNG FEHLGESCHLAGEN: %s" % fehler, file=sys.stderr)
        return 1
    print("%s  %d kB  geprüft" % (ergebnis["datei"], ergebnis["groesse"] // 1024))
    print("  " + ", ".join("%s %d" % (k, v) for k, v in ergebnis["zeilen"].items()))
    if ergebnis["aufgeraeumt"]:
        print("  %d alte Sicherungen weggeräumt" % ergebnis["aufgeraeumt"])
    # Die Fotos liegen daneben und sind NICHT Teil dieser Datei. Wer nur die
    # Sicherungen mitnimmt, hat im Ernstfall alle Belegbilder verloren.
    fotos = os.path.join(os.path.dirname(quelle), "fotos")
    if os.path.isdir(fotos):
        anzahl = len([n for n in os.listdir(fotos) if not n.startswith(".")])
        print("  Hinweis: %d Fotos in betrieb/fotos sind NICHT in dieser Datei. "
              "Wöchentlich den ganzen Ordner betrieb/ mitnehmen." % anzahl)

    # Eine volle Platte trifft zuerst die Halle: Dann schlaegt das Erfassen
    # fehl. Besser, der Cron meldet es Wochen vorher.
    raum = sicherung.platz(ordner)
    print("  Platte: %d von %d MB frei (%s %%)"
          % (raum["frei_mb"], raum["gesamt_mb"], raum["anteil"]))
    if raum["knapp"]:
        # Auf Fehlerkanal, damit Cron eine Mail schickt. Der Rückgabewert bleibt
        # 0: Die Sicherung ist gelungen, das ist eine Warnung, kein Fehlschlag.
        print("PLATTE WIRD KNAPP: nur noch %s %% frei. Fotos und Sicherungen "
              "wachsen weiter — bitte aufräumen oder vergrössern."
              % raum["anteil"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

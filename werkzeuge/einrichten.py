#!/usr/bin/env python3
"""Erste Einrichtung: einen Büro-Zugang anlegen, wahlweise Beispieldaten.

  python3 werkzeuge/einrichten.py --name "Achmed" --anmeldename achmed
  python3 werkzeuge/einrichten.py --beispiel        (nur zum Ausprobieren)

Das Passwort wird **erfragt**, nicht als Argument übergeben. Auf der
Befehlszeile stünde es sonst in der Shell-Historie des Servers und wäre für
jeden sichtbar, der `ps` aufruft, während der Befehl läuft. `--passwort` gibt
es weiterhin für Skripte, mit Warnung.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hallenbuch import datenbank, sicherheit  # noqa: E402

BEISPIEL_HAEUSER = [
    ("Autohaus Nordstadt", "NORD", "Industriestr. 12", "38228", "Salzgitter", ""),
    ("Autohaus Lebenstedt", "LEBE", "Am Ring 4", "38226", "Salzgitter", ""),
    ("Autozentrum Thiede", "THIE", "Hauptstr. 88", "38239", "Salzgitter", ""),
]
BEISPIEL_LEISTUNGEN = [
    ("Vollaufbereitung", 14900), ("Innenreinigung", 8900),
    ("Außenwäsche und Politur", 6900), ("Nachbehandlung", 4500),
]


def main():
    zerleger = argparse.ArgumentParser()
    zerleger.add_argument("--name")
    zerleger.add_argument("--anmeldename")
    zerleger.add_argument("--passwort",
                          help="nur für Skripte; landet in der Shell-Historie")
    zerleger.add_argument("--rolle", default="buero", choices=["buero", "erfasser"])
    zerleger.add_argument("--beispiel", action="store_true",
                          help="Beispiel-Autohäuser und -Leistungen anlegen")
    zerleger.add_argument("--db", default=None)
    args = zerleger.parse_args()

    verb = datenbank.verbinden(args.db)
    datenbank.aufbauen(verb)

    if args.anmeldename:
        passwort = args.passwort
        if passwort:
            print("Hinweis: Ein Passwort auf der Befehlszeile steht in der "
                  "Shell-Historie. Besser ohne --passwort aufrufen.")
        else:
            passwort = getpass.getpass("Passwort für %s: " % args.anmeldename)
            if passwort != getpass.getpass("Noch einmal: "):
                sys.exit("Die beiden Eingaben sind nicht gleich.")
        if len(passwort or "") < 8:
            sys.exit("Passwort muss mindestens 8 Zeichen haben.")
        try:
            verb.execute(
                "INSERT INTO benutzer (name, anmeldename, passwort, rolle, angelegt_am)"
                " VALUES (?,?,?,?,?)",
                (args.name or args.anmeldename, args.anmeldename.strip().lower(),
                 sicherheit.passwort_hashen(passwort), args.rolle, datenbank.jetzt()))
        except sqlite3.IntegrityError:
            sys.exit("Den Anmeldenamen %s gibt es schon." % args.anmeldename)
        print("Zugang angelegt: %s (%s)" % (args.anmeldename, args.rolle))

    if args.beispiel:
        for name, kurz, strasse, plz, ort, referenz in BEISPIEL_HAEUSER:
            verb.execute(
                "INSERT OR IGNORE INTO autohaus (name, kurz, strasse, plz, ort, kaeuferreferenz)"
                " VALUES (?,?,?,?,?,?)", (name, kurz, strasse, plz, ort, referenz))
        for i, (bezeichnung, preis) in enumerate(BEISPIEL_LEISTUNGEN, start=1):
            verb.execute(
                "INSERT OR IGNORE INTO leistung (bezeichnung, netto_cent, sortierung)"
                " VALUES (?,?,?)", (bezeichnung, preis, i))
        print("Beispieldaten angelegt: %d Autohäuser, %d Leistungen"
              % (len(BEISPIEL_HAEUSER), len(BEISPIEL_LEISTUNGEN)))

    anzahl = verb.execute("SELECT COUNT(*) FROM benutzer").fetchone()[0]
    if not anzahl:
        print("Achtung: Es gibt noch keinen Zugang. Ohne Zugang kommt niemand hinein.")
    verb.close()


if __name__ == "__main__":
    main()

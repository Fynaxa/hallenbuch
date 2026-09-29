#!/usr/bin/env python3
"""Startet das Hallenbuch.

  python3 start.py                 Port 8080, Datenbank in betrieb/
  HALLENBUCH_PORT=9000 python3 start.py

Ohne LEXWARE_SCHLUESSEL laeuft alles im Probebetrieb: Belege werden vollstaendig
aufgebaut, aber nicht ausgestellt.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hallenbuch import server  # noqa: E402

if __name__ == "__main__":
    if not os.environ.get("HALLENBUCH_GEHEIMNIS") \
            and os.environ.get("HALLENBUCH_ENTWICKLUNG") != "1":
        sys.exit(
            "HALLENBUCH_GEHEIMNIS ist nicht gesetzt.\n"
            "Ohne diesen Wert liessen sich Anmeldungen faelschen, deshalb startet\n"
            "der Dienst nicht. Einmal erzeugen und in die .env eintragen:\n"
            '  python3 -c "import secrets; print(secrets.token_hex(32))"\n'
            "Nur zum Entwickeln: HALLENBUCH_ENTWICKLUNG=1")
    if not os.environ.get("LEXWARE_SCHLUESSEL"):
        print("Hinweis: Kein Lexware-Zugang hinterlegt, es laeuft im Probebetrieb.")
    server.starten(server.port_lesen(os.environ.get("HALLENBUCH_PORT")))

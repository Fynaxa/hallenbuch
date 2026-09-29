"""Aufbau fuer die Tests: eine frische Datenbank mit echten Stammdaten-Formen."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hallenbuch import datenbank, fahrzeuge  # noqa: E402

FINS = [
    "WVWZZZ1JZ3W386752", "WAUZZZ8V1JA123456", "WBA5R71040FH12345",
    "W1K2130461A123456", "VF3LBHYBWKS123456", "TMBJJ7NE2J0123456",
    "ZFA31200000123456", "SB1KZ3JE60F123456", "JTDKB20U703123456",
    "KNADN512BJ6123456", "VSSZZZ6JZAR123456", "WF0AXXGCDA1A12345",
    "YV1RS58D912123456", "SJNFAAJ11U1234567", "WMWXM51040TX12345",
    "VNKKTUD310A123456", "TRUZZZ8N021123456", "WDD1690321J123456",
    "WVGZZZ5NZ8W123456", "W0LPD6EE5A1123456", "ZAR93900007123456",
    "SALLDHMP7AA123456", "VF7DDRHKC12345678", "WP0ZZZ99Z9S123456",
    "WAUZZZF24JN123456",
]


def frisch(anzahl_autohaeuser: int = 2, anzahl_leistungen: int = 3):
    verb = datenbank.verbinden(":memory:")
    datenbank.aufbauen(verb)
    verb.execute(
        "INSERT INTO benutzer (id,name,anmeldename,passwort,rolle,angelegt_am)"
        " VALUES (1,'Achmed','achmed','x','buero',?)", (datenbank.jetzt(),))
    verb.execute(
        "INSERT INTO benutzer (id,name,anmeldename,passwort,rolle,angelegt_am)"
        " VALUES (2,'Halle 1','halle1','x','erfasser',?)", (datenbank.jetzt(),))
    for i in range(1, anzahl_autohaeuser + 1):
        # Mit Kaeuferreferenz kommt zwingend ein Kontakt im Rechnungsprogramm:
        # Lexware nimmt die Referenz sonst nicht an (HTTP 406, belegt im echten
        # Ausstelllauf am 23.09.). Die Testdaten bilden das nach.
        verb.execute(
            "INSERT INTO autohaus (id,name,kurz,strasse,plz,ort,kaeuferreferenz,"
            " lexware_kontakt,zahlungsziel)"
            " VALUES (?,?,?,?,?,?,?,?,14)",
            (i, "Autohaus %d" % i, "AH%d" % i, "Musterweg %d" % i, "4%04d" % i,
             "Ort %d" % i, "04011000-1234512345-%02d" % i,
             "kontakt-%08d-0000-0000-0000-000000000000" % i))
    preise = [14900, 8900, 4500]
    for i in range(1, anzahl_leistungen + 1):
        verb.execute(
            "INSERT INTO leistung (id,bezeichnung,netto_cent,sortierung)"
            " VALUES (?,?,?,?)",
            (i, ["Vollaufbereitung", "Innenreinigung", "Politur"][i - 1],
             preise[i - 1], i))
    return verb


def benutzer(verb, kennung: int = 1):
    return verb.execute("SELECT * FROM benutzer WHERE id=?", (kennung,)).fetchone()


def fin_nummer(i: int) -> str:
    """Eindeutige, formal gueltige FIN fuer Testmengen beliebiger Groesse."""
    grund = FINS[i % len(FINS)]
    block = "%05d" % (i // len(FINS))
    return grund[:12] + block


def erfassen_viele(verb, nutzer, anzahl: int, autohaus_id: int = 1,
                   leistung_id: int = 1, tag: str = "2026-09-21", ab: int = 0):
    gemacht = []
    for i in range(anzahl):
        zeile, _ = fahrzeuge.erfassen(
            verb, nutzer, fin_nummer(ab + i), autohaus_id, leistung_id, tag,
            vorgang="v-%d-%d-%d" % (autohaus_id, leistung_id, ab + i))
        gemacht.append(zeile)
    return gemacht

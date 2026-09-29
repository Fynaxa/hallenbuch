"""Fertige Zeiträume für die Abrechnung.

Die Abrechnungswoche zählt nach dem Fertigstellungsdatum (so steht es im
Lieferschnitt), und sie läuft von Montag bis Sonntag. Wer am Montagvormittag
die vergangene Woche abrechnet, will „letzte Woche" antippen und nicht zwei
Daten tippen.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

SCHLUESSEL = ("diese-woche", "letzte-woche", "dieser-monat", "letzter-monat")
BESCHRIFTUNG = {
    "diese-woche": "Diese Woche",
    "letzte-woche": "Letzte Woche",
    "dieser-monat": "Dieser Monat",
    "letzter-monat": "Letzter Monat",
}


def _woche(tag: date):
    montag = tag - timedelta(days=tag.weekday())
    return montag, montag + timedelta(days=6)


def _monat(jahr: int, monat: int):
    letzter = calendar.monthrange(jahr, monat)[1]
    return date(jahr, monat, 1), date(jahr, monat, letzter)


def aufloesen(schluessel: str, heute: date = None):
    """Gibt (von, bis) als ISO-Datum zurück, oder None bei unbekanntem Schlüssel."""
    heute = heute or date.today()
    if schluessel == "diese-woche":
        von, bis = _woche(heute)
    elif schluessel == "letzte-woche":
        von, bis = _woche(heute - timedelta(days=7))
    elif schluessel == "dieser-monat":
        von, bis = _monat(heute.year, heute.month)
    elif schluessel == "letzter-monat":
        vormonat = date(heute.year, heute.month, 1) - timedelta(days=1)
        von, bis = _monat(vormonat.year, vormonat.month)
    else:
        return None
    return von.isoformat(), bis.isoformat()


def alle(heute: date = None):
    """[(schluessel, beschriftung, von, bis)] in der Reihenfolge der Knöpfe."""
    heute = heute or date.today()
    ergebnis = []
    for schluessel in SCHLUESSEL:
        von, bis = aufloesen(schluessel, heute)
        ergebnis.append((schluessel, BESCHRIFTUNG[schluessel], von, bis))
    return ergebnis


def passend(von: str, bis: str, heute: date = None):
    """Welcher Knopf ist gerade aktiv? Leer, wenn es ein eigener Zeitraum ist."""
    for schluessel, _, a, b in alle(heute):
        if a == von and b == bis:
            return schluessel
    return ""

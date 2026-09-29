"""Bremse gegen das Durchprobieren von Passwörtern.

Absichtlich **keine harte Sperre des Zugangs**: In der Halle steht sonst jemand
mit nassen Händen vor dem Telefon und kann nicht arbeiten, weil er sich dreimal
vertippt hat. Stattdessen ein gleitendes Fenster — nach ein paar Fehlversuchen
ein paar Minuten Pause, danach geht es von selbst weiter.

Gezählt wird doppelt:
  * je Anmeldename  — schützt das einzelne Konto
  * je Herkunft     — schützt gegen jemanden, der viele Namen durchprobiert
"""
from __future__ import annotations

import time

VERSUCHE_JE_NAME = 5
VERSUCHE_JE_HERKUNFT = 20
FENSTER_SEKUNDEN = 15 * 60
AUFBEWAHREN_SEKUNDEN = 7 * 24 * 3600


def _seit(jetzt=None) -> int:
    return int(jetzt or time.time()) - FENSTER_SEKUNDEN


def vermerken(verb, anmeldename: str, herkunft: str, erfolg: bool,
              jetzt=None) -> None:
    verb.execute(
        "INSERT INTO anmeldeversuch (wann, anmeldename, herkunft, erfolg)"
        " VALUES (?,?,?,?)",
        (int(jetzt or time.time()), (anmeldename or "").strip().lower(),
         herkunft or "", 1 if erfolg else 0))
    if erfolg:
        # Wer drin ist, fängt bei null an.
        verb.execute(
            "DELETE FROM anmeldeversuch WHERE anmeldename=? AND erfolg=0",
            ((anmeldename or "").strip().lower(),))


def fehlversuche(verb, anmeldename: str = None, herkunft: str = None,
                 jetzt=None) -> int:
    bedingung, werte = ["erfolg=0", "wann >= ?"], [_seit(jetzt)]
    if anmeldename is not None:
        bedingung.append("anmeldename=?")
        werte.append((anmeldename or "").strip().lower())
    if herkunft is not None:
        bedingung.append("herkunft=?")
        werte.append(herkunft)
    return verb.execute(
        "SELECT COUNT(*) FROM anmeldeversuch WHERE %s" % " AND ".join(bedingung),
        tuple(werte)).fetchone()[0]


def gesperrt(verb, anmeldename: str, herkunft: str, jetzt=None):
    """Gibt None zurück, wenn frei — sonst die Wartezeit in Minuten."""
    zu_viele = (fehlversuche(verb, anmeldename=anmeldename, jetzt=jetzt)
                >= VERSUCHE_JE_NAME)
    if not zu_viele and herkunft:
        zu_viele = (fehlversuche(verb, herkunft=herkunft, jetzt=jetzt)
                    >= VERSUCHE_JE_HERKUNFT)
    if not zu_viele:
        return None
    bedingung = "anmeldename=?" if fehlversuche(
        verb, anmeldename=anmeldename, jetzt=jetzt) >= VERSUCHE_JE_NAME else "herkunft=?"
    wert = (anmeldename or "").strip().lower() if "anmeldename" in bedingung else herkunft
    aeltester = verb.execute(
        "SELECT MIN(wann) FROM anmeldeversuch WHERE erfolg=0 AND wann >= ? AND %s"
        % bedingung, (_seit(jetzt), wert)).fetchone()[0]
    rest = (aeltester + FENSTER_SEKUNDEN) - int(jetzt or time.time())
    return max(1, (rest + 59) // 60)


def aufraeumen(verb, jetzt=None) -> int:
    grenze = int(jetzt or time.time()) - AUFBEWAHREN_SEKUNDEN
    zeiger = verb.execute("DELETE FROM anmeldeversuch WHERE wann < ?", (grenze,))
    return zeiger.rowcount

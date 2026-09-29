"""Passwörter ändern, Zugänge stilllegen und wieder öffnen.

Jede dieser Handlungen zählt den **Sitzungsstand** hoch. Damit werden alle
Kekse ungültig, die vorher ausgegeben wurden — sonst bliebe ein verlorenes
Handy angemeldet, obwohl das Büro das Passwort längst geändert hat.
"""
from __future__ import annotations

from . import datenbank, protokoll, sicherheit

MINDESTLAENGE = 8


class Abgelehnt(ValueError):
    pass


def _pruefe_neues(neu: str, wiederholung: str) -> None:
    if not neu or len(neu) < MINDESTLAENGE:
        raise Abgelehnt("Das neue Passwort braucht mindestens %d Zeichen."
                        % MINDESTLAENGE)
    if neu != wiederholung:
        raise Abgelehnt("Die beiden neuen Passwörter sind nicht gleich.")


def _neu_setzen(verb, benutzer_id: int, neu: str) -> int:
    """Setzt das Passwort und zählt den Sitzungsstand hoch. Gibt ihn zurück."""
    verb.execute(
        "UPDATE benutzer SET passwort=?, sitzung_stand=sitzung_stand+1 WHERE id=?",
        (sicherheit.passwort_hashen(neu), benutzer_id))
    return verb.execute(
        "SELECT sitzung_stand FROM benutzer WHERE id=?", (benutzer_id,)).fetchone()[0]


def eigenes_passwort_aendern(verb, benutzer, alt: str, neu: str, wiederholung: str) -> int:
    if not sicherheit.passwort_stimmt(alt or "", benutzer["passwort"]):
        raise Abgelehnt("Das bisherige Passwort stimmt nicht.")
    _pruefe_neues(neu, wiederholung)
    if neu == alt:
        raise Abgelehnt("Das neue Passwort ist das alte.")
    stand = _neu_setzen(verb, benutzer["id"], neu)
    protokoll.notieren(verb, benutzer, "Passwort geändert", benutzer["anmeldename"])
    return stand


def passwort_zuruecksetzen(verb, buero, benutzer_id: int, neu: str,
                           wiederholung: str) -> None:
    """Das Büro vergibt ein neues Passwort, etwa wenn ein Handy weg ist."""
    if buero["rolle"] != "buero":
        raise Abgelehnt("Passwörter setzt nur das Büro.")
    _pruefe_neues(neu, wiederholung)
    ziel = verb.execute("SELECT * FROM benutzer WHERE id=?", (benutzer_id,)).fetchone()
    if not ziel:
        raise Abgelehnt("Diesen Zugang gibt es nicht.")
    _neu_setzen(verb, benutzer_id, neu)
    protokoll.notieren(verb, buero, "Passwort zurückgesetzt", ziel["anmeldename"],
                       "alle bestehenden Anmeldungen beendet")


def _aktive_bueros(verb, ausser: int = None) -> int:
    bedingung = "rolle='buero' AND aktiv=1"
    werte = ()
    if ausser is not None:
        bedingung += " AND id<>?"
        werte = (ausser,)
    return verb.execute(
        "SELECT COUNT(*) FROM benutzer WHERE %s" % bedingung, werte).fetchone()[0]


def stilllegen(verb, buero, benutzer_id: int) -> None:
    """Zugang stilllegen.

    Zählen und Schreiben stehen in EINER Transaktion. Ohne sie könnten sich
    zwei Bürozugänge im selben Moment gegenseitig stilllegen: Beide sähen den
    jeweils anderen als aktiv, beide kämen durch, und danach käme niemand mehr
    hinein — das liesse sich nur noch von Hand in der Datenbank reparieren.
    """
    if buero["rolle"] != "buero":
        raise Abgelehnt("Zugänge legt nur das Büro still.")
    if int(benutzer_id) == int(buero["id"]):
        raise Abgelehnt("Den eigenen Zugang kann man nicht stilllegen. "
                        "Sonst kommt niemand mehr hinein.")
    verb.execute("BEGIN IMMEDIATE")
    try:
        ziel = verb.execute("SELECT * FROM benutzer WHERE id=?", (benutzer_id,)).fetchone()
        if not ziel:
            raise Abgelehnt("Diesen Zugang gibt es nicht.")
        if not ziel["aktiv"]:
            raise Abgelehnt("Dieser Zugang ist schon stillgelegt.")
        if ziel["rolle"] == "buero" and _aktive_bueros(verb, ausser=benutzer_id) == 0:
            raise Abgelehnt("Das ist der letzte Zugang mit Bürorechten. "
                            "Erst einen zweiten anlegen, dann diesen stilllegen.")
        verb.execute(
            "UPDATE benutzer SET aktiv=0, sitzung_stand=sitzung_stand+1 WHERE id=?",
            (benutzer_id,))
        protokoll.notieren(verb, buero, "Zugang stillgelegt", ziel["anmeldename"],
                           "bestehende Anmeldungen beendet")
        verb.execute("COMMIT")
    except Exception:
        verb.execute("ROLLBACK")
        raise


def aktivieren(verb, buero, benutzer_id: int) -> None:
    if buero["rolle"] != "buero":
        raise Abgelehnt("Zugänge öffnet nur das Büro.")
    ziel = verb.execute("SELECT * FROM benutzer WHERE id=?", (benutzer_id,)).fetchone()
    if not ziel:
        raise Abgelehnt("Diesen Zugang gibt es nicht.")
    verb.execute("UPDATE benutzer SET aktiv=1 WHERE id=?", (benutzer_id,))
    protokoll.notieren(verb, buero, "Zugang wieder geöffnet", ziel["anmeldename"])

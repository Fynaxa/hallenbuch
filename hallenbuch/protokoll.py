"""Wer wann was. Wird nie geloescht und nie geaendert."""
from __future__ import annotations

from . import datenbank


def notieren(verb, benutzer, was: str, gegenstand: str = "", einzelheiten: str = "") -> None:
    verb.execute(
        "INSERT INTO protokoll (zeit, benutzer_id, benutzer_name, was, gegenstand, einzelheiten)"
        " VALUES (?,?,?,?,?,?)",
        (datenbank.jetzt(),
         benutzer["id"] if benutzer else None,
         benutzer["name"] if benutzer else "System",
         was, gegenstand, einzelheiten))


def lesen(verb, grenze: int = 200):
    return verb.execute(
        "SELECT * FROM protokoll ORDER BY id DESC LIMIT ?", (grenze,)).fetchall()

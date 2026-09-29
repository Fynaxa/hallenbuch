"""Passwoerter, Sitzungen, Rollen. Reine Standardbibliothek."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

ITERATIONEN = 200000
SITZUNG_STUNDEN = 12


class KeinGeheimnis(RuntimeError):
    pass


def _geheimnis() -> bytes:
    """Der Schlüssel, mit dem Sitzungs-Kekse unterschrieben werden.

    **Kein Rückfall auf einen festen Wert.** Ein im Quelltext stehendes
    Ersatzgeheimnis wäre kein Geheimnis: Wer es kennt, baut sich einen gültigen
    Keks für das Bürokonto und ist drin. Eine blosse Warnung beim Hochfahren
    reicht nicht, die liest im systemd-Log niemand. Fehlt der Wert, läuft hier
    nichts.

    Für die Entwicklung und für Tests gibt es HALLENBUCH_ENTWICKLUNG=1. Das ist
    eine bewusste Ansage und kann nicht aus Versehen passieren.
    """
    wert = os.environ.get("HALLENBUCH_GEHEIMNIS", "")
    if wert:
        return wert.encode("utf-8")
    if os.environ.get("HALLENBUCH_ENTWICKLUNG") == "1":
        return b"nur-fuer-entwicklung-niemals-im-betrieb"
    raise KeinGeheimnis(
        "HALLENBUCH_GEHEIMNIS ist nicht gesetzt. Ohne diesen Wert liessen sich "
        "Anmeldungen fälschen. Einmal erzeugen und in die .env eintragen:\n"
        '  python3 -c "import secrets; print(secrets.token_hex(32))"')


def passwort_hashen(passwort: str) -> str:
    salz = secrets.token_bytes(16)
    roh = hashlib.pbkdf2_hmac("sha256", passwort.encode("utf-8"), salz, ITERATIONEN)
    return "pbkdf2$%d$%s$%s" % (
        ITERATIONEN,
        base64.b64encode(salz).decode(),
        base64.b64encode(roh).decode(),
    )


def passwort_stimmt(passwort: str, gespeichert: str) -> bool:
    try:
        art, runden, salz_b64, soll_b64 = gespeichert.split("$")
        if art != "pbkdf2":
            return False
        roh = hashlib.pbkdf2_hmac(
            "sha256", passwort.encode("utf-8"),
            base64.b64decode(salz_b64), int(runden))
        return hmac.compare_digest(roh, base64.b64decode(soll_b64))
    except Exception:
        return False


# Ein Hash, gegen den bei unbekanntem Namen gerechnet wird. Ohne ihn antwortet
# der Server bei einem unbekannten Namen messbar schneller als bei einem
# bekannten mit falschem Passwort — daran liest man ab, welche Namen es gibt.
_LEERLAUF = passwort_hashen("nur-zum-zeitverbrauch")


def leerlauf() -> None:
    passwort_stimmt("nur-zum-zeitverbrauch", _LEERLAUF)


def sitzung_ausstellen(benutzer_id: int, stand: int = 1) -> str:
    """Der Sitzungsstand steht mit im Keks.

    Damit endet eine Sitzung wirklich, wenn das Passwort neu gesetzt oder der
    Zugang stillgelegt wird. Ohne ihn bliebe der Keks auf einem verlorenen
    Handy gültig, obwohl das Büro das Passwort längst geändert hat.
    """
    ablauf = int(time.time()) + SITZUNG_STUNDEN * 3600
    nutzlast = "%d:%d:%d" % (benutzer_id, int(stand), ablauf)
    unterschrift = hmac.new(_geheimnis(), nutzlast.encode(), hashlib.sha256).hexdigest()
    return "%s:%s" % (nutzlast, unterschrift)


def sitzung_pruefen(keks: str):
    """Gibt (Benutzer-ID, Sitzungsstand) zurück oder None."""
    if not keks:
        return None
    try:
        benutzer_id, stand, ablauf, unterschrift = keks.split(":")
        nutzlast = "%s:%s:%s" % (benutzer_id, stand, ablauf)
        soll = hmac.new(_geheimnis(), nutzlast.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(soll, unterschrift):
            return None
        if int(ablauf) < time.time():
            return None
        return int(benutzer_id), int(stand)
    except Exception:
        return None

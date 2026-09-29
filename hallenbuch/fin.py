"""FIN-Behandlung.

Bindend aus der Bauliste: Die Prüfziffer an Stelle 9 steht in 49 CFR 565.15
(Nordamerika), NICHT in ISO 3779:2009. Wer sie bei europaeischen Fahrzeugen als
Ablehnungsgrund einbaut, sperrt gueltige Fahrzeuge aus. Sie ist hier deshalb
ausschliesslich ein Hinweis, niemals eine Sperre.
"""
from __future__ import annotations

import re

LAENGE = 17

# I, O und Q sind in einer FIN nicht zulaessig (ISO 3779). Genau deshalb ist die
# Ersetzung sicher: Wer sie liest, hat sich verlesen oder die Texterkennung hat
# sich verlesen. 1/0 sind die einzigen sinnvollen Deutungen.
VERWECHSLUNG = {"I": "1", "O": "0", "Q": "0"}

ERLAUBT = set("ABCDEFGHJKLMNPRSTUVWXYZ0123456789")

# Gewichte und Zahlwerte nach 49 CFR 565.15 (nur fuer den Hinweis).
_GEWICHT = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
_WERT = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}


class FinFehler(ValueError):
    """Die Eingabe kann keine FIN sein."""


def normalisieren(roh: str) -> str:
    """Trimmt, macht groß, wirft Trenner weg und ersetzt I/O/Q.

    Wirft FinFehler nur bei Laenge oder verbotenen Zeichen, nie wegen der
    Prüfziffer.
    """
    if roh is None:
        raise FinFehler("Keine FIN angegeben.")
    text = re.sub(r"[\s\-_.]", "", str(roh)).upper()
    text = "".join(VERWECHSLUNG.get(z, z) for z in text)
    if len(text) != LAENGE:
        raise FinFehler(
            "Eine FIN hat 17 Zeichen, diese hat %d: %s" % (len(text), text or "leer")
        )
    ungueltig = sorted({z for z in text if z not in ERLAUBT})
    if ungueltig:
        raise FinFehler("Unerlaubte Zeichen in der FIN: %s" % " ".join(ungueltig))
    return text


def ist_nordamerikanisch(fin: str) -> bool:
    """Nur für diese Fahrzeuge ist die Prüfziffer überhaupt vorgeschrieben."""
    return bool(fin) and fin[0] in "12345"


def pruefziffer_stimmt(fin: str) -> bool:
    summe = sum(_WERT.get(z, int(z) if z.isdigit() else 0) * g
                for z, g in zip(fin, _GEWICHT))
    rest = summe % 11
    soll = "X" if rest == 10 else str(rest)
    return fin[8] == soll


def hinweis(fin: str) -> str:
    """Leerer String heisst: nichts anzumerken. Nie ein Grund zum Ablehnen."""
    if not ist_nordamerikanisch(fin):
        return ""
    if pruefziffer_stimmt(fin):
        return ""
    return ("Prüfziffer passt nicht. Bei US-Fahrzeugen ist das ein Tippfehler-Verdacht, "
            "kein Hindernis. Bitte einmal gegenlesen.")


def gruppiert(fin: str) -> str:
    """WMI, VDS, VIS getrennt, damit das Auge 17 Zeichen vergleichen kann."""
    if len(fin) != LAENGE:
        return fin
    return "%s %s %s" % (fin[0:3], fin[3:9], fin[9:17])


def aus_text(roh):
    """Sucht eine FIN in einem Texterkennungsergebnis.

    Nicht so einfach, wie es aussieht. Der erste Anlauf klebte alle Zeichen
    zusammen und nahm die ersten 17 erlaubten. Aus

        MERCEDES-BENZ
        FAHRZEUG-IDENT-NR.
        WDD I69O32 1J1OOOOO

    wurde dabei "MERCEDESBENZFAHRZ" — eine erfundene FIN, die niemandem
    auffällt, bis sie auf einer Rechnung steht.

    Darum jetzt: **zeilenweise** arbeiten, in Wörter zerlegen, und nur
    zusammenhängende Wörter zusammenfassen, die zusammen GENAU 17 erlaubte
    Zeichen ergeben. Die Zeile mit dem zulässigen Gesamtgewicht hat 18 und
    fällt damit heraus. Kommen mehrere Treffer in Frage, gewinnt der erste.
    Gibt es keinen, wird nichts geraten, sondern None zurückgegeben.
    """
    if not roh:
        return None
    for zeile in str(roh).splitlines():
        woerter = [
            "".join(VERWECHSLUNG.get(z, z) for z in stueck.upper())
            for stueck in re.split(r"[^A-Za-z0-9]+", zeile) if stueck
        ]
        woerter = [w for w in woerter if w and all(z in ERLAUBT for z in w)]
        for anfang in range(len(woerter)):
            laenge = 0
            for ende in range(anfang, len(woerter)):
                laenge += len(woerter[ende])
                if laenge == LAENGE:
                    return "".join(woerter[anfang:ende + 1])
                if laenge > LAENGE:
                    break
    return None

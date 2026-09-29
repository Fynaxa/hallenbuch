"""Stammdaten ändern.

Bis zum 22.09. liessen sich Autohäuser und Leistungen **anlegen, aber nie
korrigieren** — im ganzen Code stand kein einziges `UPDATE autohaus`. Ein
Tippfehler in der Anschrift stand damit auf jeder Rechnung an dieses Haus, und
§ 14 Abs. 4 UStG verlangt dort die richtige. Geradeziehen ging nur mit SQL auf
dem Server.

Ausserdem waren `filiale_von` und `sammeln_mit_hauptsitz` über die Oberfläche
nicht erreichbar: Die Filial-Abrechnung war gebaut, getestet und in der
Anleitung beschrieben, aber niemand konnte sie einschalten.
"""
from __future__ import annotations

import sqlite3

from . import datenbank, protokoll


class Abgelehnt(ValueError):
    pass


def _pflicht(wert: str, name: str) -> str:
    sauber = str(wert or "").strip()
    if not sauber:
        raise Abgelehnt("%s darf nicht leer sein." % name)
    return sauber


def _nur_buero(benutzer):
    if benutzer["rolle"] != "buero":
        raise Abgelehnt("Stammdaten ändert nur das Büro.")



# Die Spalten, aus denen die Bearbeitungsformulare gebaut werden. Sie kommen als
# `alt_*` wieder mit zurueck und werden daraus zum Waechter im Schreibbefehl.
STAND_AUTOHAUS = ("name", "kurz", "strasse", "plz", "ort", "kaeuferreferenz",
                  "lieferantennummer", "lexware_kontakt", "zahlungsziel",
                  "filiale_von", "sammeln_mit_hauptsitz", "aktiv")
STAND_LEISTUNG = ("bezeichnung", "netto_cent", "sortierung", "aktiv")


def _stand_bedingung(felder, spalten):
    """Aus dem mitgeschickten Stand einen Waechter IM Schreibbefehl bauen.

    Im Buero sitzen zwei Leute. Beide haben die Stammdaten offen. Sie erhoeht den
    Preis, er korrigiert danach einen Tippfehler im Namen — sein Formular traegt
    aber noch den ALTEN Preis, und das UPDATE schrieb ihn zurueck. Die Erhoehung
    war still weg, und jedes weitere Fahrzeug wurde zum alten Preis erfasst.

    Eine Pruefung vor dem Schreiben hilft dagegen nicht: Zwischen Lesen und
    Schreiben liegt sonst wieder eine Luecke. Der Vergleich gehoert deshalb in
    dieselbe Anweisung. Verglichen wird als Text, damit `NULL`, Zahl und
    Zeichenkette gleich behandelt werden.

    Schickt ein Formular keinen Stand mit, gibt es keinen Waechter — sonst waere
    jeder Aufruf aus einem Skript heraus gesperrt.
    """
    teile, werte = [], []
    for spalte in spalten:
        schluessel = "alt_" + spalte
        if schluessel not in felder:
            return "", []
        teile.append(" AND IFNULL(CAST(%s AS TEXT),'')=?" % spalte)
        werte.append(str(felder[schluessel] if felder[schluessel] is not None else ""))
    return "".join(teile), werte


def _stand_gebrochen(zeile, felder, spalten):
    """Hat sich die Zeile seit dem Laden des Formulars geaendert?"""
    for spalte in spalten:
        schluessel = "alt_" + spalte
        if schluessel not in felder:
            return False
        jetzt = "" if zeile[spalte] is None else str(zeile[spalte])
        if jetzt != str(felder[schluessel] if felder[schluessel] is not None else ""):
            return True
    return False


def offene_fahrzeuge(verb, autohaus_id: int) -> int:
    """Offene Fahrzeuge dieses Autohauses **und** der Filialen, die mit ihm
    abgerechnet werden.

    Ohne die Filialen zählte die Prüfung nur das halbe Haus: Gemessen liess
    sich ein Hauptsitz stilllegen, während bei seiner Filiale drei Fahrzeuge
    offen waren. Danach zeigte die Bürotafel **nichts** mehr davon — weder den
    Hauptsitz (stillgelegt) noch die Filiale (wird beim Hauptsitz gezählt) —
    und 447 EUR standen nirgends mehr.
    """
    return verb.execute(
        "SELECT COUNT(*) FROM fahrzeug f"
        " WHERE f.status='offen'"
        "   AND (f.autohaus_id=?"
        "        OR f.autohaus_id IN (SELECT k.id FROM autohaus k"
        "                              WHERE k.filiale_von=? AND k.sammeln_mit_hauptsitz=1))",
        (autohaus_id, autohaus_id)).fetchone()[0]


def autohaus_aendern(verb, benutzer, autohaus_id: int, felder: dict):
    """Ändert ein Autohaus. Gibt die geänderten Felder zurück."""
    _nur_buero(benutzer)
    vorher = verb.execute("SELECT * FROM autohaus WHERE id=?", (autohaus_id,)).fetchone()
    if not vorher:
        raise Abgelehnt("Dieses Autohaus gibt es nicht.")

    name = _pflicht(felder.get("name"), "Der Name")
    kurz = _pflicht(felder.get("kurz"), "Das Kürzel").upper()
    aktiv = 1 if felder.get("aktiv") else 0
    hauptsitz = felder.get("filiale_von")
    hauptsitz = int(hauptsitz) if str(hauptsitz or "").strip().isdigit() else None
    sammeln = 1 if felder.get("sammeln_mit_hauptsitz") else 0

    if hauptsitz is not None:
        if hauptsitz == autohaus_id:
            raise Abgelehnt("Ein Autohaus kann keine Filiale von sich selbst sein.")
        ziel = verb.execute("SELECT * FROM autohaus WHERE id=?", (hauptsitz,)).fetchone()
        if not ziel:
            raise Abgelehnt("Den angegebenen Hauptsitz gibt es nicht.")
        if ziel["filiale_von"]:
            raise Abgelehnt(
                "%s ist selbst eine Filiale. Ketten über zwei Stufen rechnet das "
                "System nicht ab." % ziel["name"])
        eigene = verb.execute(
            "SELECT COUNT(*) FROM autohaus WHERE filiale_von=?", (autohaus_id,)).fetchone()[0]
        if eigene:
            raise Abgelehnt(
                "%s hat selbst Filialen und kann deshalb keine Filiale werden."
                % vorher["name"])
    else:
        sammeln = 0

    if not aktiv and offene_fahrzeuge(verb, autohaus_id):
        raise Abgelehnt(
            "Für %s sind noch %d Fahrzeuge offen. Erst abrechnen, dann "
            "stilllegen — sonst fallen sie aus jeder Übersicht heraus."
            % (vorher["name"], offene_fahrzeuge(verb, autohaus_id)))

    zahlungsziel = str(felder.get("zahlungsziel") or "").strip() or "14"
    if not zahlungsziel.isdigit() or not 0 <= int(zahlungsziel) <= 180:
        raise Abgelehnt("Das Zahlungsziel muss eine Zahl zwischen 0 und 180 sein.")

    neu = {
        "name": name, "kurz": kurz,
        "strasse": str(felder.get("strasse") or "").strip(),
        "plz": str(felder.get("plz") or "").strip(),
        "ort": str(felder.get("ort") or "").strip(),
        "kaeuferreferenz": str(felder.get("kaeuferreferenz") or "").strip(),
        "lieferantennummer": str(felder.get("lieferantennummer") or "").strip(),
        "lexware_kontakt": str(felder.get("lexware_kontakt") or "").strip(),
        "zahlungsziel": int(zahlungsziel),
        "filiale_von": hauptsitz, "sammeln_mit_hauptsitz": sammeln, "aktiv": aktiv,
    }
    # Der Wächter steht IM Schreibbefehl, nicht davor. Zwischen dem Zählen und
    # dem Stilllegen kann die Halle ein Fahrzeug erfassen — und ein Fahrzeug bei
    # einem stillgelegten Autohaus fällt aus JEDER Übersicht heraus und ist
    # nicht mehr abzurechnen. Eine Vorabprüfung allein verpasst genau das.
    bedingung = "" if aktiv else (
        " AND NOT EXISTS (SELECT 1 FROM fahrzeug f"
        "                  WHERE f.status='offen'"
        "                    AND (f.autohaus_id=autohaus.id"
        "                         OR f.autohaus_id IN ("
        "                              SELECT k.id FROM autohaus k"
        "                               WHERE k.filiale_von=autohaus.id"
        "                                 AND k.sammeln_mit_hauptsitz=1)))")
    stand, stand_werte = _stand_bedingung(felder, STAND_AUTOHAUS)
    try:
        zeiger = verb.execute(
            "UPDATE autohaus SET name=?, kurz=?, strasse=?, plz=?, ort=?,"
            " kaeuferreferenz=?, lieferantennummer=?, lexware_kontakt=?,"
            " zahlungsziel=?, filiale_von=?, sammeln_mit_hauptsitz=?, aktiv=?"
            " WHERE id=?" + bedingung + stand,
            [neu["name"], neu["kurz"], neu["strasse"], neu["plz"], neu["ort"],
             neu["kaeuferreferenz"], neu["lieferantennummer"], neu["lexware_kontakt"],
             neu["zahlungsziel"], neu["filiale_von"], neu["sammeln_mit_hauptsitz"],
             neu["aktiv"], autohaus_id] + stand_werte)
    except sqlite3.IntegrityError:
        raise Abgelehnt("Dieses Kürzel gibt es schon. Bitte ein anderes wählen.")
    if zeiger.rowcount != 1:
        # Zwei Gründe sind möglich, und sie verlangen Verschiedenes vom Büro.
        jetzt = verb.execute("SELECT * FROM autohaus WHERE id=?",
                             (autohaus_id,)).fetchone()
        if jetzt is not None and _stand_gebrochen(jetzt, felder, STAND_AUTOHAUS):
            raise Abgelehnt(
                "%s wurde inzwischen von jemand anderem geändert. Es wurde nichts "
                "überschrieben. Bitte die Seite neu laden und noch einmal ansehen."
                % vorher["name"])
        raise Abgelehnt(
            "Für %s ist zwischenzeitlich ein Fahrzeug erfasst worden. Es wurde "
            "nichts geändert." % vorher["name"])

    # Nicht nur WELCHE Felder sich geändert haben, sondern von was auf was.
    # „ort geändert" beantwortet die Frage nicht, die man später stellt.
    geaendert = [s for s in neu if str(vorher[s] or "") != str(neu[s] or "")]
    einzeln = ", ".join(
        "%s: %s -> %s" % (s, str(vorher[s] or "") or "leer", str(neu[s] or "") or "leer")
        for s in geaendert)
    protokoll.notieren(verb, benutzer, "Autohaus geändert", vorher["name"],
                       einzeln or "nichts")
    return geaendert


def leistung_aendern(verb, benutzer, leistung_id: int, felder: dict):
    """Ändert eine Leistung. Der Preis gilt ab jetzt; erfasste Fahrzeuge
    behalten den Preis, der beim Erfassen galt."""
    _nur_buero(benutzer)
    vorher = verb.execute("SELECT * FROM leistung WHERE id=?", (leistung_id,)).fetchone()
    if not vorher:
        raise Abgelehnt("Diese Leistung gibt es nicht.")

    bezeichnung = _pflicht(felder.get("bezeichnung"), "Die Bezeichnung")
    netto = datenbank.cent(felder.get("preis"))
    if netto < 0:
        raise Abgelehnt("Der Preis kann nicht negativ sein.")
    sortierung = str(felder.get("sortierung") or "0").strip()
    if not sortierung.lstrip("-").isdigit():
        raise Abgelehnt("Die Sortierung muss eine Zahl sein.")
    aktiv = 1 if felder.get("aktiv") else 0

    stand, stand_werte = _stand_bedingung(felder, STAND_LEISTUNG)
    zeiger = verb.execute(
        "UPDATE leistung SET bezeichnung=?, netto_cent=?, sortierung=?, aktiv=?"
        " WHERE id=?" + stand,
        [bezeichnung, netto, int(sortierung), aktiv, leistung_id] + stand_werte)
    if zeiger.rowcount != 1:
        raise Abgelehnt(
            "%s wurde inzwischen von jemand anderem geändert. Es wurde nichts "
            "überschrieben — sonst wäre zum Beispiel eine Preisänderung still "
            "wieder verschwunden. Bitte die Seite neu laden."
            % vorher["bezeichnung"])
    teile = []
    if bezeichnung != vorher["bezeichnung"]:
        teile.append("Bezeichnung: %s -> %s" % (vorher["bezeichnung"], bezeichnung))
    if netto != vorher["netto_cent"]:
        teile.append("Preis: %s -> %s EUR"
                     % (datenbank.euro(vorher["netto_cent"]), datenbank.euro(netto)))
    if aktiv != vorher["aktiv"]:
        teile.append("stillgelegt" if not aktiv else "wieder in Benutzung")
    protokoll.notieren(verb, benutzer, "Leistung geändert", vorher["bezeichnung"],
                       ", ".join(teile) or "nichts")
    return netto


def sonderpreise(verb):
    """Alle gesetzten Sonderpreise, mit dem Standardpreis daneben.

    Sie wurden bis zum 22.09. **nirgends angezeigt**: gelesen nur beim
    Erfassen, gesetzt über ein Formular, und danach unsichtbar. Wer sich einmal
    vertippt hatte, konnte es weder sehen noch rueckgaengig machen — und wenn
    spaeter der Standardpreis steigt, bleibt der alte Sonderpreis still
    bestehen und das Autohaus zahlt weiter zu wenig.
    """
    return verb.execute(
        "SELECT p.autohaus_id, p.leistung_id, p.netto_cent,"
        " a.name AS autohaus, l.bezeichnung AS leistung,"
        " l.netto_cent AS standard_cent"
        " FROM preis p"
        " JOIN autohaus a ON a.id=p.autohaus_id"
        " JOIN leistung l ON l.id=p.leistung_id"
        " ORDER BY a.name, l.sortierung, l.bezeichnung").fetchall()


def sonderpreis_entfernen(verb, benutzer, autohaus_id: int, leistung_id: int):
    """Nimmt den Sonderpreis weg. Ab dann gilt wieder der Standardpreis."""
    _nur_buero(benutzer)
    zeile = verb.execute(
        "SELECT p.netto_cent, a.name AS autohaus, l.bezeichnung AS leistung"
        " FROM preis p JOIN autohaus a ON a.id=p.autohaus_id"
        " JOIN leistung l ON l.id=p.leistung_id"
        " WHERE p.autohaus_id=? AND p.leistung_id=?",
        (autohaus_id, leistung_id)).fetchone()
    if not zeile:
        raise Abgelehnt("Diesen Sonderpreis gibt es nicht.")
    verb.execute("DELETE FROM preis WHERE autohaus_id=? AND leistung_id=?",
                 (autohaus_id, leistung_id))
    protokoll.notieren(
        verb, benutzer, "Sonderpreis entfernt",
        "%s / %s" % (zeile["autohaus"], zeile["leistung"]),
        "war %s EUR, ab jetzt gilt der Standardpreis" % datenbank.euro(zeile["netto_cent"]))

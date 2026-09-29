"""Erfassung am Fahrzeug.

Wichtig für die Halle: `vorgang` ist ein vom Handy erzeugter Schlüssel. Schickt
die Warteschlange denselben Vorgang nach einem Netzausfall ein zweites Mal, wird
er nicht doppelt angelegt, sondern der vorhandene zurückgegeben.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date

from . import datenbank, fin as finmodul, protokoll


class Abgelehnt(ValueError):
    pass


# Ein vertipptes Jahr (2027 statt 2026) parkt das Fahrzeug ausserhalb jedes
# Abrechnungszeitraums. Es taucht dann in keiner Sammelrechnung auf, und
# niemandem fällt es auf — der Wagen ist erfasst, aber nie berechnet.
TAGE_ZUKUNFT = 1
TAGE_VERGANGENHEIT = 90

ARTEN = ("aussen", "innen", "schaden")
ART_NAME = {"aussen": "Außen", "innen": "Innen", "schaden": "Schaden"}

MINDESTLAENGE_SUCHE = 3


def _tage_bis_heute(fertig_am: str):
    """Positiv heisst Zukunft, negativ Vergangenheit. None heisst unlesbar."""
    try:
        tag = date.fromisoformat(str(fertig_am)[:10])
    except (ValueError, TypeError):
        return None
    return (tag - date.today()).days


def preis_fuer(verb, autohaus_id: int, leistung_id: int) -> int:
    zeile = verb.execute(
        "SELECT netto_cent FROM preis WHERE autohaus_id=? AND leistung_id=?",
        (autohaus_id, leistung_id)).fetchone()
    if zeile:
        return int(zeile["netto_cent"])
    zeile = verb.execute(
        "SELECT netto_cent FROM leistung WHERE id=? AND aktiv=1", (leistung_id,)).fetchone()
    if not zeile:
        raise Abgelehnt("Diese Leistung gibt es nicht oder sie ist stillgelegt.")
    return int(zeile["netto_cent"])


def aehnliche_warnung(verb, fin_wert: str, autohaus_id: int, fertig_am: str) -> str:
    """Doppelerfassung ist ein Hinweis, keine Sperre. Ein Auto kann zweimal kommen."""
    zeile = verb.execute(
        "SELECT id, fertig_am FROM fahrzeug"
        " WHERE fin=? AND autohaus_id=? AND status='offen'"
        " AND date(fertig_am) BETWEEN date(?, '-6 day') AND date(?, '+6 day')"
        " ORDER BY id DESC LIMIT 1",
        (fin_wert, autohaus_id, fertig_am, fertig_am)).fetchone()
    if zeile:
        return ("Diese FIN ist am %s schon offen erfasst (Nr. %d). Wenn das Fahrzeug wirklich "
                "zweimal da war, ist alles in Ordnung." % (zeile["fertig_am"][:10], zeile["id"]))
    return ""


def erfassen(verb, benutzer, fin_roh: str, autohaus_id: int, leistung_id: int,
             fertig_am: str, kennzeichen: str = "", notiz: str = "",
             vorgang: str = None):
    """Legt ein Fahrzeug an. Gibt (zeile, hinweise) zurück."""
    fin_wert = finmodul.normalisieren(fin_roh)

    if vorgang:
        vorhanden = verb.execute(
            "SELECT * FROM fahrzeug WHERE vorgang=?", (vorgang,)).fetchone()
        if vorhanden:
            return vorhanden, ["War schon erfasst, nichts doppelt angelegt."]

    haus = verb.execute(
        "SELECT * FROM autohaus WHERE id=? AND aktiv=1", (autohaus_id,)).fetchone()
    if not haus:
        raise Abgelehnt("Dieses Autohaus gibt es nicht oder es ist stillgelegt.")
    # Eine Filiale, die mit dem Hauptsitz abgerechnet wird, kann nicht arbeiten,
    # wenn der Hauptsitz stillgelegt ist: Ihre Fahrzeuge stünden auf keiner
    # Übersicht mehr, weil die Filiale beim Hauptsitz gezählt wird.
    if haus["filiale_von"] and haus["sammeln_mit_hauptsitz"]:
        sitz = verb.execute("SELECT * FROM autohaus WHERE id=? AND aktiv=1",
                            (haus["filiale_von"],)).fetchone()
        if not sitz:
            raise Abgelehnt(
                "Der Hauptsitz von %s ist stillgelegt. Solange das so ist, kann "
                "für diese Filiale nichts erfasst werden." % haus["name"])

    abstand = _tage_bis_heute(fertig_am)
    if abstand is None:
        raise Abgelehnt("Das Fertigstellungsdatum ist nicht lesbar: %s" % fertig_am)
    if abstand > TAGE_ZUKUNFT:
        raise Abgelehnt(
            "Fertig am %s liegt in der Zukunft. Vermutlich ein Tippfehler im "
            "Jahr — ein Fahrzeug mit einem falschen Datum landet in keiner "
            "Sammelrechnung und wird nie berechnet." % fertig_am)

    netto = preis_fuer(verb, autohaus_id, leistung_id)
    hinweise = []
    if abstand < -TAGE_VERGANGENHEIT:
        hinweise.append(
            "Fertig am %s liegt %d Tage zurück. Wenn das stimmt, ist alles gut — "
            "sonst bitte das Datum prüfen." % (fertig_am, -abstand))
    marke = finmodul.hinweis(fin_wert)
    if marke:
        hinweise.append(marke)
    doppelt = aehnliche_warnung(verb, fin_wert, autohaus_id, fertig_am)
    if doppelt:
        hinweise.append(doppelt)

    try:
        # Der Wächter steht IM Schreibbefehl: Zwischen der Prüfung oben und
        # diesem Einfügen kann das Büro das Autohaus stilllegen. Ein Fahrzeug
        # bei einem stillgelegten Autohaus fällt aus jeder Übersicht heraus und
        # ist nicht mehr abzurechnen.
        zeiger = verb.execute(
            "INSERT INTO fahrzeug (fin, kennzeichen, autohaus_id, leistung_id, netto_cent,"
            " notiz, erfasst_von, erfasst_am, fertig_am, status, vorgang)"
            " SELECT ?,?,?,?,?,?,?,?,?,'offen',?"
            "  WHERE EXISTS (SELECT 1 FROM autohaus a WHERE a.id=? AND a.aktiv=1"
            "                  AND (a.filiale_von IS NULL OR a.sammeln_mit_hauptsitz=0"
            "                       OR EXISTS (SELECT 1 FROM autohaus h"
            "                                   WHERE h.id=a.filiale_von AND h.aktiv=1)))",
            (fin_wert, (kennzeichen or "").strip().upper(), autohaus_id, leistung_id, netto,
             (notiz or "").strip(), benutzer["id"], datenbank.jetzt(), fertig_am, vorgang,
             autohaus_id))
        if zeiger.rowcount != 1:
            raise Abgelehnt(
                "Dieses Autohaus wurde zwischenzeitlich stillgelegt. Das Fahrzeug "
                "ist NICHT gespeichert. Bitte im Büro nachfragen.")
    except sqlite3.IntegrityError as fehler:
        if "vorgang" in str(fehler):
            vorhanden = verb.execute(
                "SELECT * FROM fahrzeug WHERE vorgang=?", (vorgang,)).fetchone()
            if vorhanden:
                return vorhanden, ["War schon erfasst, nichts doppelt angelegt."]
        raise

    zeile = verb.execute("SELECT * FROM fahrzeug WHERE id=?", (zeiger.lastrowid,)).fetchone()
    protokoll.notieren(verb, benutzer, "Fahrzeug erfasst", "Fahrzeug %d" % zeile["id"],
                       "%s, %s" % (fin_wert, haus["name"]))
    return zeile, hinweise


def aendern(verb, benutzer, fahrzeug_id: int, **felder):
    """Nur solange nichts abgerechnet ist."""
    zeile = verb.execute("SELECT * FROM fahrzeug WHERE id=?", (fahrzeug_id,)).fetchone()
    if not zeile:
        raise Abgelehnt("Fahrzeug nicht gefunden.")
    if zeile["status"] != "offen":
        raise Abgelehnt("Das Fahrzeug steht schon auf einer Rechnung und ist damit festgeschrieben.")

    erlaubt = {}
    if "fin" in felder and felder["fin"]:
        erlaubt["fin"] = finmodul.normalisieren(felder["fin"])
    for name in ("kennzeichen", "notiz", "fertig_am"):
        if felder.get(name) is not None:
            erlaubt[name] = str(felder[name]).strip()
    if felder.get("autohaus_id"):
        erlaubt["autohaus_id"] = int(felder["autohaus_id"])
    if felder.get("leistung_id"):
        erlaubt["leistung_id"] = int(felder["leistung_id"])
    if erlaubt.get("autohaus_id") or erlaubt.get("leistung_id"):
        erlaubt["netto_cent"] = preis_fuer(
            verb,
            erlaubt.get("autohaus_id", zeile["autohaus_id"]),
            erlaubt.get("leistung_id", zeile["leistung_id"]))
    if not erlaubt:
        return zeile

    # Der Wächter steht IM Schreibbefehl, nicht davor. Zwischen einer Prüfung
    # und einem ungeschützten Schreiben kann das Büro die Sammelrechnung
    # erzeugen — dann stünde hinterher ein anderes Autohaus am Fahrzeug als auf
    # der Rechnung, auf der es steht.
    satz = ", ".join("%s=?" % k for k in erlaubt)
    geschrieben = verb.execute(
        "UPDATE fahrzeug SET %s WHERE id=? AND status='offen'" % satz,
        tuple(erlaubt.values()) + (fahrzeug_id,))
    if geschrieben.rowcount != 1:
        raise Abgelehnt(
            "Das Fahrzeug wurde inzwischen abgerechnet. Die Änderung wurde "
            "nicht übernommen.")
    protokoll.notieren(verb, benutzer, "Fahrzeug geändert", "Fahrzeug %d" % fahrzeug_id,
                       ", ".join("%s=%s" % (k, v) for k, v in erlaubt.items()))
    return verb.execute("SELECT * FROM fahrzeug WHERE id=?", (fahrzeug_id,)).fetchone()


def verwerfen(verb, benutzer, fahrzeug_id: int, grund: str = ""):
    """Ein offenes Fahrzeug verwerfen.

    Erst bedingt schreiben, dann den Grund ermitteln — nicht umgekehrt. Wer
    vorher prüft und danach ungeschützt schreibt, kann ein Fahrzeug als
    verworfen markieren, das inzwischen auf einer Rechnung steht.
    """
    geschrieben = verb.execute(
        "UPDATE fahrzeug SET status='verworfen' WHERE id=? AND status='offen'",
        (fahrzeug_id,))
    if geschrieben.rowcount == 1:
        protokoll.notieren(verb, benutzer, "Fahrzeug verworfen",
                           "Fahrzeug %d" % fahrzeug_id, grund)
        return

    zeile = verb.execute("SELECT * FROM fahrzeug WHERE id=?", (fahrzeug_id,)).fetchone()
    if not zeile:
        raise Abgelehnt("Fahrzeug nicht gefunden.")
    if zeile["status"] == "verworfen":
        raise Abgelehnt("Dieses Fahrzeug ist schon verworfen.")
    raise Abgelehnt("Abgerechnete Fahrzeuge werden nicht gelöscht, dafür gibt es "
                    "die Gutschrift.")


def fotos_je_fahrzeug(verb, kennungen):
    """Alle Fotos zu einer Menge Fahrzeuge in einer Abfrage."""
    if not kennungen:
        return {}
    platz = ",".join("?" * len(kennungen))
    gebuendelt = {}
    for zeile in verb.execute(
            "SELECT * FROM foto WHERE fahrzeug_id IN (%s) ORDER BY fahrzeug_id, id"
            % platz, tuple(kennungen)):
        gebuendelt.setdefault(zeile["fahrzeug_id"], []).append(zeile)
    return gebuendelt


def foto_anhaengen(verb, benutzer, fahrzeug_id: int, art: str, bild: bytes,
                   dateiname: str):
    """Trägt ein bereits gespeichertes Bild in die Datenbank ein."""
    if art not in ARTEN:
        raise Abgelehnt("Unbekannte Fotoart: %s" % art)
    zeile = verb.execute("SELECT * FROM fahrzeug WHERE id=?", (fahrzeug_id,)).fetchone()
    if not zeile:
        raise Abgelehnt("Fahrzeug nicht gefunden.")
    verb.execute(
        "INSERT INTO foto (fahrzeug_id, art, datei, erstellt_am) VALUES (?,?,?,?)",
        (fahrzeug_id, art, dateiname, datenbank.jetzt()))
    protokoll.notieren(verb, benutzer, "Foto angehängt", "Fahrzeug %d" % fahrzeug_id,
                       "%s, %d kB" % (ART_NAME[art], len(bild) // 1024))


def suchen(verb, text: str, grenze: int = 60):
    """Sucht über ALLE Fahrzeuge, auch abgerechnete.

    Bewusst nachsichtig: Trenner und Kleinschreibung sind egal, und es wird in
    zwei Schreibweisen gesucht — einmal roh und einmal mit der FIN-Ersetzung
    I/O/Q zu 1/0. Wer ein Kennzeichen „SZ-IO 12" sucht, soll es finden, und wer
    eine FIN mit einem O statt einer Null abliest, ebenso.

    Teilsuche ist Absicht: Am Telefon liest jemand die letzten sechs Stellen vor.
    """
    roh = re.sub(r"[\s\-_.]", "", str(text or "")).upper()
    if len(roh) < MINDESTLAENGE_SUCHE:
        return []
    finartig = "".join(finmodul.VERWECHSLUNG.get(z, z) for z in roh)
    muster = ["%%%s%%" % roh, "%%%s%%" % finartig]
    return verb.execute(
        "SELECT f.*, a.name AS autohaus, a.kurz AS autohaus_kurz,"
        " l.bezeichnung AS leistung, b.name AS erfasser,"
        " r.status AS rechnung_status, r.lexware_nummer, r.von AS rechnung_von,"
        " r.bis AS rechnung_bis,"
        " (SELECT COUNT(*) FROM gutschrift g WHERE g.fahrzeug_id=f.id) AS gutgeschrieben"
        " FROM fahrzeug f"
        " JOIN autohaus a ON a.id=f.autohaus_id"
        " JOIN leistung l ON l.id=f.leistung_id"
        " JOIN benutzer b ON b.id=f.erfasst_von"
        " LEFT JOIN rechnung r ON r.id=f.rechnung_id"
        " WHERE f.fin LIKE ? OR f.fin LIKE ?"
        # Kennzeichen stehen mit Strich UND Leerzeichen in der Datenbank
        # ("SZ-AB 123"), gesucht wird meist ohne beides.
        "    OR REPLACE(REPLACE(REPLACE(UPPER(f.kennzeichen),'-',''),' ',''),'.','') LIKE ?"
        "    OR REPLACE(REPLACE(REPLACE(UPPER(f.kennzeichen),'-',''),' ',''),'.','') LIKE ?"
        " ORDER BY f.id DESC LIMIT ?",
        (muster[0], muster[1], muster[0], muster[1], grenze)).fetchall()


def zuletzt_erfasst(verb, benutzer_id: int):
    """Das zuletzt von DIESEM Mitarbeiter erfasste Fahrzeug.

    Dient nur dazu, das Autohaus vorzubelegen. Zwei Leute können an zwei
    verschiedenen Häusern arbeiten, deshalb je Mitarbeiter und nicht global.
    """
    return verb.execute(
        "SELECT * FROM fahrzeug WHERE erfasst_von=? ORDER BY id DESC LIMIT 1",
        (benutzer_id,)).fetchone()


def offene(verb, autohaus_id: int = None, von: str = None, bis: str = None):
    bedingung = ["f.status='offen'"]
    werte = []
    if autohaus_id:
        bedingung.append("f.autohaus_id=?")
        werte.append(autohaus_id)
    if von:
        bedingung.append("date(f.fertig_am) >= date(?)")
        werte.append(von)
    if bis:
        bedingung.append("date(f.fertig_am) <= date(?)")
        werte.append(bis)
    return verb.execute(
        "SELECT f.*, a.name AS autohaus, a.kurz AS autohaus_kurz, l.bezeichnung AS leistung,"
        " b.name AS erfasser"
        " FROM fahrzeug f"
        " JOIN autohaus a ON a.id=f.autohaus_id"
        " JOIN leistung l ON l.id=f.leistung_id"
        " JOIN benutzer b ON b.id=f.erfasst_von"
        " WHERE %s ORDER BY date(f.fertig_am), f.id" % " AND ".join(bedingung),
        tuple(werte)).fetchall()

"""Sicherung der Datenbank, mit sofortiger Prüfung.

Zwei Dinge, die man falsch machen kann und die beide teuer sind:

1. **SQLite einfach kopieren, während geschrieben wird.** Dabei entsteht eine
   Datei, die halb aussieht wie eine Datenbank und beim Einspielen zerbricht.
   Deshalb `Connection.backup()` aus der Standardbibliothek: die liefert einen
   in sich stimmigen Stand, auch wenn gerade jemand ein Fahrzeug erfasst.

2. **Eine Sicherung anlegen und nie hineinsehen.** Eine ungeprüfte Sicherung
   ist keine Sicherung, sie ist eine Hoffnung. Jede hier erzeugte Datei wird
   sofort geöffnet, auf innere Unversehrtheit geprüft und durchgezählt.
"""
from __future__ import annotations

import os
import shutil
import sqlite3

from . import datenbank, protokoll

ERWARTETE_TABELLEN = ("benutzer", "autohaus", "leistung", "fahrzeug",
                      "rechnung", "rechnungsposition", "gutschrift",
                      "foto", "protokoll", "preis")
BEHALTEN = 14


class SicherungFehler(RuntimeError):
    pass


def dateiname(zeitpunkt: str = None) -> str:
    roh = (zeitpunkt or datenbank.jetzt()).replace(":", "-")
    return "sicherung-%s.db" % roh[:19]


def anlegen(quelle_pfad: str, ziel_pfad: str) -> None:
    quelle = sqlite3.connect(quelle_pfad)
    ziel = sqlite3.connect(ziel_pfad)
    try:
        quelle.backup(ziel)
    finally:
        ziel.close()
        quelle.close()


def pruefen(pfad: str) -> dict:
    """Öffnet die Sicherung und sieht wirklich hinein. Wirft bei jedem Zweifel."""
    if not os.path.isfile(pfad) or os.path.getsize(pfad) == 0:
        raise SicherungFehler("Die Sicherung wurde nicht angelegt oder ist leer.")
    verb = sqlite3.connect(pfad)
    verb.row_factory = sqlite3.Row
    try:
        befund = verb.execute("PRAGMA integrity_check").fetchone()[0]
        if befund != "ok":
            raise SicherungFehler("Die Sicherung ist beschädigt: %s" % befund)
        vorhanden = {z["name"] for z in
                     verb.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        fehlend = [t for t in ERWARTETE_TABELLEN if t not in vorhanden]
        if fehlend:
            raise SicherungFehler("In der Sicherung fehlen Tabellen: %s"
                                  % ", ".join(fehlend))
        zahlen = {t: verb.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                  for t in ERWARTETE_TABELLEN}
    except sqlite3.DatabaseError as fehler:
        raise SicherungFehler("Die Sicherung lässt sich nicht lesen: %s" % fehler)
    finally:
        verb.close()
    return {"datei": os.path.basename(pfad), "groesse": os.path.getsize(pfad),
            "zeilen": zahlen}


def aufraeumen(ordner: str, behalten: int = BEHALTEN) -> int:
    """Alte Sicherungen wegräumen. Gibt zurück, wie viele gelöscht wurden."""
    dateien = sorted(n for n in os.listdir(ordner)
                     if n.startswith("sicherung-") and n.endswith(".db"))
    weg = dateien[:-behalten] if len(dateien) > behalten else []
    for name in weg:
        os.remove(os.path.join(ordner, name))
    return len(weg)


def anlegen_und_pruefen(quelle_pfad: str, ordner: str, benutzer=None,
                        verb=None) -> dict:
    """Der ganze Vorgang. Schlägt die Prüfung fehl, fliegt die Datei weg —
    eine kaputte Sicherung im Ordner wiegt in falscher Sicherheit."""
    os.makedirs(ordner, exist_ok=True)
    ziel = os.path.join(ordner, dateiname())
    anlegen(quelle_pfad, ziel)
    try:
        ergebnis = pruefen(ziel)
    except SicherungFehler:
        try:
            os.remove(ziel)
        except OSError:
            pass
        raise
    ergebnis["aufgeraeumt"] = aufraeumen(ordner)
    if verb is not None:
        from . import protokoll
        protokoll.notieren(
            verb, benutzer, "Sicherung angelegt", ergebnis["datei"],
            "geprüft, %d Fahrzeuge, %d Rechnungen, %d kB"
            % (ergebnis["zeilen"]["fahrzeug"], ergebnis["zeilen"]["rechnung"],
               ergebnis["groesse"] // 1024))
    return ergebnis


LAUFMARKE = "laeuft.pid"


def marke_setzen(ordner: str) -> str:
    """Vermerkt, dass der Dienst laeuft. Damit niemand ihm die Datenbank unter
    den Fuessen austauscht.

    Das Zuruecksetzen loescht die Datei und legt sie neu an. Laeuft der Dienst
    dabei weiter, schreibt er in eine Datei, die es nicht mehr gibt: Alles, was
    in dem Moment unterwegs ist, ist verloren, und niemand sieht es.
    """
    os.makedirs(ordner, exist_ok=True)
    pfad = os.path.join(ordner, LAUFMARKE)
    with open(pfad, "w", encoding="utf-8") as datei:
        datei.write("%d\n" % os.getpid())
    return pfad


def marke_versuchen(ordner: str) -> str:
    """Wie `marke_setzen`, aber ohne den Dienst umzubringen.

    Die Marke ist eine Bequemlichkeit für das Wiederherstellungswerkzeug. Lässt
    sie sich nicht schreiben — volle Platte, falsche Rechte —, dann ist das ein
    Grund für eine Zeile im Protokoll des Dienstes, aber keiner, den Betrieb zu
    verweigern. Gerade bei voller Platte will das Büro noch nachsehen können.
    """
    try:
        return marke_setzen(ordner)
    except OSError as fehler:
        print("[Hallenbuch] Laufmarke nicht schreibbar (%s). Der Dienst läuft "
              "trotzdem; das Wiederherstellungswerkzeug erkennt ihn dann aber "
              "nicht und muss von Hand gestoppt werden." % fehler)
        return ""


def marke_loeschen(pfad: str) -> None:
    if not pfad:
        return
    try:
        os.remove(pfad)
    except OSError:
        pass


def dienst_laeuft(ordner: str) -> int:
    """Prozessnummer des laufenden Dienstes, oder 0.

    Eine liegengebliebene Marke nach einem Absturz zaehlt nicht: Es wird
    nachgesehen, ob es den Prozess wirklich noch gibt.
    """
    pfad = os.path.join(ordner, LAUFMARKE)
    try:
        with open(pfad, encoding="utf-8") as datei:
            nummer = int(datei.read().strip() or 0)
    except (OSError, ValueError):
        return 0
    if nummer <= 0:
        return 0
    try:
        os.kill(nummer, 0)
    except ProcessLookupError:
        return 0
    except PermissionError:
        return nummer      # gibt es, gehoert nur jemand anderem
    except OSError:
        return 0
    return nummer


def zurueckspielen(quelle_pfad: str, ziel_pfad: str) -> dict:
    """Eine Sicherung wieder einspielen. Der Weg zurück, den es vorher nicht gab.

    Eine Sicherung, die nie zurückgespielt wurde, ist eine Hoffnung. Bis heute
    war dieser Weg nirgends beschrieben und nirgends geprüft: Es gab weder ein
    Werkzeug noch eine Zeile in der Einführung. Wer im Ernstfall die Datei von
    Hand kopiert, trifft dabei zwei Fallen — die alten `-wal` und `-shm` bleiben
    liegen und überschreiben den eingespielten Stand, und niemand merkt, wenn
    die Sicherung selbst beschädigt ist.

    Der Ablauf hier: erst die Sicherung prüfen, dann den vorhandenen Stand
    beiseitelegen, dann über die Sicherungsschnittstelle von SQLite einspielen
    (die räumt WAL mit ab), dann das Ergebnis erneut prüfen.
    """
    vorher = pruefen(quelle_pfad)      # wirft, wenn die Sicherung nichts taugt
    beiseite = ""
    if os.path.isfile(ziel_pfad):
        beiseite = "%s.vorher-%s" % (ziel_pfad, datenbank.jetzt().replace(":", "-")[:19])
        try:
            anlegen(ziel_pfad, beiseite)      # sauberer Stand, wenn die Datei noch taugt
        except sqlite3.DatabaseError:
            # Genau der haeufigste Anlass: Es wird zurueckgespielt, WEIL die
            # laufende Datei hinueber ist. Dann scheitert die Sicherungs-
            # schnittstelle, und eine blosse Dateikopie ist das Richtige — sie
            # bewahrt auf, was da war, ohne es lesen zu muessen.
            for anhang in ("", "-wal", "-shm"):
                if os.path.isfile(ziel_pfad + anhang):
                    shutil.copy2(ziel_pfad + anhang, beiseite + anhang)
    # Die Zieldatei muss WEG, bevor eingespielt wird: Ist sie zerstoert — der
    # haeufigste Anlass ueberhaupt —, dann laesst sie sich nicht mehr oeffnen,
    # und `Connection.backup()` scheitert an ihr statt sie zu ersetzen. Mit
    # Test belegt. `-wal` und `-shm` gehen mit: ein fremdes Log neben einer
    # frisch eingespielten Datei hat dort nichts verloren.
    for anhang in ("", "-wal", "-shm"):
        try:
            os.remove(ziel_pfad + anhang)
        except OSError:
            pass
    anlegen(quelle_pfad, ziel_pfad)
    nachher = pruefen(ziel_pfad)
    if nachher["zeilen"] != vorher["zeilen"]:
        raise SicherungFehler(
            "Nach dem Einspielen stimmen die Zeilenzahlen nicht mit der Sicherung überein.")

    # Das Einspielen setzt ALLES zurueck, auch das Protokoll. Ohne die
    # folgenden Zeilen waere der Vorgang selbst spurlos: Das System stuende auf
    # einem aelteren Stand und nichts darin sagte, dass jemand es
    # zurueckgesetzt hat. Fuer die Nachvollziehbarkeit nach GoBD ist genau das
    # der Punkt — eine Luecke muss erklaerbar sein.
    verb = datenbank.verbinden(ziel_pfad)
    try:
        letzter = verb.execute("SELECT MAX(zeit) FROM protokoll").fetchone()[0] or "?"
        protokoll.notieren(
            verb, None, "Sicherung eingespielt", os.path.basename(quelle_pfad),
            "Der Stand wurde auf %s zurückgesetzt. Was danach erfasst wurde, ist "
            "nicht mehr im System.%s"
            % (str(letzter)[:19].replace("T", " "),
               (" Die Datei von vorher liegt als %s daneben."
                % os.path.basename(beiseite)) if beiseite else ""))
    finally:
        verb.close()

    return {"quelle": os.path.basename(quelle_pfad), "ziel": ziel_pfad,
            "beiseite": beiseite, "zeilen": nachher["zeilen"],
            "stand": str(letzter)[:19].replace("T", " ")}


PLATZ_WARNUNG = 15          # Prozent freier Platz, ab dem gewarnt wird


def platz(ordner: str) -> dict:
    """Wie viel Platz ist noch da, und reicht er.

    Niemand merkte bisher, wenn die Platte volläuft. Fotos wachsen dauerhaft:
    bei 95 Fahrzeugen die Woche und zwei Bildern je Wagen sind das im Jahr
    mehrere Gigabyte. Ist die Platte voll, schlägt **das Erfassen** fehl — der
    schlechteste denkbare Zeitpunkt, mitten in der Halle.
    """
    gesamt, benutzt, frei = shutil.disk_usage(ordner)
    anteil = (frei * 100.0 / gesamt) if gesamt else 0.0
    return {"frei_mb": frei // (1024 * 1024), "gesamt_mb": gesamt // (1024 * 1024),
            "anteil": round(anteil, 1), "knapp": anteil < PLATZ_WARNUNG}


def vorhandene(ordner: str):
    """Die vorhandenen Sicherungen, neueste zuerst."""
    if not os.path.isdir(ordner):
        return []
    liste = []
    for name in sorted((n for n in os.listdir(ordner)
                        if n.startswith("sicherung-") and n.endswith(".db")),
                       reverse=True):
        voll = os.path.join(ordner, name)
        liste.append({"datei": name, "groesse": os.path.getsize(voll)})
    return liste

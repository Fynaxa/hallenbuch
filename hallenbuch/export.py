"""Vollständiger Datenexport.

In der Bauliste steht das als Vertragszusage: Der Wettbewerber löscht die Daten
spätestens 30 Tage nach Vertragsende und sichert kein Wiedereinspielen zu. Wir
sagen das Gegenteil zu, also muss es auch auf Knopfdruck gehen.

Format: eine ZIP mit einer CSV je Tabelle plus den Fotos. CSV mit Semikolon und
BOM, damit Excel in Deutschland sie ohne Nachfragen richtig öffnet.

**Passwörter werden nicht exportiert.** Ein Export wandert per Mail und liegt auf
Rechnern, auf die wir keinen Blick haben.
"""
from __future__ import annotations

import csv
import io
import os
import zipfile

from . import datenbank, protokoll

# Tabelle -> Spalten, die NICHT mit hinausgehen.
TABELLEN = {
    "benutzer": ("passwort",),
    "autohaus": (),
    "leistung": (),
    "preis": (),
    "fahrzeug": (),
    "rechnung": (),
    "rechnungsposition": (),
    "gutschrift": (),
    "foto": (),
    "protokoll": (),
}

LIESMICH = """Datenexport Hallenbuch
=====================

Erzeugt am: %(zeit)s
Betrieb:    TDetailing

Inhalt
------
Je Tabelle eine CSV-Datei, getrennt durch Semikolon, in UTF-8 mit
Byte-Order-Mark. So öffnet Excel sie ohne Nachfrage richtig.

%(tabellen)s

Der Ordner fotos/ enthält alle Bilder. Die Spalte "datei" in foto.csv nennt
den jeweiligen Dateinamen.

Was NICHT enthalten ist
-----------------------
Passwörter. Die stehen nur als nicht rückrechenbarer Prüfwert in der
Datenbank und gehen in keinen Export.

Wiedereinspielen
----------------
Die CSV-Dateien sind vollständig und lassen sich in jede Datenbank einlesen.
Wer den Betrieb genau so wieder aufnehmen will, braucht zusätzlich die Datei
betrieb/hallenbuch.db vom Server.
"""


def _zeilen(verb, tabelle: str, geheim=()):
    spalten = [z["name"] for z in verb.execute("PRAGMA table_info(%s)" % tabelle)
               if z["name"] not in geheim]
    daten = verb.execute("SELECT %s FROM %s ORDER BY rowid"
                         % (", ".join(spalten), tabelle)).fetchall()
    return spalten, daten


def _csv(spalten, daten) -> bytes:
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=";", quoting=csv.QUOTE_MINIMAL,
                           lineterminator="\r\n")
    schreiber.writerow(spalten)
    for zeile in daten:
        schreiber.writerow(["" if zeile[s] is None else zeile[s] for s in spalten])
    return b"\xef\xbb\xbf" + puffer.getvalue().encode("utf-8")


def schreiben(verb, ziel_pfad: str, fotos_ordner: str, benutzer=None) -> dict:
    """Schreibt den Export nach ziel_pfad. Gibt je Tabelle die Zeilenzahl zurück."""
    # Alle Tabellen aus EINEM Stand lesen. Ohne Transaktion kann zwischen zwei
    # Tabellen eine Sammelrechnung entstehen — im Export stünde dann eine
    # Rechnung ohne ihre Positionen. Ein Export, der „vollständig" heisst, darf
    # nicht in sich widersprüchlich sein.
    verb.execute("BEGIN")
    try:
        zusammenfassung = _tabellen_schreiben(verb, ziel_pfad, fotos_ordner)
    finally:
        verb.execute("COMMIT")

    if benutzer is not None:
        protokoll.notieren(
            verb, benutzer, "Datenexport erzeugt", "",
            ", ".join("%s %d" % (k, v) for k, v in zusammenfassung.items()))
    return zusammenfassung


def _tabellen_schreiben(verb, ziel_pfad: str, fotos_ordner: str) -> dict:
    zusammenfassung = {}
    with zipfile.ZipFile(ziel_pfad, "w", zipfile.ZIP_DEFLATED) as zip_datei:
        beschreibung = []
        for tabelle, geheim in TABELLEN.items():
            spalten, daten = _zeilen(verb, tabelle, geheim)
            zip_datei.writestr("%s.csv" % tabelle, _csv(spalten, daten))
            zusammenfassung[tabelle] = len(daten)
            weggelassen = (" (ohne %s)" % ", ".join(geheim)) if geheim else ""
            beschreibung.append("  %-20s %5d Zeilen%s" % (tabelle + ".csv", len(daten),
                                                          weggelassen))

        bilder = 0
        if os.path.isdir(fotos_ordner):
            for name in sorted(os.listdir(fotos_ordner)):
                voll = os.path.join(fotos_ordner, name)
                if os.path.isfile(voll):
                    zip_datei.write(voll, "fotos/%s" % name)
                    bilder += 1
        zusammenfassung["fotos"] = bilder
        beschreibung.append("  %-20s %5d Dateien" % ("fotos/", bilder))

        zip_datei.writestr("LIESMICH.txt", (LIESMICH % {
            "zeit": datenbank.jetzt(),
            "tabellen": "\n".join(beschreibung),
        }).encode("utf-8"))

    return zusammenfassung


def dateiname() -> str:
    return "hallenbuch-export-%s.zip" % datenbank.jetzt()[:10]

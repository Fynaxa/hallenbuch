"""SQLite-Schicht. Die Abnahmeregeln stehen als Datenbankregeln drin, nicht nur im Code.

Zwei Zusagen aus dem Lieferschnitt sind hier als eindeutige Schlüssel verankert,
damit sie auch bei Doppelklick, zwei Browsern oder einem Abbruch mitten im
Speichern halten:

  ix_rechnung_einmalig  — je Autohaus und Zeitraum höchstens eine lebende Rechnung
  ix_position_fahrzeug  — ein Fahrzeug kann nie auf zwei Rechnungen stehen
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

PFAD = os.environ.get("HALLENBUCH_DB", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "betrieb", "hallenbuch.db"))

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS benutzer (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  anmeldename  TEXT NOT NULL UNIQUE,
  passwort     TEXT NOT NULL,
  rolle        TEXT NOT NULL CHECK (rolle IN ('erfasser','buero')),
  aktiv        INTEGER NOT NULL DEFAULT 1,
  sitzung_stand INTEGER NOT NULL DEFAULT 1,
  angelegt_am  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS autohaus (
  id               INTEGER PRIMARY KEY,
  name             TEXT NOT NULL,
  kurz             TEXT NOT NULL UNIQUE,
  strasse          TEXT DEFAULT '',
  plz              TEXT DEFAULT '',
  ort              TEXT DEFAULT '',
  land             TEXT NOT NULL DEFAULT 'DE',
  kaeuferreferenz  TEXT DEFAULT '',
  lieferantennummer TEXT DEFAULT '',
  lexware_kontakt  TEXT DEFAULT '',
  filiale_von      INTEGER REFERENCES autohaus(id),
  zahlungsziel     INTEGER NOT NULL DEFAULT 14,
  sammeln_mit_hauptsitz INTEGER NOT NULL DEFAULT 0,
  aktiv            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS leistung (
  id            INTEGER PRIMARY KEY,
  bezeichnung   TEXT NOT NULL,
  netto_cent    INTEGER NOT NULL,
  sortierung    INTEGER NOT NULL DEFAULT 0,
  aktiv         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS preis (
  autohaus_id INTEGER NOT NULL REFERENCES autohaus(id) ON DELETE CASCADE,
  leistung_id INTEGER NOT NULL REFERENCES leistung(id) ON DELETE CASCADE,
  netto_cent  INTEGER NOT NULL,
  PRIMARY KEY (autohaus_id, leistung_id)
);

CREATE TABLE IF NOT EXISTS rechnung (
  id              INTEGER PRIMARY KEY,
  autohaus_id     INTEGER NOT NULL REFERENCES autohaus(id),
  von             TEXT NOT NULL,
  bis             TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('entwurf','freigegeben','finalisiert','storniert')),
  netto_cent      INTEGER NOT NULL DEFAULT 0,
  steuersatz      INTEGER NOT NULL DEFAULT 19,
  erstellt_von    INTEGER REFERENCES benutzer(id),
  erstellt_am     TEXT NOT NULL,
  freigegeben_von INTEGER REFERENCES benutzer(id),
  freigegeben_am  TEXT,
  finalisiert_am  TEXT,
  lexware_id      TEXT DEFAULT '',
  lexware_nummer  TEXT DEFAULT '',
  lexware_uri     TEXT DEFAULT '',
  letzter_fehler  TEXT DEFAULT '',
  letzter_fehler_am TEXT DEFAULT '',
  fehler_unklar   INTEGER NOT NULL DEFAULT 0,
  ausstellung_seit TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS fahrzeug (
  id           INTEGER PRIMARY KEY,
  fin          TEXT NOT NULL,
  kennzeichen  TEXT DEFAULT '',
  autohaus_id  INTEGER NOT NULL REFERENCES autohaus(id),
  leistung_id  INTEGER NOT NULL REFERENCES leistung(id),
  netto_cent   INTEGER NOT NULL,
  notiz        TEXT DEFAULT '',
  erfasst_von  INTEGER NOT NULL REFERENCES benutzer(id),
  erfasst_am   TEXT NOT NULL,
  fertig_am    TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'offen'
               CHECK (status IN ('offen','abgerechnet','verworfen')),
  rechnung_id  INTEGER REFERENCES rechnung(id),
  vorgang      TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS rechnungsposition (
  id          INTEGER PRIMARY KEY,
  rechnung_id INTEGER NOT NULL REFERENCES rechnung(id) ON DELETE CASCADE,
  fahrzeug_id INTEGER NOT NULL REFERENCES fahrzeug(id),
  text        TEXT NOT NULL,
  netto_cent  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS gutschrift (
  id            INTEGER PRIMARY KEY,
  fahrzeug_id   INTEGER NOT NULL REFERENCES fahrzeug(id),
  rechnung_id   INTEGER NOT NULL REFERENCES rechnung(id),
  netto_cent    INTEGER NOT NULL,
  grund         TEXT NOT NULL,
  erstellt_von  INTEGER REFERENCES benutzer(id),
  erstellt_am   TEXT NOT NULL,
  lexware_id    TEXT DEFAULT '',
  lexware_nummer TEXT DEFAULT '',
  letzter_fehler TEXT DEFAULT '',
  letzter_fehler_am TEXT DEFAULT '',
  fehler_unklar INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS foto (
  id          INTEGER PRIMARY KEY,
  fahrzeug_id INTEGER NOT NULL REFERENCES fahrzeug(id) ON DELETE CASCADE,
  art         TEXT NOT NULL CHECK (art IN ('aussen','innen','schaden')),
  datei       TEXT NOT NULL,
  erstellt_am TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS anmeldeversuch (
  id          INTEGER PRIMARY KEY,
  wann        INTEGER NOT NULL,          -- Unixzeit, vergleichbar ohne Zeitzonenspass
  anmeldename TEXT NOT NULL,
  herkunft    TEXT DEFAULT '',
  erfolg      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS protokoll (
  id            INTEGER PRIMARY KEY,
  zeit          TEXT NOT NULL,
  benutzer_id   INTEGER,
  benutzer_name TEXT DEFAULT '',
  was           TEXT NOT NULL,
  gegenstand    TEXT DEFAULT '',
  einzelheiten  TEXT DEFAULT ''
);

-- Keine Rechnung doppelt erzeugt.
CREATE UNIQUE INDEX IF NOT EXISTS ix_rechnung_einmalig
  ON rechnung (autohaus_id, von, bis) WHERE status <> 'storniert';

-- Kein Fahrzeug doppelt auf einer Rechnung, und auf keiner zweiten.
-- Beim Stornieren werden die Positionen gelöscht, damit neu abgerechnet werden kann.
CREATE UNIQUE INDEX IF NOT EXISTS ix_position_fahrzeug
  ON rechnungsposition (fahrzeug_id);

CREATE UNIQUE INDEX IF NOT EXISTS ix_gutschrift_fahrzeug
  ON gutschrift (fahrzeug_id);

CREATE INDEX IF NOT EXISTS ix_fahrzeug_offen
  ON fahrzeug (autohaus_id, status, fertig_am);
CREATE INDEX IF NOT EXISTS ix_fahrzeug_fin ON fahrzeug (fin);
CREATE INDEX IF NOT EXISTS ix_protokoll_zeit ON protokoll (zeit DESC);
CREATE INDEX IF NOT EXISTS ix_versuch_name ON anmeldeversuch (anmeldename, wann);
CREATE INDEX IF NOT EXISTS ix_versuch_herkunft ON anmeldeversuch (herkunft, wann);
"""


def jetzt() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def verbinden(pfad: str = None) -> sqlite3.Connection:
    ziel = pfad or PFAD
    ordner = os.path.dirname(ziel)
    if ordner and ziel != ":memory:":
        os.makedirs(ordner, exist_ok=True)
    verb = sqlite3.connect(ziel, timeout=15, isolation_level=None)
    verb.row_factory = sqlite3.Row
    verb.execute("PRAGMA foreign_keys = ON")
    verb.execute("PRAGMA busy_timeout = 15000")
    if ziel != ":memory:":
        verb.execute("PRAGMA journal_mode = WAL")
    verb.execute("PRAGMA synchronous = FULL")
    return verb


# Spalten, die nach dem ersten Livegang dazugekommen sind. CREATE TABLE
# IF NOT EXISTS fasst eine bestehende Tabelle nicht mehr an, deshalb werden sie
# einzeln nachgerüstet. Idempotent: was da ist, bleibt.
NACHZURUESTEN = (
    ("benutzer", "sitzung_stand", "INTEGER NOT NULL DEFAULT 1"),
    ("rechnung", "letzter_fehler", "TEXT DEFAULT ''"),
    ("rechnung", "letzter_fehler_am", "TEXT DEFAULT ''"),
    ("rechnung", "fehler_unklar", "INTEGER NOT NULL DEFAULT 0"),
    ("rechnung", "ausstellung_seit", "TEXT DEFAULT ''"),
    ("gutschrift", "letzter_fehler", "TEXT DEFAULT ''"),
    ("gutschrift", "letzter_fehler_am", "TEXT DEFAULT ''"),
    ("gutschrift", "fehler_unklar", "INTEGER NOT NULL DEFAULT 0"),
)


def _nachruesten(verb: sqlite3.Connection) -> list:
    ergaenzt = []
    for tabelle, spalte, art in NACHZURUESTEN:
        vorhanden = {z["name"] for z in verb.execute("PRAGMA table_info(%s)" % tabelle)}
        if spalte not in vorhanden:
            verb.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tabelle, spalte, art))
            ergaenzt.append("%s.%s" % (tabelle, spalte))
    return ergaenzt


def aufbauen(verb: sqlite3.Connection) -> None:
    verb.executescript(SCHEMA)
    _nachruesten(verb)


def euro(cent: int) -> str:
    return ("%d,%02d" % (cent // 100, cent % 100)).replace(",", ",")


def cent(text: str) -> int:
    """Einen getippten Betrag in Cent: '129,90', '129.90', '129', '1.299,90'.

    Vorher wurde hier **jeder Punkt zu einem Komma**. Damit wurde aus dem
    Tausenderpunkt eine Nachkommastelle: `1.299` ergab **1,29 EUR**,
    hundertfach zu wenig, ohne jede Fehlermeldung — und auf jedem Fahrzeug mit
    diesem Preis. `1.299,90` liess sich ueberhaupt nicht eingeben. Beides
    gemessen, und die Funktion hatte keinen einzigen Test.

    Die Regel jetzt: Stehen Punkt und Komma zusammen, ist das **letzte**
    Zeichen das Dezimaltrennzeichen (das deckt `1.299,90` und `1,299.90` ab).
    Steht nur ein Komma, ist es immer das Dezimaltrennzeichen. Steht nur ein
    Punkt, entscheidet die Zahl der Ziffern dahinter: drei heisst Tausender,
    ein oder zwei heisst Dezimal. Alles andere wird abgelehnt statt geraten.
    """
    if text is None:
        raise ValueError("Kein Betrag angegeben.")
    sauber = (str(text).strip().replace("€", "").replace("\xa0", "")
              .replace(" ", "").replace("'", ""))
    if not sauber:
        raise ValueError("Kein Betrag angegeben.")
    vorzeichen = -1 if sauber.startswith("-") else 1
    sauber = sauber.lstrip("+-")

    stellen = [i for i, z in enumerate(sauber) if z in ".,"]
    if not stellen:
        ganzteil, nachkomma = sauber, ""
    else:
        letzte = stellen[-1]
        rest = sauber[letzte + 1:]
        zeichen = sauber[letzte]
        gemischt = "." in sauber and "," in sauber
        if gemischt or zeichen == "," or len(rest) in (1, 2):
            if not 1 <= len(rest) <= 2:
                raise ValueError(
                    "Bitte höchstens zwei Stellen nach dem Komma: %s" % text)
            ganzteil, nachkomma = sauber[:letzte], rest
        elif len(rest) == 3:
            ganzteil, nachkomma = sauber, ""      # 1.299 sind 1299 Euro
        else:
            raise ValueError("Betrag nicht lesbar: %s" % text)

    gruppen = [g for g in ganzteil.replace(".", ",").split(",")] if ganzteil else ["0"]
    if len(gruppen) > 1:
        # Tausendergruppen muessen stimmen, sonst ist es ein Tippfehler und
        # kein Betrag. Lieber eine Absage als ein geratener Preis.
        if not 1 <= len(gruppen[0]) <= 3 or any(len(g) != 3 for g in gruppen[1:]):
            raise ValueError("Betrag nicht lesbar: %s" % text)
    ganz_text = "".join(gruppen) or "0"
    if not ganz_text.isdigit() or (nachkomma and not nachkomma.isdigit()):
        raise ValueError("Betrag nicht lesbar: %s" % text)
    return vorzeichen * (int(ganz_text) * 100 + int((nachkomma + "00")[:2]))

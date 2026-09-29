"""Eine ungeprüfte Sicherung ist keine Sicherung. Das prüfen diese Tests."""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hallenbuch import datenbank, fahrzeuge, sicherung  # noqa: E402

WURZEL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tests import hilfe  # noqa: E402


class Sicherungen(unittest.TestCase):

    def setUp(self):
        self.ordner = tempfile.mkdtemp(prefix="hallenbuch-sicherung-")
        self.quelle = os.path.join(self.ordner, "quelle.db")
        self.ziel = os.path.join(self.ordner, "sicherungen")
        verb = datenbank.verbinden(self.quelle)
        datenbank.aufbauen(verb)
        verb.execute("INSERT INTO benutzer (name,anmeldename,passwort,rolle,angelegt_am)"
                     " VALUES ('Halle','halle','x','erfasser',?)", (datenbank.jetzt(),))
        verb.execute("INSERT INTO autohaus (id,name,kurz) VALUES (1,'Nord','NORD')")
        verb.execute("INSERT INTO leistung (id,bezeichnung,netto_cent)"
                     " VALUES (1,'Vollaufbereitung',14900)")
        nutzer = verb.execute("SELECT * FROM benutzer LIMIT 1").fetchone()
        for i in range(5):
            fahrzeuge.erfassen(verb, nutzer, hilfe.fin_nummer(i), 1, 1,
                               "2026-09-21", vorgang="si-%d" % i)
        verb.close()

    def tearDown(self):
        shutil.rmtree(self.ordner, ignore_errors=True)

    def test_sicherung_wird_angelegt_und_geprueft(self):
        ergebnis = sicherung.anlegen_und_pruefen(self.quelle, self.ziel)
        self.assertEqual(ergebnis["zeilen"]["fahrzeug"], 5)
        self.assertEqual(ergebnis["zeilen"]["autohaus"], 1)
        self.assertGreater(ergebnis["groesse"], 0)
        self.assertTrue(os.path.isfile(os.path.join(self.ziel, ergebnis["datei"])))

    def test_sicherung_laesst_sich_wirklich_wieder_oeffnen(self):
        """Der eigentliche Zweck: aus der Sicherung muss man weiterarbeiten können."""
        ergebnis = sicherung.anlegen_und_pruefen(self.quelle, self.ziel)
        wieder = datenbank.verbinden(os.path.join(self.ziel, ergebnis["datei"]))
        try:
            offen = fahrzeuge.offene(wieder)
            self.assertEqual(len(offen), 5)
            self.assertEqual(offen[0]["autohaus"], "Nord")
        finally:
            wieder.close()

    def test_kaputte_datei_faellt_durch(self):
        kaputt = os.path.join(self.ordner, "kaputt.db")
        with open(kaputt, "wb") as datei:
            datei.write(b"SQLite format 3\x00" + b"\xff" * 4000)
        with self.assertRaises(sicherung.SicherungFehler):
            sicherung.pruefen(kaputt)

    def test_leere_datei_faellt_durch(self):
        leer = os.path.join(self.ordner, "leer.db")
        open(leer, "wb").close()
        with self.assertRaises(sicherung.SicherungFehler) as fall:
            sicherung.pruefen(leer)
        self.assertIn("leer", str(fall.exception))

    def test_datenbank_ohne_unsere_tabellen_faellt_durch(self):
        """Eine gültige SQLite-Datei ist noch lange keine Hallenbuch-Sicherung."""
        fremd = os.path.join(self.ordner, "fremd.db")
        verb = sqlite3.connect(fremd)
        verb.execute("CREATE TABLE irgendwas (a INTEGER)")
        verb.commit()
        verb.close()
        with self.assertRaises(sicherung.SicherungFehler) as fall:
            sicherung.pruefen(fremd)
        self.assertIn("fehlen Tabellen", str(fall.exception))

    def test_kaputte_sicherung_bleibt_nicht_liegen(self):
        """Eine kaputte Datei im Ordner wiegt in falscher Sicherheit."""
        echte_pruefung = sicherung.pruefen

        def platzt(pfad):
            raise sicherung.SicherungFehler("Prüfung fehlgeschlagen")

        sicherung.pruefen = platzt
        try:
            with self.assertRaises(sicherung.SicherungFehler):
                sicherung.anlegen_und_pruefen(self.quelle, self.ziel)
        finally:
            sicherung.pruefen = echte_pruefung
        self.assertEqual(sicherung.vorhandene(self.ziel), [])

    def test_alte_sicherungen_werden_weggeraeumt(self):
        os.makedirs(self.ziel, exist_ok=True)
        for i in range(20):
            open(os.path.join(self.ziel, "sicherung-2026-09-%02dT03-00-00.db" % (i + 1)),
                 "wb").close()
        weg = sicherung.aufraeumen(self.ziel, behalten=14)
        self.assertEqual(weg, 6)
        uebrig = sicherung.vorhandene(self.ziel)
        self.assertEqual(len(uebrig), 14)
        # Die neuesten bleiben, nicht die ältesten
        self.assertEqual(uebrig[0]["datei"], "sicherung-2026-09-20T03-00-00.db")

    def test_neueste_zuerst(self):
        sicherung.anlegen(self.quelle, os.path.join(self.ordner, "x.db"))
        os.makedirs(self.ziel, exist_ok=True)
        for tag in ("01", "09", "05"):
            open(os.path.join(self.ziel, "sicherung-2026-09-%sT03-00-00.db" % tag),
                 "wb").close()
        namen = [s["datei"] for s in sicherung.vorhandene(self.ziel)]
        self.assertEqual(namen[0], "sicherung-2026-09-09T03-00-00.db")

    def test_werkzeug_meldet_erfolg_mit_rueckgabewert_null(self):
        import subprocess
        wurzel = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        lauf = subprocess.run(
            [sys.executable, os.path.join(wurzel, "werkzeuge", "sichern.py")],
            env=dict(os.environ, HALLENBUCH_DB=self.quelle),
            capture_output=True, text=True)
        self.assertEqual(lauf.returncode, 0, lauf.stderr)
        self.assertIn("geprüft", lauf.stdout)
        self.assertIn("fahrzeug 5", lauf.stdout)

    def test_werkzeug_meldet_fehlende_datenbank(self):
        import subprocess
        wurzel = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        lauf = subprocess.run(
            [sys.executable, os.path.join(wurzel, "werkzeuge", "sichern.py")],
            env=dict(os.environ, HALLENBUCH_DB=os.path.join(self.ordner, "gibtsnicht.db")),
            capture_output=True, text=True)
        self.assertEqual(lauf.returncode, 1)


if __name__ == "__main__":
    unittest.main()


class Wiederherstellung(unittest.TestCase):
    """Der Weg zurück. Eine Sicherung, die nie zurückgespielt wurde, ist eine
    Hoffnung — bis heute gab es dafür weder Werkzeug noch Test."""

    def setUp(self):
        self.ordner = tempfile.mkdtemp(prefix="hallenbuch-zurueck-")
        self.db = os.path.join(self.ordner, "hallenbuch.db")
        self.sicherungen = os.path.join(self.ordner, "sicherungen")
        verb = datenbank.verbinden(self.db)
        datenbank.aufbauen(verb)
        verb.execute("INSERT INTO benutzer (id,name,anmeldename,passwort,rolle,angelegt_am)"
                     " VALUES (1,'Achmed','achmed','x','buero',?)", (datenbank.jetzt(),))
        verb.execute("INSERT INTO benutzer (id,name,anmeldename,passwort,rolle,angelegt_am)"
                     " VALUES (2,'Halle','halle','x','erfasser',?)", (datenbank.jetzt(),))
        verb.execute("INSERT INTO autohaus (id,name,kurz) VALUES (1,'Autohaus','AH')")
        verb.execute("INSERT INTO leistung (id,bezeichnung,netto_cent)"
                     " VALUES (1,'Vollaufbereitung',14900)")
        self.nutzer = verb.execute("SELECT * FROM benutzer WHERE id=2").fetchone()
        for i in range(3):
            fahrzeuge.erfassen(verb, self.nutzer, hilfe.fin_nummer(i), 1, 1,
                               "2026-09-18", vorgang="w-%d" % i)
        verb.close()
        self.stand = sicherung.anlegen_und_pruefen(self.db, self.sicherungen)

    def tearDown(self):
        shutil.rmtree(self.ordner, ignore_errors=True)

    def _fahrzeuge(self):
        verb = datenbank.verbinden(self.db)
        try:
            return verb.execute("SELECT COUNT(*) FROM fahrzeug").fetchone()[0]
        finally:
            verb.close()

    def test_eingespielt_wird_der_stand_der_sicherung(self):
        # Nach der Sicherung kommen zwei Fahrzeuge dazu, die verloren gehen sollen.
        verb = datenbank.verbinden(self.db)
        for i in range(3, 5):
            fahrzeuge.erfassen(verb, self.nutzer, hilfe.fin_nummer(i), 1, 1,
                               "2026-09-19", vorgang="w-%d" % i)
        verb.close()
        self.assertEqual(self._fahrzeuge(), 5)

        quelle = os.path.join(self.sicherungen, self.stand["datei"])
        ergebnis = sicherung.zurueckspielen(quelle, self.db)
        self.assertEqual(self._fahrzeuge(), 3)
        self.assertEqual(ergebnis["zeilen"]["fahrzeug"], 3)

    def test_der_stand_von_vorher_wird_nicht_weggeworfen(self):
        quelle = os.path.join(self.sicherungen, self.stand["datei"])
        ergebnis = sicherung.zurueckspielen(quelle, self.db)
        self.assertTrue(os.path.isfile(ergebnis["beiseite"]), ergebnis["beiseite"])
        # Und die beiseitegelegte Datei ist eine lesbare Datenbank, keine Kopie
        # mitten im Schreibvorgang.
        sicherung.pruefen(ergebnis["beiseite"])

    def test_der_alte_wal_stand_kommt_nicht_zurueck(self):
        """Die Falle beim Kopieren von Hand: Neben der Datei liegen `-wal` und
        `-shm`. Wer nur die `.db` ersetzt, lässt den neueren Stand im Write-Ahead-Log
        stehen; beim nächsten Öffnen ist die Wiederherstellung wieder weg.
        (Die Dateien selbst bleiben auf manchen Systemen liegen, das ist
        harmlos — es zählt, welcher Stand am Ende drinsteht.)"""
        verb = datenbank.verbinden(self.db)
        fahrzeuge.erfassen(verb, self.nutzer, hilfe.fin_nummer(9), 1, 1,
                           "2026-09-19", vorgang="w-wal")
        verb.close()
        self.assertEqual(self._fahrzeuge(), 4)

        quelle = os.path.join(self.sicherungen, self.stand["datei"])
        sicherung.zurueckspielen(quelle, self.db)
        self.assertEqual(self._fahrzeuge(), 3)
        # Noch einmal oeffnen und schliessen: ein liegengebliebenes Log haette
        # hier den vierten Wagen zurueckgebracht.
        self.assertEqual(self._fahrzeuge(), 3)

    def test_ein_abgebrochener_schreibvorgang_kommt_nicht_zurueck(self):
        """Der Ernstfall: Der Dienst wurde abgeschossen, im Write-Ahead-Log
        stehen noch Aenderungen, die nie in die Datei geschrieben wurden.
        Wer jetzt nur die `.db` ersetzt und `-wal` liegen laesst, bekommt beim
        naechsten Oeffnen den ALTEN Stand zurueck und die Wiederherstellung ist
        weg. Hier wird genau das erzeugt: ein Kindprozess schreibt und stirbt,
        ohne die Verbindung zu schliessen."""
        schreiber = (
            "import sys, os\n"
            "sys.path.insert(0, %r)\n"
            "sys.path.insert(0, %r)\n"
            "from hallenbuch import datenbank, fahrzeuge\n"
            "from tests import hilfe\n"
            "v = datenbank.verbinden(%r)\n"
            "n = v.execute('SELECT * FROM benutzer WHERE id=2').fetchone()\n"
            "fahrzeuge.erfassen(v, n, hilfe.fin_nummer(7), 1, 1, '2026-09-19',"
            " vorgang='w-abbruch')\n"
            "os._exit(0)\n"
        ) % (WURZEL, os.path.join(WURZEL, "tests"), self.db)
        subprocess.run([sys.executable, "-c", schreiber], check=True, cwd=WURZEL)

        self.assertTrue(os.path.isfile(self.db + "-wal"), "kein Log liegengeblieben")
        self.assertEqual(self._fahrzeuge(), 4)

        quelle = os.path.join(self.sicherungen, self.stand["datei"])
        sicherung.zurueckspielen(quelle, self.db)
        self.assertEqual(self._fahrzeuge(), 3)

    def test_einspielen_ueber_eine_zerstoerte_datenbank(self):
        """Der haeufigste Anlass ueberhaupt: Man spielt zurueck, WEIL die
        laufende Datei hinueber ist. Dann darf das Werkzeug nicht daran
        scheitern, dass es die kaputte Datei zuerst oeffnen will."""
        with open(self.db, "wb") as datei:
            datei.write(b"SQLite format 3\x00" + b"\xff" * 4000)
        quelle = os.path.join(self.sicherungen, self.stand["datei"])
        ergebnis = sicherung.zurueckspielen(quelle, self.db)
        self.assertEqual(ergebnis["zeilen"]["fahrzeug"], 3)
        self.assertEqual(self._fahrzeuge(), 3)

    def test_das_einspielen_steht_danach_im_protokoll(self):
        """Das Zurückspielen setzt auch das Protokoll zurück. Ohne einen
        Eintrag über den Vorgang selbst stünde das System auf einem älteren
        Stand, und nichts darin sagte warum. Für die Nachvollziehbarkeit nach
        GoBD muss eine Lücke erklärbar sein."""
        verb = datenbank.verbinden(self.db)
        fahrzeuge.erfassen(verb, self.nutzer, hilfe.fin_nummer(6), 1, 1,
                           "2026-09-19", vorgang="w-spur")
        verb.close()
        quelle = os.path.join(self.sicherungen, self.stand["datei"])
        sicherung.zurueckspielen(quelle, self.db)

        verb = datenbank.verbinden(self.db)
        try:
            eintrag = verb.execute(
                "SELECT * FROM protokoll ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            verb.close()
        self.assertEqual(eintrag["was"], "Sicherung eingespielt")
        self.assertEqual(eintrag["gegenstand"], self.stand["datei"])
        self.assertIn("zurückgesetzt", eintrag["einzelheiten"])
        self.assertEqual(eintrag["benutzer_name"], "System")

    def test_eine_kaputte_sicherung_wird_nicht_eingespielt(self):
        kaputt = os.path.join(self.ordner, "kaputt.db")
        with open(kaputt, "wb") as datei:
            datei.write(b"das ist keine datenbank")
        with self.assertRaises(sicherung.SicherungFehler):
            sicherung.zurueckspielen(kaputt, self.db)
        # Der laufende Stand ist unangetastet.
        self.assertEqual(self._fahrzeuge(), 3)


class Laufmarke(unittest.TestCase):
    """Solange der Dienst läuft, darf ihm niemand die Datenbank austauschen.
    Er schriebe sonst in eine gelöschte Datei weiter, und alles aus diesem
    Moment wäre verloren, ohne dass es jemand merkt."""

    def setUp(self):
        self.ordner = tempfile.mkdtemp(prefix="hallenbuch-marke-")

    def tearDown(self):
        shutil.rmtree(self.ordner, ignore_errors=True)

    def test_ohne_marke_laeuft_nichts(self):
        self.assertEqual(sicherung.dienst_laeuft(self.ordner), 0)

    def test_die_eigene_prozessnummer_gilt_als_laufend(self):
        pfad = sicherung.marke_setzen(self.ordner)
        self.assertEqual(sicherung.dienst_laeuft(self.ordner), os.getpid())
        sicherung.marke_loeschen(pfad)
        self.assertEqual(sicherung.dienst_laeuft(self.ordner), 0)

    def test_eine_unschreibbare_marke_haelt_den_dienst_nicht_auf(self):
        """Die Marke ist eine Bequemlichkeit. Lässt sie sich nicht schreiben —
        volle Platte, falsche Rechte —, darf das den Betrieb nicht verhindern.
        Gerade bei voller Platte will das Büro noch nachsehen können."""
        os.chmod(self.ordner, 0o500)
        try:
            with self.assertRaises(OSError):
                sicherung.marke_setzen(self.ordner)
            self.assertEqual(sicherung.marke_versuchen(self.ordner), "")
        finally:
            os.chmod(self.ordner, 0o700)

    def test_ohne_marke_laesst_sich_auch_nichts_loeschen(self):
        sicherung.marke_loeschen("")          # wirft nicht

    def test_eine_liegengebliebene_marke_zaehlt_nicht(self):
        """Nach einem Absturz bleibt die Datei liegen. Dann darf sie nicht
        dauerhaft blockieren."""
        with open(os.path.join(self.ordner, sicherung.LAUFMARKE), "w") as datei:
            datei.write("999999\n")          # diese Nummer gibt es nicht
        self.assertEqual(sicherung.dienst_laeuft(self.ordner), 0)

    def test_muell_in_der_marke_blockiert_nicht(self):
        with open(os.path.join(self.ordner, sicherung.LAUFMARKE), "w") as datei:
            datei.write("kein pid\n")
        self.assertEqual(sicherung.dienst_laeuft(self.ordner), 0)


class Plattenplatz(unittest.TestCase):
    """Eine volle Platte trifft zuerst die Halle: Dann schlägt das Erfassen
    fehl. Bisher merkte das niemand vorher."""

    def setUp(self):
        self.ordner = tempfile.mkdtemp(prefix="hallenbuch-platz-")

    def tearDown(self):
        shutil.rmtree(self.ordner, ignore_errors=True)

    def test_der_platz_wird_gemessen(self):
        raum = sicherung.platz(self.ordner)
        self.assertGreater(raum["gesamt_mb"], 0)
        self.assertGreaterEqual(raum["frei_mb"], 0)
        self.assertGreaterEqual(raum["anteil"], 0)

    def test_die_schwelle_greift_in_beide_richtungen(self):
        """Unabhängig davon, wie voll die Platte dieses Rechners gerade ist."""
        vorher = sicherung.PLATZ_WARNUNG
        try:
            sicherung.PLATZ_WARNUNG = 100.0     # alles gilt als knapp
            self.assertTrue(sicherung.platz(self.ordner)["knapp"])
            sicherung.PLATZ_WARNUNG = 0.0       # nichts gilt als knapp
            self.assertFalse(sicherung.platz(self.ordner)["knapp"])
        finally:
            sicherung.PLATZ_WARNUNG = vorher


class Migration(unittest.TestCase):
    """Nachgeruestete Spalten. Wer eine Spalte ins Schema schreibt und NICHT
    nach NACHZURUESTEN, bricht beim naechsten Deploy jede bestehende Anlage."""

    def setUp(self):
        self.ordner = tempfile.mkdtemp(prefix="hallenbuch-migration-")
        self.db = os.path.join(self.ordner, "alt.db")

    def tearDown(self):
        shutil.rmtree(self.ordner, ignore_errors=True)

    def _spalten(self, verb, tabelle):
        return {z[1] for z in verb.execute("PRAGMA table_info(%s)" % tabelle)}

    def test_eine_alte_datenbank_bekommt_alle_spalten_zurueck(self):
        verb = datenbank.verbinden(self.db)
        datenbank.aufbauen(verb)
        verb.execute("INSERT INTO benutzer (id,name,anmeldename,passwort,rolle,angelegt_am)"
                     " VALUES (1,'Achmed','achmed','x','buero',?)", (datenbank.jetzt(),))
        # Zustand vor der Nachruestung herstellen: die Spalten wieder entfernen.
        for tabelle, spalte, _art in datenbank.NACHZURUESTEN:
            verb.execute("ALTER TABLE %s DROP COLUMN %s" % (tabelle, spalte))
        for tabelle, spalte, _art in datenbank.NACHZURUESTEN:
            self.assertNotIn(spalte, self._spalten(verb, tabelle))

        datenbank.aufbauen(verb)          # genau das tut ein Neustart nach dem Deploy

        for tabelle, spalte, _art in datenbank.NACHZURUESTEN:
            self.assertIn(spalte, self._spalten(verb, tabelle),
                          "%s.%s fehlt nach dem Nachruesten" % (tabelle, spalte))
        # Und die Daten sind noch da.
        self.assertEqual(
            verb.execute("SELECT name FROM benutzer WHERE id=1").fetchone()["name"],
            "Achmed")
        verb.close()

    def test_nachruesten_laeuft_mehrfach_ohne_schaden(self):
        verb = datenbank.verbinden(self.db)
        for _ in range(3):
            datenbank.aufbauen(verb)
        self.assertIn("sitzung_stand", self._spalten(verb, "benutzer"))
        verb.close()

"""HTTP-Schicht. Standardbibliothek, läuft hinter einem Caddy oder nginx."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import tempfile
import traceback
import urllib.parse
from datetime import date
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import (anmeldeschutz, datenbank, export, fahrzeuge, konten, lexware,
               protokoll, rechnung, seiten, sicherheit, sicherung, stammdaten,
               zeitraum)

HIER = os.path.dirname(os.path.abspath(__file__))
STATISCH = os.path.join(HIER, "statisch")
BETRIEB = os.path.join(os.path.dirname(HIER), "betrieb")
FOTOS = os.path.join(BETRIEB, "fotos")
SICHERUNGEN = os.path.join(BETRIEB, "sicherungen")
KEKS = "hallenbuch"
# Rohe SQLite-Meldungen sind englisch und nennen Tabellennamen. Was der
# Betrieb tatsaechlich falsch gemacht hat, steht hier auf Deutsch.
DOPPELT = {
    "autohaus.kurz": "Dieses Kürzel gibt es schon. Bitte ein anderes wählen.",
    "benutzer.anmeldename": "Diesen Anmeldenamen gibt es schon.",
    "leistung.bezeichnung": "Diese Leistung gibt es schon.",
}
GROESSTE_ANFRAGE = 12 * 1024 * 1024   # Fotos kommen als Base64 mit


def nur_weg(pfad: str) -> str:
    """Der Weg ohne Fragezeichenteil, fuer Zeilen im Systemprotokoll.

    In der Adresszeile stehen Suchbegriffe: `/suche?q=WVWZZZ1JZ3W386752` oder
    ein Kennzeichen. Beides ist nach EuGH C-319/22 personenbezogen, sobald sich
    ein Halter dahinter ermitteln laesst. Im Journal des Servers hat das nichts
    verloren — dort gilt weder unsere Aufbewahrungsfrist noch unser Loeschweg.
    """
    return urllib.parse.urlparse(pfad or "").path[:200]


def woche_heute():
    return zeitraum.aufloesen("diese-woche")


class Hallenbuch:
    """Haelt Datenbank und Rechnungsdienst. Ein Objekt je Prozess."""

    def __init__(self, db_pfad=None, dienst=None):
        self.db_pfad = db_pfad or datenbank.PFAD
        self.dienst = dienst or lexware.dienst()
        verb = self.verbinden()
        datenbank.aufbauen(verb)
        verb.close()

    @property
    def probebetrieb(self) -> bool:
        return isinstance(self.dienst, lexware.Attrappe)

    def verbinden(self):
        return datenbank.verbinden(self.db_pfad)


class Griff(BaseHTTPRequestHandler):
    server_version = "Hallenbuch"
    protocol_version = "HTTP/1.1"
    anwendung: Hallenbuch = None

    # --- Werkzeug ---------------------------------------------------------

    def log_message(self, format, *args):  # noqa: A002
        pass

    def handle_one_request(self):
        # Die Merker gelten je ANFRAGE, nicht je Verbindung: bei Keep-Alive
        # laufen mehrere Anfragen durch dasselbe Objekt.
        self._antwort_begonnen = False
        self._koerper_gelesen = False
        self._felder_gelesen = None
        BaseHTTPRequestHandler.handle_one_request(self)

    def _senden(self, koerper, art="text/html; charset=utf-8", kode=200, kekse=None,
                zwischenspeicher=None):
        if isinstance(koerper, str):
            koerper = koerper.encode("utf-8")
        self._antwort_begonnen = True
        self.send_response(kode)
        self.send_header("Content-Type", art)
        self.send_header("Content-Length", str(len(koerper)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", zwischenspeicher or "no-store")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data: blob:; "
                         "script-src 'self'; style-src 'self'; connect-src 'self'")
        if self.close_connection:
            # Wenn der Rumpf liegengeblieben ist, muss auch der Browser wissen,
            # dass diese Verbindung zu Ende ist. Sonst schickt er die naechste
            # Anfrage in eine Leitung, die der Server gerade zumacht.
            self.send_header("Connection", "close")
        for k in (kekse or []):
            self.send_header("Set-Cookie", k)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(koerper)

    def _umleiten(self, weg, kekse=None):
        # Kopfzeilen werden als Latin-1 verschickt. Ein Umlaut, der nicht durch
        # `quote` gegangen ist, kommt beim Browser als Fragezeichen an — einmal
        # passiert, vom Test gefangen. Deshalb hier die Notbremse.
        weg = weg.encode("ascii", "backslashreplace").decode("ascii")
        self._antwort_begonnen = True
        self.send_response(303)
        self.send_header("Location", weg)
        self.send_header("Content-Length", "0")
        for k in (kekse or []):
            self.send_header("Set-Cookie", k)
        self.end_headers()

    def _felder(self):
        # Das Ergebnis wird gemerkt. Ein zweiter Aufruf kann den Rumpf nicht
        # noch einmal lesen — er ist aus dem Puffer heraus — und lieferte
        # stillschweigend ein leeres Formular. Wer das in einem neuen Weg
        # zweimal aufruft, verlöre die Eingaben, ohne eine Fehlermeldung zu
        # sehen. Eine Falle, die man genau einmal stellt.
        if self._felder_gelesen is not None:
            return self._felder_gelesen
        self._felder_gelesen = self._felder_lesen()
        return self._felder_gelesen

    def _felder_lesen(self):
        try:
            laenge = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            raise ValueError("Die Anfrage ist unvollständig angekommen.")
        self._koerper_gelesen = True
        if laenge <= 0:
            return {}
        if laenge > GROESSTE_ANFRAGE:
            # Den Rest absichtlich NICHT einlesen, das waeren Gigabyte. Dann
            # aber auch die Verbindung schliessen, sonst liest der Server den
            # liegengebliebenen Rumpf als naechste Anfrage.
            self.close_connection = True
            raise ValueError("Die Anfrage ist zu groß.")
        roh = self.rfile.read(laenge)
        art = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if art == "application/json":
            try:
                gelesen = json.loads(roh.decode("utf-8") or "{}")
            except ValueError:
                # Die Meldung von `json` ist englisch und nennt Spaltennummern.
                # In der Warteschlange am Handy stuende sie dann als Grund.
                raise ValueError("Die Anfrage ist unterwegs beschädigt worden.")
            return self._flach(gelesen)
        werte = urllib.parse.parse_qs(roh.decode("utf-8"), keep_blank_values=True)
        return {k: v[0] for k, v in werte.items()}

    @staticmethod
    def _flach(roh):
        """JSON zu einer flachen Abbildung aus Text machen.

        Die Warteschlange am Handy schickt JSON. Kommt etwas anderes an als ein
        Objekt aus einfachen Werten, ist der Eintrag kaputt. Dann muss die
        Antwort 400 lauten, damit das Handy ihn aus der Schlange wirft; 500
        heisst dort „spaeter noch einmal", und ein kaputter Eintrag bliebe fuer
        immer liegen. Gemessen: eine Liste statt eines Objekts ergab 500.
        """
        if not isinstance(roh, dict):
            raise ValueError("Die Anfrage hat die falsche Form.")
        flach = {}
        for name, wert in roh.items():
            if wert is None:
                flach[str(name)] = ""
            elif isinstance(wert, bool):
                flach[str(name)] = "1" if wert else ""
            elif isinstance(wert, (str, int, float)):
                flach[str(name)] = str(wert)
            else:
                raise ValueError("Das Feld %s hat die falsche Form." % name)
        return flach

    @staticmethod
    def _zahl(wert, name):
        """Ganze Zahl aus Formular oder Adresszeile, mit deutscher Absage."""
        text = str(wert if wert is not None else "").strip()
        if not text.lstrip("-").isdigit():
            raise ValueError("Die Angabe für %s ist keine Zahl." % name)
        return int(text)

    def _koerper_verwerfen(self):
        """Einen nicht gelesenen Anfragekoerper wegraeumen.

        Wird der Rumpf einer POST-Anfrage nicht gelesen (abgelehnte Herkunft,
        Weg ohne Formularauswertung), bleibt er im Puffer stehen: die naechste
        Anfrage auf derselben Verbindung beginnt mitten im alten Rumpf und geht
        verloren. Gemessen: POST /buero mit Rumpf, danach GET /gesund auf
        derselben Verbindung ergab 501.
        """
        if self._koerper_gelesen or self.command not in ("POST", "PUT", "PATCH"):
            return
        self._koerper_gelesen = True
        try:
            laenge = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            return
        if (self.headers.get("Transfer-Encoding") or "").strip():
            self.close_connection = True      # Laenge unbekannt
            return
        if laenge <= 0:
            return
        if laenge > GROESSTE_ANFRAGE:
            self.close_connection = True      # Gigabyte nicht erst einlesen
            return
        uebrig = laenge
        while uebrig > 0:
            stueck = self.rfile.read(min(self.BLOCK, uebrig))
            if not stueck:
                break
            uebrig -= len(stueck)

    def _absage(self, kode, text):
        """Fehlerantwort, die weder die Verbindung noch eine laufende Antwort zerstoert."""
        if self._antwort_begonnen:
            # Die Kopfzeilen sind schon raus (etwa mitten im Export). Ein
            # zweiter Antwortkopf waere Datenmuell auf der Leitung.
            self.close_connection = True
            return
        if urllib.parse.urlparse(self.path).path.startswith("/api/"):
            return self._senden(json.dumps({"ok": False, "fehler": text}),
                                "application/json; charset=utf-8", kode)
        return self._senden(text, "text/plain; charset=utf-8", kode)

    def _abfrage(self):
        teil = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(teil, keep_blank_values=True).items()}

    def _benutzer(self, verb):
        roh = self.headers.get("Cookie")
        if not roh:
            return None
        keks = SimpleCookie()
        keks.load(roh)
        if KEKS not in keks:
            return None
        geprueft = sicherheit.sitzung_pruefen(keks[KEKS].value)
        if not geprueft:
            return None
        kennung, stand = geprueft
        # Der Sitzungsstand muss noch zum Konto passen: Nach einem neuen
        # Passwort oder einer Stilllegung ist der alte Keks wertlos.
        return verb.execute(
            "SELECT * FROM benutzer WHERE id=? AND aktiv=1 AND sitzung_stand=?",
            (kennung, stand)).fetchone()

    def _herkunft(self) -> str:
        """Die echte Adresse des Anrufers, auch hinter dem Caddy.

        Caddy hängt die von IHM gesehene Adresse an `X-Forwarded-For` an,
        deshalb zählt der LETZTE Eintrag. Wer den Kopf selbst mitschickt, kann
        damit nur Einträge davor erfinden, nicht den letzten.
        """
        weiter = self.headers.get("X-Forwarded-For")
        if weiter:
            return weiter.split(",")[-1].strip()[:64]
        return (self.client_address[0] if self.client_address else "")[:64]

    def _gleiche_herkunft(self) -> bool:
        """Schutz gegen fremde Formulare: die Herkunft muss GENAU der Wirt sein.

        Vorher stand hier `Host in Herkunft`, ein Teilstrichvergleich und damit
        kein Schutz. Gemessen: `Referer: https://angreifer.example/?z=<host>`
        ging durch und legte ein Fahrzeug an. Genauso waere
        `https://hallenbuch.fynaxa.de.angreifer.example` durchgegangen.
        """
        herkunft = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if not herkunft:
            return True          # Handy-Warteschlange ohne Referer
        gastgeber = (self.headers.get("Host") or "").strip().lower()
        if not gastgeber:
            return False
        wirt = urllib.parse.urlsplit(herkunft).netloc.strip().lower()
        return bool(wirt) and wirt == gastgeber

    # --- Wegweiser --------------------------------------------------------

    def do_GET(self):
        self._bedienen("GET")

    def do_HEAD(self):
        self._bedienen("GET")

    def do_POST(self):
        if not self._gleiche_herkunft():
            self._koerper_verwerfen()
            self._senden("Abgelehnt: fremde Herkunft.", "text/plain; charset=utf-8", 403)
            return
        self._bedienen("POST")

    def _bedienen(self, art):
        weg = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        verb = self.anwendung.verbinden()
        try:
            if weg.startswith("/statisch/"):
                return self._datei(STATISCH, weg[len("/statisch/"):])
            if weg == "/manifest.json":
                return self._senden(json.dumps({
                    "name": "Hallenbuch", "short_name": "Hallenbuch",
                    "description": "Fahrzeugerfassung und Sammelabrechnung",
                    "start_url": "/erfassen", "scope": "/",
                    "display": "standalone", "orientation": "portrait",
                    "background_color": "#EDEFF2", "theme_color": "#0C1116",
                    "lang": "de",
                    "icons": [
                        {"src": "/statisch/symbole/symbol-192.png",
                         "sizes": "192x192", "type": "image/png", "purpose": "any"},
                        {"src": "/statisch/symbole/symbol-512.png",
                         "sizes": "512x512", "type": "image/png", "purpose": "any"},
                        {"src": "/statisch/symbole/symbol-maskabel-512.png",
                         "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
                    ]}), "application/manifest+json")
            if weg == "/sw.js":
                return self._datei(STATISCH, "sw.js")
            if weg == "/gesund":
                return self._senden("ok", "text/plain; charset=utf-8")

            benutzer = self._benutzer(verb)
            if weg == "/anmelden":
                return self._anmelden(verb, art)
            if weg == "/abmelden":
                return self._umleiten("/anmelden", ["%s=; Path=/; Max-Age=0; HttpOnly" % KEKS])
            if not benutzer:
                if weg.startswith("/api/"):
                    # NIE eine Umleitung fuer die Warteschlange am Handy: fetch
                    # folgt ihr, bekommt die Anmeldeseite mit Kennzahl 200 und
                    # haelt den Eintrag fuer gespeichert — es loescht ihn dann.
                    # Gemessen: eine ueber Nacht abgelaufene Sitzung liess alle
                    # gemerkten Fahrzeuge still verschwinden, mit der Meldung
                    # "nachgeladen".
                    return self._absage(401, "Nicht mehr angemeldet. Bitte neu "
                                             "anmelden, das Gemerkte bleibt erhalten.")
                return self._umleiten("/anmelden")

            # Fotos erst NACH der Anmeldung. Vorher standen sie unter einer
            # offenen Adresse: Der Dateiname ist zwar nicht zu raten, aber
            # Adressen werden geteilt, protokolliert und weitergereicht, und es
            # sind Kundendaten.
            if weg.startswith("/fotos/"):
                return self._datei(FOTOS, weg[len("/fotos/"):])

            return self._geschuetzt(verb, benutzer, weg, art)
        except KeyError as fehler:
            # Ein fehlendes Formularfeld ist eine kaputte Eingabe, kein
            # Serverfehler. Vorher gab es dafuer 500 mit dem rohen Python-Text.
            print("[Hallenbuch] Feld fehlt: %s %s — %s"
                  % (self.command, nur_weg(self.path), fehler))
            self._absage(400, "Es fehlt eine Angabe: %s."
                         % (fehler.args[0] if fehler.args else "unbekannt"))
        except ValueError as fehler:
            # Alle fachlichen Absagen erben von ValueError (fahrzeuge.Abgelehnt,
            # konten.Abgelehnt, rechnung.Abgelehnt), kaputte Eingaben ebenso.
            # 400 statt 500 ist hier nicht Kosmetik: die Warteschlange am Handy
            # wirft 400er aus der Liste und wiederholt 500er endlos.
            # Eine Zeile ins Protokoll des Dienstes, kein voller Rueckverfolg:
            # fachliche Absagen sind Alltag, sollen aber auffindbar bleiben,
            # falls sich jemand ueber "geht nicht" beschwert.
            print("[Hallenbuch] Abgewiesen: %s %s — %s"
                  % (self.command, nur_weg(self.path), fehler))
            self._absage(400, str(fehler) or "Die Anfrage passt nicht zum Formular.")
        except Exception:                                  # noqa: BLE001
            # Der Ausnahmetext geht NICHT mehr an den Browser: er nannte
            # Tabellen- und Spaltennamen und war englisch. Stattdessen eine
            # Nummer, die im Protokoll des Dienstes steht.
            kennzeichen = secrets.token_hex(4)
            print("[Hallenbuch] Fehler %s bei %s %s"
                  % (kennzeichen, self.command, nur_weg(self.path)))
            traceback.print_exc()
            self._absage(500, "Da ist etwas schiefgegangen. Bitte noch einmal "
                              "versuchen. Wenn es wieder passiert, gib diese "
                              "Nummer weiter: %s" % kennzeichen)
        finally:
            # Einen liegengebliebenen Rumpf wegraeumen, bevor die naechste
            # Anfrage auf derselben Verbindung beginnt.
            try:
                self._koerper_verwerfen()
            except Exception:                              # noqa: BLE001
                self.close_connection = True
            verb.close()

    def _geschuetzt(self, verb, benutzer, weg, art):
        gute = [m for m in self._abfrage().get("gut", "").split("|") if m]
        schlechte = [m for m in self._abfrage().get("fehler", "").split("|") if m]
        probe = self.anwendung.probebetrieb

        if weg == "/":
            return self._umleiten("/erfassen" if benutzer["rolle"] == "erfasser" else "/buero")

        if weg == "/erfassen":
            if art == "POST":
                return self._erfassen_speichern(verb, benutzer)
            letzte = fahrzeuge.offene(verb)[-5:][::-1]
            # Das Autohaus vorbelegen, die Leistung NICHT: das Autohaus bleibt
            # zwanzig Autos lang dasselbe, die Leistung wechselt je Fahrzeug.
            # Eine falsch vorbelegte Leistung waere ein Preisfehler, den niemand
            # bemerkt.
            vorher = fahrzeuge.zuletzt_erfasst(verb, benutzer["id"])
            return self._senden(seiten.erfassen(
                benutzer, self._haeuser(verb), self._leistungen(verb),
                date.today().isoformat(), letzte, gute, schlechte,
                vorbelegt={"autohaus_id": vorher["autohaus_id"]} if vorher else None,
                probebetrieb=probe,
                fotos=fahrzeuge.fotos_je_fahrzeug(verb, [z["id"] for z in letzte])))

        if weg == "/api/erfassen" and art == "POST":
            return self._api_erfassen(verb, benutzer)

        if weg == "/api/foto" and art == "POST":
            return self._api_foto(verb, benutzer)

        if weg == "/fahrzeuge":
            f = self._abfrage()
            # Dieselbe Datumspruefung wie beim Abrechnen. Vorher lieferte ein
            # unmoegliches Datum stillschweigend eine leere Liste, und das liest
            # sich wie „nichts da" statt wie „so geht das nicht".
            von = rechnung.tag(f["von"], "Das Startdatum") if f.get("von") else None
            bis = rechnung.tag(f["bis"], "Das Enddatum") if f.get("bis") else None
            zeilen = fahrzeuge.offene(
                verb, self._zahl(f["autohaus_id"], "Autohaus") if f.get("autohaus_id") else None,
                von, bis)
            ueberhaupt = verb.execute("SELECT 1 FROM fahrzeug LIMIT 1").fetchone()
            return self._senden(seiten.fahrzeuge(
                benutzer, zeilen, self._haeuser(verb), f.get("autohaus_id", ""),
                f.get("von", ""), f.get("bis", ""), gute, schlechte, probe,
                fotos=fahrzeuge.fotos_je_fahrzeug(verb, [z["id"] for z in zeilen]),
                ueberhaupt_welche=bool(ueberhaupt)))

        treffer = re.match(r"^/fahrzeuge/(\d{1,12})/verwerfen$", weg)
        if treffer and art == "POST":
            try:
                fahrzeuge.verwerfen(verb, benutzer, int(treffer.group(1)),
                                    self._felder().get("grund", ""))
                return self._umleiten("/fahrzeuge?gut=Fahrzeug+verworfen.")
            except (fahrzeuge.Abgelehnt, ValueError) as f:
                return self._umleiten("/fahrzeuge?fehler=%s" % urllib.parse.quote(str(f)))

        if weg == "/konto":
            if art == "POST":
                felder = self._felder()
                try:
                    stand = konten.eigenes_passwort_aendern(
                        verb, benutzer, felder.get("alt", ""), felder.get("neu", ""),
                        felder.get("wiederholung", ""))
                except konten.Abgelehnt as fehler:
                    return self._umleiten("/konto?fehler=%s"
                                          % urllib.parse.quote(str(fehler)))
                # Den eigenen Keks sofort erneuern, sonst fliegt man aus der
                # gerade benutzten Sitzung heraus.
                keks = "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (
                    KEKS, sicherheit.sitzung_ausstellen(benutzer["id"], stand),
                    sicherheit.SITZUNG_STUNDEN * 3600)
                if self.headers.get("X-Forwarded-Proto") == "https":
                    keks += "; Secure"
                return self._umleiten("/konto?gut=%s" % urllib.parse.quote(
                    "Passwort geändert. Auf anderen Geräten musst du dich neu anmelden."),
                    [keks])
            return self._senden(seiten.konto(benutzer, gute, schlechte, probe))

        if weg == "/suche":
            frage = self._abfrage().get("q", "")
            return self._senden(seiten.suche(
                benutzer, frage, fahrzeuge.suchen(verb, frage), probe))

        if benutzer["rolle"] != "buero":
            return self._senden("Dafür fehlt dir die Berechtigung.",
                                "text/plain; charset=utf-8", 403)

        if weg == "/buero":
            f = self._abfrage()
            gewaehlt = zeitraum.aufloesen(f.get("zeitraum", ""))
            if gewaehlt:
                von, bis = gewaehlt
            else:
                von, bis = woche_heute()
                if f.get("von"):
                    von = rechnung.tag(f["von"], "Das Startdatum")
                if f.get("bis"):
                    bis = rechnung.tag(f["bis"], "Das Enddatum")
            return self._senden(seiten.buero(
                benutzer, rechnung.je_haus(verb, von, bis), rechnung.uebersicht(verb, 40),
                von, bis, gute, schlechte, probe,
                zeitraeume=zeitraum.alle(), aktiv=zeitraum.passend(von, bis)))

        if weg == "/abrechnen" and art == "POST":
            return self._abrechnen(verb, benutzer)

        treffer = re.match(r"^/rechnung/(\d{1,12})$", weg)
        if treffer:
            try:
                kopf, posten = rechnung.mit_positionen(verb, int(treffer.group(1)))
            except rechnung.Abgelehnt:
                # Ein alter Lesezeichen-Link oder eine vertippte Nummer ist
                # nicht gefunden, nicht kaputt.
                return self._senden("Diese Rechnung gibt es nicht.",
                                    "text/plain; charset=utf-8", 404)
            return self._senden(seiten.rechnung_seite(
                benutzer, kopf, posten, gute, schlechte, probe))

        treffer = re.match(r"^/rechnung/(\d{1,12})/(freigeben|ausstellen|stornieren)$", weg)
        if treffer and art == "POST":
            return self._rechnungsschritt(verb, benutzer, int(treffer.group(1)),
                                          treffer.group(2))

        treffer = re.match(r"^/gutschrift/(\d{1,12})$", weg)
        if treffer and art == "POST":
            fahrzeug_id = int(treffer.group(1))
            grund = self._felder().get("grund", "")
            zurueck = "/fahrzeuge"
            try:
                zeile = verb.execute(
                    "SELECT rechnung_id FROM fahrzeug WHERE id=?", (fahrzeug_id,)).fetchone()
                zurueck = "/rechnung/%d" % zeile["rechnung_id"] if zeile and zeile["rechnung_id"]\
                    else zurueck
                rechnung.gutschrift(verb, benutzer, fahrzeug_id, grund, self.anwendung.dienst)
                return self._umleiten("%s?gut=Gutschrift+erzeugt." % zurueck)
            except (rechnung.Abgelehnt, lexware.LexwareFehler, ValueError) as f:
                return self._umleiten("%s?fehler=%s" % (zurueck, urllib.parse.quote(str(f))))

        if weg == "/verwaltung":
            # Auch stillgelegte Stammdaten, sonst liessen sie sich nie wieder
            # anschalten.
            return self._senden(seiten.verwaltung(
                benutzer,
                verb.execute("SELECT * FROM autohaus ORDER BY aktiv DESC, name").fetchall(),
                verb.execute("SELECT * FROM leistung"
                             " ORDER BY aktiv DESC, sortierung, bezeichnung").fetchall(),
                verb.execute("SELECT * FROM benutzer ORDER BY id").fetchall(),
                gute, schlechte, probe,
                sicherungen=sicherung.vorhandene(SICHERUNGEN),
                preise=stammdaten.sonderpreise(verb)))

        treffer = re.match(r"^/verwaltung/benutzer/(\d{1,12})/(stilllegen|aktivieren)$", weg)
        if treffer and art == "POST":
            try:
                if treffer.group(2) == "stilllegen":
                    konten.stilllegen(verb, benutzer, int(treffer.group(1)))
                    meldung = "Zugang stillgelegt."
                else:
                    konten.aktivieren(verb, benutzer, int(treffer.group(1)))
                    meldung = "Zugang wieder geöffnet."
                return self._umleiten("/verwaltung?gut=%s"
                                      % urllib.parse.quote(meldung))
            except konten.Abgelehnt as fehler:
                return self._umleiten("/verwaltung?fehler=%s"
                                      % urllib.parse.quote(str(fehler)))

        if weg == "/verwaltung/passwort" and art == "POST":
            felder = self._felder()
            try:
                konten.passwort_zuruecksetzen(
                    verb, benutzer, self._zahl(felder.get("benutzer_id"), "Zugang"),
                    felder.get("passwort", ""), felder.get("wiederholung", ""))
                return self._umleiten("/verwaltung?gut=%s" % urllib.parse.quote(
                    "Passwort gesetzt. Bestehende Anmeldungen dieses Zugangs sind beendet."))
            except (konten.Abgelehnt, ValueError) as fehler:
                return self._umleiten("/verwaltung?fehler=%s"
                                      % urllib.parse.quote(str(fehler)))

        treffer = re.match(
            r"^/verwaltung/preis/(\d{1,12})/(\d{1,12})/entfernen$", weg)
        if treffer and art == "POST":
            try:
                stammdaten.sonderpreis_entfernen(
                    verb, benutzer, int(treffer.group(1)), int(treffer.group(2)))
                return self._umleiten("/verwaltung?gut=%s"
                                      % urllib.parse.quote("Sonderpreis entfernt."))
            except (stammdaten.Abgelehnt, ValueError) as fehler:
                return self._umleiten("/verwaltung?fehler=%s"
                                      % urllib.parse.quote(str(fehler)))

        treffer = re.match(r"^/verwaltung/(autohaus|leistung)/(\d{1,12})$", weg)
        if treffer and art == "POST":
            was, kennung = treffer.group(1), int(treffer.group(2))
            try:
                if was == "autohaus":
                    stammdaten.autohaus_aendern(verb, benutzer, kennung, self._felder())
                else:
                    stammdaten.leistung_aendern(verb, benutzer, kennung, self._felder())
                return self._umleiten("/verwaltung?gut=%s"
                                      % urllib.parse.quote("Geändert."))
            except (stammdaten.Abgelehnt, ValueError) as fehler:
                return self._umleiten("/verwaltung?fehler=%s"
                                      % urllib.parse.quote(str(fehler)))

        if weg.startswith("/verwaltung/") and art == "POST":
            return self._stammdaten(verb, benutzer, weg.rsplit("/", 1)[1])

        if weg == "/sicherung" and art == "POST":
            try:
                ergebnis = sicherung.anlegen_und_pruefen(
                    self.anwendung.db_pfad, SICHERUNGEN, benutzer, verb)
                return self._umleiten("/verwaltung?gut=%s" % urllib.parse.quote(
                    "Sicherung angelegt und geprüft: %s, %d kB, %d Fahrzeuge."
                    % (ergebnis["datei"], ergebnis["groesse"] // 1024,
                       ergebnis["zeilen"]["fahrzeug"])))
            except sicherung.SicherungFehler as fehler:
                return self._umleiten("/verwaltung?fehler=%s"
                                      % urllib.parse.quote(str(fehler)))

        if weg == "/export":
            return self._export(verb, benutzer)

        if weg == "/protokoll":
            return self._senden(seiten.protokoll_seite(
                benutzer, protokoll.lesen(verb, 300), probe))

        return self._senden("Nicht gefunden.", "text/plain; charset=utf-8", 404)

    # --- Einzelne Handlungen ----------------------------------------------

    def _haeuser(self, verb):
        return verb.execute(
            "SELECT * FROM autohaus WHERE aktiv=1 ORDER BY name").fetchall()

    def _leistungen(self, verb):
        return verb.execute(
            "SELECT * FROM leistung WHERE aktiv=1 ORDER BY sortierung, bezeichnung").fetchall()

    def _anmelden(self, verb, art):
        if art == "GET":
            return self._senden(seiten.anmelden())
        felder = self._felder()
        name = (felder.get("anmeldename") or "").strip().lower()
        herkunft = self._herkunft()

        pause = anmeldeschutz.gesperrt(verb, name, herkunft)
        if pause:
            # Absichtlich NICHT als Fehlversuch vermerkt: sonst könnte jemand
            # die Wartezeit durch weiteres Klopfen selbst verlängern, und die
            # Tabelle wüchse unbegrenzt.
            return self._senden(seiten.anmelden(
                "Zu viele Fehlversuche. Bitte %d Minuten warten, danach geht es "
                "von selbst wieder." % pause), kode=429)

        zeile = verb.execute(
            "SELECT * FROM benutzer WHERE anmeldename=? AND aktiv=1",
            (name,)).fetchone()
        if not zeile:
            sicherheit.leerlauf()      # gleiche Antwortzeit wie bei falschem Passwort
        if not zeile or not sicherheit.passwort_stimmt(felder.get("passwort", ""), zeile["passwort"]):
            anmeldeschutz.vermerken(verb, name, herkunft, False)
            uebrig = (anmeldeschutz.VERSUCHE_JE_NAME
                      - anmeldeschutz.fehlversuche(verb, anmeldename=name))
            hinweis = "Name oder Passwort stimmt nicht."
            if 0 < uebrig <= 2:
                hinweis += " Noch %d Versuch%s, dann ist eine Pause fällig." % (
                    uebrig, "" if uebrig == 1 else "e")
            return self._senden(seiten.anmelden(hinweis), kode=401)

        anmeldeschutz.vermerken(verb, name, herkunft, True)
        anmeldeschutz.aufraeumen(verb)
        keks = "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (
            KEKS, sicherheit.sitzung_ausstellen(zeile["id"], zeile["sitzung_stand"]),
            sicherheit.SITZUNG_STUNDEN * 3600)
        if self.headers.get("X-Forwarded-Proto") == "https":
            keks += "; Secure"
        protokoll.notieren(verb, zeile, "Angemeldet")
        return self._umleiten("/", [keks])

    def _erfassen_speichern(self, verb, benutzer):
        felder = self._felder()
        try:
            zeile, hinweise = fahrzeuge.erfassen(
                verb, benutzer, felder.get("fin", ""),
                self._zahl(felder.get("autohaus_id"), "Autohaus"),
                self._zahl(felder.get("leistung_id"), "Leistung"),
                felder.get("fertig_am") or date.today().isoformat(),
                felder.get("kennzeichen", ""), felder.get("notiz", ""),
                felder.get("vorgang") or None)
        except (fahrzeuge.Abgelehnt, ValueError) as fehler:
            return self._umleiten("/erfassen?fehler=%s" % urllib.parse.quote(str(fehler)))
        meldungen = ["Fahrzeug %d erfasst: %s" % (zeile["id"], zeile["fin"])] + list(hinweise)
        return self._umleiten("/erfassen?gut=%s" % urllib.parse.quote("|".join(meldungen)))

    def _api_erfassen(self, verb, benutzer):
        """Für die Warteschlange am Handy. Antwortet immer mit JSON."""
        try:
            felder = self._felder()
            zeile, hinweise = fahrzeuge.erfassen(
                verb, benutzer, felder.get("fin", ""),
                self._zahl(felder.get("autohaus_id"), "Autohaus"),
                self._zahl(felder.get("leistung_id"), "Leistung"),
                felder.get("fertig_am") or date.today().isoformat(),
                felder.get("kennzeichen", ""), felder.get("notiz", ""),
                felder.get("vorgang") or None)
            return self._senden(json.dumps(
                {"ok": True, "id": zeile["id"], "fin": zeile["fin"], "hinweise": hinweise}),
                "application/json; charset=utf-8")
        except (fahrzeuge.Abgelehnt, ValueError, KeyError) as fehler:
            return self._senden(json.dumps({"ok": False, "fehler": str(fehler)}),
                                "application/json; charset=utf-8", 400)

    # Erlaubte Bildanfaenge. Ohne diese Pruefung koennte beliebiger Inhalt unter
    # einem .jpg landen und spaeter als Bild ausgeliefert werden.
    BILDMARKEN = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"))

    def _api_foto(self, verb, benutzer):
        try:
            felder = self._felder()
            fahrzeug_id = self._zahl(felder.get("fahrzeug_id"), "Fahrzeug")
            art = (felder.get("art") or "aussen").strip()
            roh = felder.get("daten", "")
            if "," in roh:
                roh = roh.split(",", 1)[1]
            try:
                bild = base64.b64decode(roh, validate=False)
            except Exception:
                raise ValueError("Das Foto ist unterwegs beschädigt worden. "
                                 "Bitte noch einmal aufnehmen.")
            if not bild:
                raise ValueError("Kein Bild angekommen.")
            if len(bild) > 8 * 1024 * 1024:
                raise ValueError("Das Foto ist zu groß. Bitte noch einmal aufnehmen.")
            endung = next((e for marke, e in self.BILDMARKEN if bild.startswith(marke)), None)
            if not endung:
                raise ValueError("Das ist kein JPEG und kein PNG.")

            os.makedirs(FOTOS, exist_ok=True)
            # Zufaelliger Name: zwei gleichzeitige Aufnahmen duerfen sich nicht
            # gegenseitig ueberschreiben.
            name = "%d-%s-%s.%s" % (fahrzeug_id, art, secrets.token_hex(6), endung)
            ziel = os.path.join(FOTOS, name)
            with open(ziel, "wb") as datei:
                datei.write(bild)
            try:
                fahrzeuge.foto_anhaengen(verb, benutzer, fahrzeug_id, art, bild, name)
            except Exception:
                os.remove(ziel)          # kein Bild ohne Eintrag liegen lassen
                raise
            return self._senden(json.dumps(
                {"ok": True, "datei": name, "adresse": "/fotos/%s" % name}),
                "application/json; charset=utf-8")
        except (fahrzeuge.Abgelehnt, ValueError, KeyError, TypeError) as fehler:
            return self._senden(json.dumps({"ok": False, "fehler": str(fehler)}),
                                "application/json; charset=utf-8", 400)

    def _abrechnen(self, verb, benutzer):
        felder = self._felder()
        try:
            neu = rechnung.erzeugen(verb, benutzer,
                                    self._zahl(felder.get("autohaus_id"), "Autohaus"),
                                    felder.get("von", ""), felder.get("bis", ""))
            return self._umleiten("/rechnung/%d?gut=Entwurf+erzeugt." % neu["id"])
        except (rechnung.Abgelehnt, ValueError, KeyError) as fehler:
            return self._umleiten("/buero?von=%s&bis=%s&fehler=%s" % (
                urllib.parse.quote(felder.get("von", "")),
                urllib.parse.quote(felder.get("bis", "")),
                urllib.parse.quote(str(fehler))))

    def _rechnungsschritt(self, verb, benutzer, kennung, schritt):
        ziel = "/rechnung/%d" % kennung
        try:
            if schritt == "freigeben":
                rechnung.freigeben(verb, benutzer, kennung)
                return self._umleiten("%s?gut=Freigegeben." % ziel)
            if schritt == "ausstellen":
                fertig = rechnung.finalisieren(verb, benutzer, kennung, self.anwendung.dienst)
                return self._umleiten("%s?gut=%s" % (ziel, urllib.parse.quote(
                    "Ausgestellt als %s." % (fertig["lexware_nummer"] or fertig["lexware_id"]))))
            rechnung.stornieren(verb, benutzer, kennung, self._felder().get("grund", ""))
            return self._umleiten("/buero?gut=Entwurf+verworfen.")
        except (rechnung.Abgelehnt, lexware.LexwareFehler, ValueError) as fehler:
            return self._umleiten("%s?fehler=%s" % (ziel, urllib.parse.quote(str(fehler))))

    def _stammdaten(self, verb, benutzer, was):
        felder = self._felder()
        try:
            if was == "autohaus":
                verb.execute(
                    "INSERT INTO autohaus (name, kurz, strasse, plz, ort, kaeuferreferenz,"
                    " lieferantennummer, lexware_kontakt, zahlungsziel) VALUES (?,?,?,?,?,?,?,?,?)",
                    (felder["name"].strip(), felder["kurz"].strip().upper(),
                     felder.get("strasse", "").strip(), felder.get("plz", "").strip(),
                     felder.get("ort", "").strip(), felder.get("kaeuferreferenz", "").strip(),
                     felder.get("lieferantennummer", "").strip(),
                     felder.get("lexware_kontakt", "").strip(),
                     self._zahl(felder.get("zahlungsziel") or 14, "Zahlungsziel")))
                protokoll.notieren(verb, benutzer, "Autohaus angelegt", felder["name"])
            elif was == "leistung":
                verb.execute(
                    "INSERT INTO leistung (bezeichnung, netto_cent) VALUES (?,?)",
                    (felder["bezeichnung"].strip(), datenbank.cent(felder["preis"])))
                protokoll.notieren(verb, benutzer, "Leistung angelegt", felder["bezeichnung"])
            elif was == "preis":
                verb.execute(
                    "INSERT INTO preis (autohaus_id, leistung_id, netto_cent) VALUES (?,?,?)"
                    " ON CONFLICT(autohaus_id, leistung_id) DO UPDATE SET netto_cent=excluded.netto_cent",
                    (self._zahl(felder.get("autohaus_id"), "Autohaus"),
                     self._zahl(felder.get("leistung_id"), "Leistung"),
                     datenbank.cent(felder["preis"])))
                protokoll.notieren(verb, benutzer, "Sonderpreis gesetzt",
                                   "Autohaus %s" % felder["autohaus_id"], felder["preis"])
            elif was == "benutzer":
                verb.execute(
                    "INSERT INTO benutzer (name, anmeldename, passwort, rolle, angelegt_am)"
                    " VALUES (?,?,?,?,?)",
                    (felder["name"].strip(), felder["anmeldename"].strip().lower(),
                     sicherheit.passwort_hashen(felder["passwort"]),
                     felder.get("rolle", "erfasser"), datenbank.jetzt()))
                protokoll.notieren(verb, benutzer, "Zugang angelegt", felder["name"])
            else:
                raise ValueError("Unbekannt.")
            return self._umleiten("/verwaltung?gut=Gespeichert.")
        except KeyError as fehler:
            meldung = "Es fehlt eine Angabe: %s." % (fehler.args[0] if fehler.args else "")
        except sqlite3.IntegrityError as fehler:
            # "UNIQUE constraint failed: autohaus.kurz" ist englisch und nennt
            # den Tabellennamen. Der Betrieb liest stattdessen, was er tun kann.
            treffer = next((t for s, t in DOPPELT.items() if s in str(fehler)), None)
            meldung = treffer or "Das ließ sich so nicht speichern."
        except Exception as fehler:                        # noqa: BLE001
            meldung = str(fehler)
        return self._umleiten("/verwaltung?fehler=%s" % urllib.parse.quote(meldung))

    BLOCK = 64 * 1024

    def _export(self, verb, benutzer):
        """Alles, was der Betrieb hat, als eine Datei zum Mitnehmen.

        Die Datei wird **blockweise** gesendet, nicht am Stück in den
        Arbeitsspeicher geladen. Bei 95 Fahrzeugen die Woche kommen mit Fotos im
        Jahr mehrere Gigabyte zusammen; ein `read()` darauf killt den Dienst auf
        einem kleinen Server.
        """
        # Die Datei entsteht im Betriebsordner, NICHT unter /tmp. Der Dienst
        # laeuft mit `PrivateTmp=yes`, und auf vielen Systemen liegt /tmp im
        # Arbeitsspeicher. Ein Jahr Fotos sind mehrere Gigabyte: der Export
        # haette dann den Server umgebracht, den er sichern soll.
        os.makedirs(BETRIEB, exist_ok=True)
        griff, pfad = tempfile.mkstemp(suffix=".zip", prefix="hallenbuch-export-",
                                       dir=BETRIEB)
        os.close(griff)
        try:
            export.schreiben(verb, pfad, FOTOS, benutzer)
            groesse = os.path.getsize(pfad)
            self._antwort_begonnen = True
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(groesse))
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % export.dateiname())
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                with open(pfad, "rb") as datei:
                    while True:
                        block = datei.read(self.BLOCK)
                        if not block:
                            break
                        self.wfile.write(block)
        finally:
            try:
                os.remove(pfad)
            except OSError:
                pass

    def _datei(self, wurzel, name):
        # Der Praefixvergleich braucht das Trennzeichen: ohne es waere neben
        # `betrieb/fotos` auch `betrieb/fotos-alt` erreichbar, ein Nachbarordner
        # weit genug. Heute gibt es den nicht, morgen vielleicht.
        oben = os.path.abspath(wurzel)
        sicher = os.path.normpath(os.path.join(oben, name))
        if (sicher != oben and not sicher.startswith(oben + os.sep)) \
                or not os.path.isfile(sicher):
            return self._senden("Nicht gefunden.", "text/plain; charset=utf-8", 404)
        art = mimetypes.guess_type(sicher)[0] or "application/octet-stream"
        # Mit Fingerabdruck in der Adresse darf ewig zwischengespeichert werden,
        # ohne ihn muss jedes Mal nachgefragt werden. Sonst haengt eine Aenderung
        # am Aussehen im Browser fest.
        hat_marke = "v=" in (urllib.parse.urlparse(self.path).query or "")
        regel = ("public, max-age=31536000, immutable" if hat_marke
                 else "no-cache, must-revalidate")
        with open(sicher, "rb") as datei:
            return self._senden(datei.read(), art, zwischenspeicher=regel)


def port_lesen(text) -> int:
    """Die Portangabe aus der Umgebung, mit einer Meldung statt eines Rückverfolgs."""
    roh = str(text or "8080").strip()
    if not roh.isdigit() or not 1 <= int(roh) <= 65535:
        raise SystemExit(
            "HALLENBUCH_PORT ist keine gültige Portnummer: %r\n"
            "Erwartet wird eine Zahl zwischen 1 und 65535, üblich ist 8080." % text)
    return int(roh)


def starten(port: int = 8080, db_pfad=None, dienst=None):
    Griff.anwendung = Hallenbuch(db_pfad, dienst)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Griff)
    print("Hallenbuch läuft auf http://127.0.0.1:%d  (%s)"
          % (port, "Probebetrieb" if Griff.anwendung.probebetrieb else "mit Rechnungsprogramm"))
    # Solange der Dienst laeuft, liegt eine Marke neben der Datenbank. Das
    # Wiederherstellungswerkzeug weigert sich dann — sonst taeuscht es Erfolg
    # vor, waehrend der laufende Dienst in eine geloeschte Datei weiterschreibt.
    marke = sicherung.marke_versuchen(os.path.dirname(Griff.anwendung.db_pfad) or ".")
    try:
        httpd.serve_forever()
    finally:
        sicherung.marke_loeschen(marke)

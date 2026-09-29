"""Anbindung an Lexware Office (Public API, Tarif XL).

Warum überhaupt ein fremdes Rechnungsprogramm: GoBD verlangt Unveränderbarkeit
und Aufbewahrung. Das Hallenbuch ist die Erfassung vorne, nie die Buchfuehrung.
Der Beleg entsteht dort, wo er auch zehn Jahre liegen bleibt, und geht von dort
über den DATEV-Rechnungsdatenservice zum Steuerberater.

Geprueft am 17. und 21.09.2026 gegen developers.lexware.io:
  POST https://api.lexware.io/v1/invoices?finalize=true
  POST https://api.lexware.io/v1/credit-notes?finalize=true
  Authorization: Bearer <schluessel>, 2 Anfragen je Sekunde, 429 bei Überschreitung
  Sammelrechnung als Leistungszeitraum: shippingType "serviceperiod"
  E-Rechnung: xRechnung.buyerReference, xRechnung.vendorNumberAtCustomer
  Rund 300 Positionen je Beleg. Fotoanhaenge an Rechnungen gibt es nicht.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

BASIS = os.environ.get("LEXWARE_BASIS", "https://api.lexware.io")
ANFRAGEN_JE_SEKUNDE = 2.0


class LexwareFehler(RuntimeError):
    """Fehler beim Rechnungsprogramm.

    `sicher_nicht_angelegt` ist der wichtige Teil. Wird die Anfrage abgelehnt
    (HTTP 4xx), ist sicher nichts entstanden und ein zweiter Versuch ist
    gefahrlos. Bricht dagegen die Verbindung ab oder läuft die Zeit aus, kann
    der Beleg drüben **schon angelegt** sein — dann legt ein blinder zweiter
    Versuch eine zweite Rechnung mit eigener Nummer an, und das Autohaus
    bekommt dieselbe Leistung zweimal berechnet.
    """

    def __init__(self, text, sicher_nicht_angelegt: bool = False):
        super().__init__(text)
        self.sicher_nicht_angelegt = sicher_nicht_angelegt


def _iso_tag(datum: str) -> str:
    """'2026-09-21' -> '2026-09-21T00:00:00.000+02:00' in Ortszeit."""
    roh = datetime.strptime(str(datum)[:10], "%Y-%m-%d")
    versatz = datetime.now().astimezone().utcoffset() or timedelta(0)
    zeichen = "+" if versatz.total_seconds() >= 0 else "-"
    minuten = int(abs(versatz.total_seconds()) // 60)
    return "%sT00:00:00.000%s%02d:%02d" % (
        roh.strftime("%Y-%m-%d"), zeichen, minuten // 60, minuten % 60)


def _euro(cent: int) -> float:
    return round(int(cent) / 100.0, 2)


def _adresse(kopf) -> dict:
    if kopf["lexware_kontakt"]:
        return {"contactId": kopf["lexware_kontakt"]}
    return {
        "name": kopf["autohaus"],
        "street": kopf["strasse"] or "",
        "zip": kopf["plz"] or "",
        "city": kopf["ort"] or "",
        "countryCode": kopf["land"] or "DE",
    }


def _steuer(satz: int) -> dict:
    if int(satz) == 0:
        # Kleinunternehmer nach § 19 UStG. Lexware kennt dafuer "vatfree".
        # Vorher stand hier "net" mit einer Randnotiz: das ergibt einen Beleg
        # mit 0 % Umsatzsteuer statt einer steuerfreien Rechnung, und die
        # Begruendung steht nur im Kleingedruckten.
        return {"taxType": "vatfree",
                "taxTypeNote": "Kein Ausweis von Umsatzsteuer nach § 19 UStG."}
    return {"taxType": "net"}


def _xrechnung(kopf) -> dict:
    daten = {}
    if kopf["kaeuferreferenz"]:
        daten["buyerReference"] = kopf["kaeuferreferenz"]
    if kopf["lieferantennummer"]:
        daten["vendorNumberAtCustomer"] = kopf["lieferantennummer"]
    return daten


def _ohne_fin(text, fin) -> str:
    """Der Positionstext traegt die FIN mit. In der Beschreibung steht sie dann
    ein zweites Mal, weil sie schon die Ueberschrift der Position ist.

    Im ersten echten Ausstelllauf am 23.09. am zurueckgeholten Beleg gesehen:
    Jede Zeile las sich „WVWZZZ1JZ3W386752 2026-09-18 / 2026-09-18 |
    WVWZZZ1JZ3W386752 | Vollaufbereitung". Bei 95 Fahrzeugen sind das 95 Zeilen
    doppelter Text auf einem Beleg, den ein Autohaus durchsieht. Die Bueroseite
    macht das seit jeher richtig; der Beleg nach draussen nicht.
    """
    return " | ".join(t for t in str(text or "").split(" | ") if t != fin)


def rechnungskoerper(kopf, posten) -> dict:
    """Baut den Beleg. Getrennt vom Versand, damit er ohne Netz pruefbar ist."""
    satz = int(kopf["steuersatz"])
    zeilen = []
    for p in posten:
        zeilen.append({
            "type": "custom",
            "name": p["fin"],
            "description": _ohne_fin(p["text"], p["fin"]),
            "quantity": 1,
            "unitName": "Fahrzeug",
            "unitPrice": {
                "currency": "EUR",
                "netAmount": _euro(p["netto_cent"]),
                "taxRatePercentage": satz,
            },
        })
    ziel = int(kopf["zahlungsziel"] or 14)
    koerper = {
        # Das Rechnungsdatum ist der AUSSTELLUNGSTAG (§ 14 Abs. 4 Nr. 3 UStG),
        # nicht das Ende des Leistungszeitraums. Vorher stand hier `bis`, und
        # damit war jede Rechnung rueckdatiert: Wer montags die Vorwoche
        # abrechnet, bekam den Sonntag als Rechnungsdatum. Ueber einen
        # Monatswechsel hinweg landet der Beleg dadurch im falschen
        # Voranmeldungszeitraum. Der Leistungszeitraum steht unten.
        "voucherDate": _iso_tag(datetime.now().strftime("%Y-%m-%d")),
        "address": _adresse(kopf),
        "lineItems": zeilen,
        "totalPrice": {"currency": "EUR"},
        "taxConditions": _steuer(satz),
        "shippingConditions": {
            "shippingType": "serviceperiod",
            "shippingDate": _iso_tag(kopf["von"]),
            "shippingEndDate": _iso_tag(kopf["bis"]),
        },
        # Das Zahlungsziel wird GESETZT, nicht nur behauptet. Vorher stand der
        # Satz "Zahlbar innerhalb von 14 Tagen" nur im Bemerkungsfeld, waehrend
        # Lexware die Faelligkeit aus der Voreinstellung des Mandanten rechnete.
        # Am echten Testkonto gemessen: dort steht "Zahlbar sofort, rein netto"
        # mit null Tagen. Der Beleg haette also 14 Tage versprochen und nach
        # null Tagen gemahnt.
        "paymentConditions": {
            "paymentTermLabel": "Zahlbar innerhalb von %d Tagen ohne Abzug" % ziel,
            "paymentTermDuration": ziel,
        },
        "title": "Sammelrechnung",
        "introduction": "Fahrzeugaufbereitung im Zeitraum %s bis %s."
                        % (str(kopf["von"])[:10], str(kopf["bis"])[:10]),
        "remark": "Sammelrechnung über %d Fahrzeuge." % len(zeilen),
    }
    xr = _xrechnung(kopf)
    if xr:
        koerper["xRechnung"] = xr
    return koerper


def gutschriftskoerper(zeile, grund: str) -> dict:
    satz = int(zeile["steuersatz"])
    koerper = {
        "voucherDate": _iso_tag(datetime.now().strftime("%Y-%m-%d")),
        "address": _adresse(zeile),
        "lineItems": [{
            "type": "custom",
            "name": "Gutschrift %s" % zeile["fin"],
            "description": "%s | Grund: %s | Bezug: Rechnung %s"
                           % (_ohne_fin(zeile["positionstext"], zeile["fin"]),
                              grund, zeile["lexware_nummer"] or "-"),
            "quantity": 1,
            "unitName": "Fahrzeug",
            "unitPrice": {
                "currency": "EUR",
                "netAmount": _euro(zeile["positionsbetrag"]),
                "taxRatePercentage": satz,
            },
        }],
        "totalPrice": {"currency": "EUR"},
        "taxConditions": _steuer(satz),
        "title": "Gutschrift",
        "introduction": "Gutschrift zu Rechnung %s." % (zeile["lexware_nummer"] or ""),
    }
    xr = _xrechnung(zeile)
    if xr:
        koerper["xRechnung"] = xr
    return koerper


class Lexware:
    """Echter Zugang. Haelt die zwei Anfragen je Sekunde ein."""

    def __init__(self, schluessel: str = None, basis: str = None):
        self.schluessel = schluessel or os.environ.get("LEXWARE_SCHLUESSEL", "")
        self.basis = (basis or BASIS).rstrip("/")
        self._sperre = threading.Lock()
        self._zuletzt = 0.0
        self._profil = None

    @property
    def bereit(self) -> bool:
        return bool(self.schluessel)

    def _bremsen(self):
        with self._sperre:
            abstand = 1.0 / ANFRAGEN_JE_SEKUNDE
            vergangen = time.time() - self._zuletzt
            if vergangen < abstand:
                time.sleep(abstand - vergangen)
            self._zuletzt = time.time()

    def _rufen(self, weg: str, koerper=None, methode: str = "GET", versuche: int = 3):
        if not self.bereit:
            raise LexwareFehler(
                "Kein Lexware-Schlüssel hinterlegt. Bis der Zugang da ist, läuft das "
                "Hallenbuch im Probebetrieb und stellt nichts aus.", True)
        daten = json.dumps(koerper).encode("utf-8") if koerper is not None else None
        anfrage = urllib.request.Request(
            "%s%s" % (self.basis, weg), data=daten, method=methode,
            headers={
                "Authorization": "Bearer %s" % self.schluessel,
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
        letzter = None
        for versuch in range(versuche):
            self._bremsen()
            try:
                with urllib.request.urlopen(anfrage, timeout=30) as antwort:
                    roh = antwort.read().decode("utf-8") or "{}"
                    return json.loads(roh)
            except urllib.error.HTTPError as fehler:
                text = fehler.read().decode("utf-8", "replace")
                # 400 Zeichen waren zu knapp: Am 23.09. schnitt genau diese
                # Grenze die Liste der fehlenden Firmenangaben ab, also den
                # einzigen Teil der Meldung, der dem Buero sagt, was zu tun
                # ist. Die Spalte `letzter_fehler` nimmt 1000 Zeichen; mehr
                # abzuschneiden als noetig hilft niemandem.
                letzter = "HTTP %s: %s" % (fehler.code, text[:1000])
                if fehler.code in (429, 500, 502, 503, 504) and versuch < versuche - 1:
                    time.sleep(1.5 * (versuch + 1))
                    continue
                # 4xx heisst: abgelehnt, nichts angelegt. 5xx und 429 lassen
                # offen, ob drueben schon etwas entstanden ist.
                raise LexwareFehler(letzter, 400 <= fehler.code < 500
                                    and fehler.code != 429)
            except urllib.error.URLError as fehler:
                letzter = "Keine Verbindung: %s" % fehler.reason
                if versuch < versuche - 1:
                    time.sleep(1.5 * (versuch + 1))
                    continue
                raise LexwareFehler(letzter, False)
            except (TimeoutError, OSError) as fehler:
                letzter = "Zeitüberschreitung: %s" % fehler
                if versuch < versuche - 1:
                    time.sleep(1.5 * (versuch + 1))
                    continue
                raise LexwareFehler(letzter, False)
        raise LexwareFehler(letzter or "Unbekannter Fehler", False)

    def pruefen(self) -> dict:
        self._profil = self._rufen("/v1/profile")
        return self._profil

    def _mandant(self) -> dict:
        """Das Profil des Mandanten, einmal je Prozess geholt."""
        if self._profil is None:
            self._profil = self._rufen("/v1/profile")
        return self._profil

    def _steuer_passt(self, satz: int):
        """Bricht ab, wenn Mandant und Beleg sich widersprechen.

        Das Profil des Mandanten sagt, ob er Kleinunternehmer ist. Steht dort
        wahr und der Beleg weist 19 % aus, waere die Rechnung um 19 % falsch —
        und das faellt erst beim Steuerberater auf, nach Wochen, ueber alle
        Belege hinweg. Deshalb hier, vor dem Aufruf, mit klarer Absage.
        """
        try:
            profil = self._mandant()
        except LexwareFehler:
            return      # Kein Profil, kein Urteil. Der Aufruf selbst meldet den Fehler.
        klein = bool(profil.get("smallBusiness"))
        if klein and int(satz) != 0:
            raise LexwareFehler(
                "Das Rechnungsprogramm ist als Kleinunternehmer eingerichtet, dieser Beleg "
                "weist aber %d %% Umsatzsteuer aus. Es wurde nichts ausgestellt. Bitte "
                "klären, was stimmt." % int(satz), True)
        if not klein and int(satz) == 0:
            raise LexwareFehler(
                "Das Rechnungsprogramm ist umsatzsteuerpflichtig eingerichtet, dieser Beleg "
                "weist aber keine Umsatzsteuer aus. Es wurde nichts ausgestellt. Bitte "
                "klären, was stimmt.", True)

    def _belegnummer(self, weg: str, kennung: str) -> str:
        try:
            voll = self._rufen("%s/%s" % (weg, kennung))
            return voll.get("voucherNumber", "")
        except LexwareFehler:
            return ""

    def rechnung_ausstellen(self, kopf, posten) -> dict:
        self._steuer_passt(kopf["steuersatz"])
        antwort = self._rufen("/v1/invoices?finalize=true",
                              rechnungskoerper(kopf, posten), "POST")
        kennung = antwort.get("id", "")
        return {"id": kennung,
                "uri": antwort.get("resourceUri", ""),
                "nummer": self._belegnummer("/v1/invoices", kennung) if kennung else ""}

    def gutschrift_ausstellen(self, zeile, grund: str) -> dict:
        self._steuer_passt(zeile["steuersatz"])
        antwort = self._rufen("/v1/credit-notes?finalize=true",
                              gutschriftskoerper(zeile, grund), "POST")
        kennung = antwort.get("id", "")
        return {"id": kennung,
                "uri": antwort.get("resourceUri", ""),
                "nummer": self._belegnummer("/v1/credit-notes", kennung) if kennung else ""}


class Attrappe:
    """Probebetrieb: baut denselben Beleg, verschickt ihn aber nicht.

    Dafür da, dass der Ablauf vollständig geprobt werden kann, bevor der
    Lexware-Zugang des Betriebs existiert. Jeder Koerper wird mitgeschrieben,
    damit er gegengelesen werden kann.
    """

    def __init__(self):
        self.belege = []
        self._zaehler = 0

    bereit = True

    def _naechste(self, vorsatz: str) -> str:
        self._zaehler += 1
        return "%s-PROBE-%04d" % (vorsatz, self._zaehler)

    def rechnung_ausstellen(self, kopf, posten) -> dict:
        koerper = rechnungskoerper(kopf, posten)
        nummer = self._naechste("RE")
        self.belege.append(("rechnung", nummer, koerper))
        return {"id": "probe-%s" % nummer.lower(), "nummer": nummer, "uri": ""}

    def gutschrift_ausstellen(self, zeile, grund: str) -> dict:
        koerper = gutschriftskoerper(zeile, grund)
        nummer = self._naechste("GU")
        self.belege.append(("gutschrift", nummer, koerper))
        return {"id": "probe-%s" % nummer.lower(), "nummer": nummer, "uri": ""}

    def pruefen(self) -> dict:
        return {"companyName": "Probebetrieb ohne Lexware-Zugang"}


def dienst():
    """Echter Zugang wenn ein Schlüssel da ist, sonst Probebetrieb."""
    echt = Lexware()
    return echt if echt.bereit else Attrappe()

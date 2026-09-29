# Hallenbuch

Erfassung am Fahrzeug, Sammelrechnung je Autohaus. Ein Mitarbeiter scannt oder
tippt in der Halle die FIN, wählt Autohaus und Leistung — mehr nicht. Das Büro
erzeugt daraus auf Knopfdruck **eine** Rechnung je Autohaus und Zeitraum, als
E-Rechnung nach EN 16931, über ein bestehendes Rechnungsprogramm.

**Gebaut im Kundenauftrag** für eine Fahrzeugaufbereitung mit rund 95 Fahrzeugen
in der Woche, die ihre Rechnungen bis dahin von Hand schrieb — ein bis zwei
Stunden am Tag. Auftrag vom 16.09.2026, Auftragswert 1.000 €, Abschlagsrechnung
2026-001 gestellt. Das System läuft im Probebetrieb; scharf geschaltet wird es,
sobald der Betrieb seine Zugänge und Preislisten beigebracht hat (siehe „Noch
offen" am Ende).

|  |  |
|---|---|
| Umfang | 4.786 Zeilen Python, 18 Module |
| Tests | **329**, dazu 58 Prüfungen der Warteschlange in Node |
| Abhängigkeiten zur Laufzeit | **keine** — reine Standardbibliothek, ab Python 3.9 |
| Betrieb | systemd-Einheit, nächtliche Sicherung mit Sofortprüfung, Wiederherstellung mit Sperre |
| Fremde Systeme | genau eines: die Lexware-Office-API |

Der interessante Teil an diesem Projekt ist nicht der Code, sondern was
**bewusst nicht** gebaut wurde: kein eigenes Rechnungsprogramm (GoBD), kein
automatischer Versand ohne Freigabe, keine zugesagte Trefferquote für die
Texterkennung, solange sie nicht an echten Fahrzeugen gemessen ist. Die
Begründungen stehen jeweils an Ort und Stelle.

## Was hier nicht liegt

Keine Betriebsdaten, keine Datenbank, keine Fotos, keine Zugangsschlüssel und
keine Kundenadressen. `betrieb/` und `.env` sind ausgeschlossen; die Vorlage
`.env.template` zeigt, welche Werte der Betrieb selbst setzt. Der
Lexware-Zugang gehört dem Betrieb, nie dem Dienstleister.

## Was es tut

1. Mitarbeiter erfasst am Auto die FIN, wählt Autohaus und Leistung, fertig.
2. Das Büro sieht die offenen Fahrzeuge je Autohaus und erzeugt auf Knopfdruck
   eine Sammelrechnung für einen Zeitraum.
3. Die Rechnung entsteht **immer erst als Entwurf**. Erst nach Freigabe wird sie
   im Rechnungsprogramm (Lexware Office) als E-Rechnung nach EN 16931
   ausgestellt, mit Käuferreferenz des Autohauses.
4. Wird ein einzelnes Fahrzeug reklamiert, entsteht dafür eine Gutschrift. Die
   Rechnung selbst bleibt unverändert.
5. Von dort geht es über den DATEV-Rechnungsdatenservice zum Steuerberater.
   Dieser Weg wird eingerichtet, nicht gebaut.

## Warum kein eigenes Rechnungsprogramm

GoBD verlangt Unveränderbarkeit, Aufbewahrung und einen Audit-Trail. Das
Hallenbuch ist die Erfassung vorne, nie die Buchführung. Der Beleg entsteht dort,
wo er zehn Jahre liegen bleibt. Entscheidung vom 16.09.2026, nicht neu aufrollen.

## Stapel

Reine Python-Standardbibliothek, kein pip, kein Build.
Läuft ab Python 3.9. SQLite als Datenbank, `http.server` als Server, HTML aus
Python, Schriften liegen lokal. Der Dienst ruft im Betrieb nur einen fremden
Server auf: die Lexware-API.

## Starten

```
python3 werkzeuge/einrichten.py --name "Vorname" --anmeldename kuerzel --rolle buero
# fragt das Passwort ab; auf der Befehlszeile stuende es in der Shell-Historie
python3 start.py                       # http://127.0.0.1:8080
```

Zum Ausprobieren mit Beispieldaten: `--beispiel` anhängen.

Einstellungen über `.env` (Vorlage: `.env.template`). Ohne `LEXWARE_SCHLUESSEL`
läuft alles im **Probebetrieb**: Belege werden vollständig aufgebaut und sind
prüfbar, gehen aber nicht raus. Das ist der Zustand für die Parallelwoche.

## Tests

```
python3 -m unittest discover -s tests
```

329 Tests plus 58 Prüfungen der Warteschlange in Node. Sie sind die
Abnahmekriterien aus dem Lieferschnitt, in Code. Die Zahlen und jeder hier
genannte Testname werden von `tests/test_dokumente.py` gegen die Wirklichkeit
geprüft: Wer einen Test umbenennt oder löscht, ohne diese Liste anzufassen,
bekommt einen roten Lauf statt einer stillen Lücke.

| Zusage | Test |
|---|---|
| Kein Fahrzeug doppelt auf einer Rechnung | `test_fahrzeug_kann_nie_auf_zwei_rechnungen_stehen`, `test_positionen_sind_auf_datenbankebene_eindeutig` |
| Keine Rechnung doppelt erzeugt | `test_zweite_rechnung_fuer_denselben_zeitraum_wird_abgelehnt` |
| Nachzügler gehen nicht verloren | `test_nachzuegler_aus_abgerechneter_woche_geht_nicht_verloren` |
| Übersicht zeigt, was abgerechnet wird | `test_uebersicht_zeigt_genau_das_was_abgerechnet_wird` |
| Getippte Preise werden richtig gelesen | `test_der_tausenderpunkt_wird_nicht_zum_komma` |
| Gefälschte Sitzungen werden abgewiesen | `test_eine_veraenderte_benutzernummer_wird_abgewiesen` |
| Eine Sicherung lässt sich zurückspielen | `test_eingespielt_wird_der_stand_der_sicherung` |
| Keine Rechnung ohne Anschrift des Empfängers | `test_ohne_anschrift_geht_keine_rechnung_raus` |
| Stammdaten lassen sich korrigieren | `test_eine_falsche_anschrift_laesst_sich_korrigieren` |
| Sonderpreise sind sichtbar und entfernbar | `test_ein_vergessener_sonderpreis_ueberlebt_die_preiserhoehung` |
| Fehler beim Ausstellen richtig eingestuft | `test_ein_vierhunderter_heisst_nichts_angelegt`, `test_ein_fuenfhunderter_laesst_es_offen_und_wird_wiederholt` |
| Die Warteschlange gehört dem Zugang, nicht dem Handy | `tests/warteschlange.mjs`, Block 5c |
| 20 Fahrzeuge, je Autohaus eine Rechnung | `test_zwanzig_fahrzeuge_zwei_autohaeuser_zwei_rechnungen` |
| Echte Woche: 95 Fahrzeuge, 8 Autohäuser, 8 Belege | `test_echte_woche_95_fahrzeuge_8_autohaeuser` |
| Gutschrift für ein Fahrzeug, Rest unberührt | `test_gutschrift_fuer_fahrzeug_sieben_von_zwanzig` |
| Ausgestellte Rechnung ist unveränderbar | `test_ausgestellte_rechnung_laesst_sich_nicht_stornieren`, `test_preisaenderung_am_fahrzeug_aendert_die_rechnung_nicht_mehr` |
| Abbruch mitten im Speichern hinterlässt nichts | `test_abbruch_mitten_im_erzeugen_hinterlaesst_nichts` |
| Prüfziffer sperrt keine europäische FIN | `test_europaeische_fin_ohne_gueltige_pruefziffer_geht_durch` |
| Käuferreferenz landet in der E-Rechnung | `test_kaeuferreferenz_landet_in_der_erechnung` |
| Käuferreferenz nur mit Lexware-Kontakt | `test_ohne_kontakt_wird_die_freigabe_abgelehnt` |
| Filialen, Wochenwechsel, 300-Positionen-Grenze | `test_filiale_*`, `test_fahrzeug_ueber_den_wochenwechsel_*`, `test_zu_viele_positionen_*` |
| Eine fehlgeschlagene Gutschrift hinterlässt eine Spur | `test_unklare_lage_blockiert_den_blinden_zweiten_versuch` |
| Ein abgestürzter Ausstellversuch sperrt nicht für immer | `test_ein_alter_anspruch_sperrt_nicht_fuer_immer` |
| Ein späteres sauberes Nein löscht den früheren Zweifel nicht | `test_ein_sauberes_nein_loescht_den_zweifel_des_ersten_versuchs_nicht` |
| Zweimal dieselbe Sendung ergibt ein Fahrzeug | `test_die_verliererin_bekommt_das_vorhandene_fahrzeug` |
| Zwei im Büro überschreiben sich nicht | `test_die_zweite_speicherung_ueberschreibt_die_erste_nicht` |
| Rollen, fremde Herkunft, Pfad-Ausbruch | `test_erfasser_darf_nicht_ins_buero`, `test_fremde_herkunft_wird_abgewiesen`, `test_statische_dateien_und_gesundheit` |

Zwei Zusagen stehen zusätzlich als eindeutige Schlüssel in der Datenbank
(`ix_rechnung_einmalig`, `ix_position_fahrzeug`). Sie halten auch bei Doppelklick,
zwei Browsern und einem Prozesstod mitten im Speichern.

## Betrieb

`hallenbuch.service` ist die systemd-Einheit für `/opt/hallenbuch`.
Der Einführungsablauf beim Betrieb gehört zu den Projektunterlagen und liegt
nicht in diesem Repository.

```
python3 werkzeuge/sichern.py              # nächtlich per Cron, prüft die Sicherung sofort
python3 werkzeuge/wiederherstellen.py     # zeigt die Sicherungen, spielt erst mit --ja ein
```

Solange der Dienst läuft, liegt eine Marke mit seiner Prozessnummer neben der
Datenbank; das Wiederherstellen verweigert dann den Dienst. Die nächtliche
Sicherung meldet ausserdem, wenn auf der Platte weniger als 15 Prozent frei sind.

**Die Fotos liegen nicht in der Sicherung.** Sie stehen in `betrieb/fotos/`.
Wöchentlich wird deshalb der **ganze** Ordner `betrieb/` abgezogen, nicht nur
`betrieb/sicherungen/`. Das Einspielen schreibt sich selbst ins Protokoll,
damit eine zurückgesetzte Lücke später erklärbar ist.
Die beiden Anleitungen für den Betrieb liegen in `anleitungen/` (Halle eine
Seite, Büro zwei), gemeinsame Gestaltung in `anleitungen/anleitung.css`.
Davor gehört ein Caddy oder nginx mit TLS; der Dienst lauscht nur auf 127.0.0.1.
Datenbank und Fotos liegen in `betrieb/`. Sicherung: dieser eine Ordner.

## Design-Brief (vor dem Bau festgelegt)

1. **Konzept:** ein Laufzettel am Fahrzeug, kein Formular am Schreibtisch.
2. **Register:** Werkstatt. Hoher Kontrast, große Flächen, nichts Dekoratives.
   Bedienbar mit Handschuhen, bei Tageslicht, in unter zwanzig Sekunden.
3. **Schrift:** Saira Condensed 900 in Versalien als Anzeige, Atkinson Hyperlegible
   für Text (ausdrücklich auf Lesbarkeit unter schlechten Bedingungen entworfen),
   Azeret Mono für FIN und Beträge. Alle drei liegen lokal, kein Google-Aufruf.
   **Versalien nur für kurze Etiketten und Überschriften, nie für Sätze** — ganze
   Sätze in Großbuchstaben sind messbar schlechter lesbar.
4. **Farbe: die Marke des Betriebs**, gemessen am Schriftzug seiner Visitenkarte:
   **Cyan `#3FBFDF` auf Schwarz**. Der Markenton
   steht nur auf dunklem Grund — auf Weiß hat er 2:1 und wäre als Text unlesbar.
   Für Aktion und Text auf hellem Grund dient derselbe Farbton abgedunkelt
   (`#0B6B85`, 5,1:1 auf dem Arbeitsgrund, 6,1:1 unter weißer Schrift). Rot
   `#8C2F39` steht bewusst außerhalb der Marke und markiert nur Gutschrift und
   Storno, weil eine Warnung nicht wie eine normale Aktion aussehen darf.
5. **Layout:** Handy einspaltig mit fester Daumenleiste unten, Büro zweispaltig
   mit echter Tabelle. Keine zentrierte Symmetrie.
6. **Signature-Detail:** die FIN als gruppierter Mono-Block (WMI, VDS, VIS) auf
   jeder Karte. Primäre Aktion am Handy: Fahrzeug erfassen. Im Büro:
   Sammelrechnung erzeugen.

Stufe: **refined-conventional**. Das Ding wird jeden Tag benutzt, tägliche
Nutzbarkeit schlägt Neuheit. Detektor (`impeccable detect`): 0 Befunde über alle
sechs Oberflächen.

## Noch offen, hängt am Betrieb

Ohne diese Angaben läuft das System im Probebetrieb weiter, aber es stellt nichts aus:

- Lexware Office XL **auf den Betrieb**, Zugangsschlüssel für die API.
- Die echte Leistungs- und Preisliste, dazu drei echte Rechnungen der letzten Woche.
- Liste der Autohäuser mit Anschrift und, wo vorhanden, Käuferreferenz.
- Steuerberater: Name, Berater- und Mandantennummer, Rechnungsdatenservice beantragt.
- Antworten auf die vier Fragen aus dem Lieferschnitt: Jahresumsatz über 800.000 €,
  Netz im hinteren Hallenteil, Zahl der Rechnungsempfänger, heutiges Rechnungsprogramm.

## Bewusst nicht gebaut

E-Mail-Sekretär (anderer Auftrag, anderer Betrieb) · Fotoanhänge an der Rechnung
(kann Lexware nicht) · Anbindung an die Systeme der Autohäuser · Mahnwesen,
Zahlungsabgleich, Auswertungen · Fahrzeugdaten aus der FIN nachschlagen
(EU-Daten kosten Geld) · App in den Stores.

**Texterkennung am Typenschild:** Der Barcode wird gelesen, wo der Browser
`BarcodeDetector` kann. Reine Texterkennung ohne Barcode ist noch nicht gebaut;
Tippen bleibt der gleichwertige Weg. Eine Trefferquote wird erst zugesagt,
nachdem an echten Fahrzeugen gemessen wurde.

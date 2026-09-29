"""HTML-Erzeugung. Bewusst ohne Vorlagenbibliothek, wie der Rest des Stapels."""
from __future__ import annotations

import hashlib
import os
from html import escape as e

from .datenbank import euro
from .fahrzeuge import ART_NAME, ARTEN
from .stammdaten import STAND_AUTOHAUS, STAND_LEISTUNG
from .fin import gruppiert

_STATISCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "statisch")


def _fingerabdruck() -> str:
    """Kurzer Hash ueber die ausgelieferten Dateien.

    Haengt als ?v= an jeder Adresse. Aendert sich das Aussehen, aendert sich die
    Adresse, und Browser wie Service Worker holen zwangslaeufig die neue Fassung.
    Ohne das liefert ein einmal gefuellter Zwischenspeicher ewig das alte Design.
    """
    h = hashlib.sha256()
    for name in ("stil.css", "app.js", "schriften.css"):
        try:
            with open(os.path.join(_STATISCH, name), "rb") as datei:
                h.update(datei.read())
        except OSError:
            pass
    return h.hexdigest()[:10]


MARKE = _fingerabdruck()

NAVIGATION = (
    ("/erfassen", "Erfassen", ("erfasser", "buero")),
    ("/fahrzeuge", "Fahrzeuge", ("erfasser", "buero")),
    ("/suche", "Suche", ("erfasser", "buero")),
    ("/buero", "Abrechnen", ("buero",)),
    ("/verwaltung", "Stammdaten", ("buero",)),
    ("/protokoll", "Protokoll", ("buero",)),
)


def huelle(titel, inhalt, benutzer=None, aktiv="", leiste="", probebetrieb=False):
    navi = ""
    if benutzer:
        punkte = []
        for weg, beschriftung, rollen in NAVIGATION:
            if benutzer["rolle"] not in rollen:
                continue
            marke = ' aria-current="page"' if weg == aktiv else ""
            punkte.append('<a href="%s"%s>%s</a>' % (weg, marke, beschriftung))
        navi = "<nav>%s</nav>" % "".join(punkte)

    wer = ""
    if benutzer:
        wer = ('<div class="wer"><a href="/konto">%s</a><br>'
               '<a href="/abmelden">abmelden</a></div>' % e(benutzer["name"]))

    hinweis = ""
    if probebetrieb:
        hinweis = ('<div class="probe"><b>Probebetrieb</b> Kein Zugang zum '
                   'Rechnungsprogramm hinterlegt. Belege entstehen vollständig, '
                   'gehen aber nicht raus.</div>')

    return """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0C1116">
<title>%(titel)s</title>
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/statisch/symbole/apple-touch-icon.png">
<link rel="icon" href="/statisch/symbole/symbol-192.png" type="image/png">
<link rel="stylesheet" href="/statisch/stil.css?v=%(marke)s">
</head>
<body data-nutzer="%(nutzer)s">
<header class="kopf"><span class="firma">TD</span><span class="name">Hallenbuch</span>%(wer)s</header>
%(hinweis)s
%(navi)s
<main class="%(klasse)s">
%(inhalt)s
</main>
%(leiste)s
<div class="quittung" id="quittung" role="status" aria-live="polite" hidden></div>
<div class="fuss"><b>Hallenbuch</b>Fahrzeugerfassung und Sammelabrechnung</div>
<input type="file" accept="image/*" capture="environment" id="foto-datei" hidden>
<script src="/statisch/app.js?v=%(marke)s" defer></script>
</body>
</html>""" % {
        "titel": e(titel), "wer": wer, "navi": navi, "inhalt": inhalt,
        "nutzer": (benutzer["id"] if benutzer else 0), "marke": MARKE,
        "leiste": leiste, "hinweis": hinweis,
        "klasse": "mit-leiste" if leiste else "",
    }


def leerer_zustand(ueberschrift, erklaerung, knopf_weg="", knopf_text=""):
    """Ein leerer Zustand sagt, WARUM nichts da ist und WAS jetzt zu tun ist.
    Eine leere Fläche ohne Erklärung sieht aus wie ein Fehler."""
    knopf = ('<a class="knopf haupt" href="%s">%s</a>' % (knopf_weg, e(knopf_text))) \
        if knopf_weg else ""
    return ('<div class="leer"><b>%s</b><p>%s</p>%s</div>'
            % (e(ueberschrift), e(erklaerung), knopf))


def leere_zeile(spalten, text):
    return '<tr><td colspan="%d" class="leer-zeile">%s</td></tr>' % (spalten, e(text))


def meldungen_block(gute=(), schlechte=()):
    stuecke = []
    if schlechte:
        stuecke.append('<div class="meldung schlecht"><b>Geht nicht.</b><ul>%s</ul></div>'
                       % "".join("<li>%s</li>" % e(str(m)) for m in schlechte))
    if gute:
        stuecke.append('<div class="meldung gut"><b>Erledigt.</b><ul>%s</ul></div>'
                       % "".join("<li>%s</li>" % e(str(m)) for m in gute))
    return "".join(stuecke)


def anmelden(fehler=""):
    # Nur das kurze Etikett in Versalien, der Satz selbst läuft normal.
    warnung = ('<div class="meldung schlecht"><b>Achtung</b>%s</div>'
               % e(fehler)) if fehler else ""
    inhalt = """
<h1>Anmelden</h1>
<p class="unter">Jeder Mitarbeiter hat einen eigenen Zugang. So steht später im
Protokoll, wer welches Fahrzeug erfasst hat.</p>
%s
<form class="block" method="post" action="/anmelden">
  <label><span class="was">Name</span>
    <input name="anmeldename" autocomplete="username" autocapitalize="none" required autofocus></label>
  <label><span class="was">Passwort</span>
    <input name="passwort" type="password" autocomplete="current-password" required></label>
  <button class="haupt" type="submit">Anmelden</button>
</form>""" % warnung
    return huelle("Anmelden", inhalt)


def erfassen(benutzer, haeuser, leistungen, heute, letzte=(), gute=(), schlechte=(),
             vorbelegt=None, probebetrieb=False, fotos=None):
    vorbelegt = vorbelegt or {}
    haus_punkte = "".join(
        '<option value="%d"%s>%s</option>'
        % (h["id"], " selected" if str(h["id"]) == str(vorbelegt.get("autohaus_id", "")) else "",
           e(h["name"]))
        for h in haeuser)
    leistung_punkte = "".join(
        '<option value="%d"%s>%s &middot; %s EUR</option>'
        % (l["id"], " selected" if str(l["id"]) == str(vorbelegt.get("leistung_id", "")) else "",
           e(l["bezeichnung"]), euro(l["netto_cent"]))
        for l in leistungen)

    if not haeuser or not leistungen:
        fehlt = []
        if not haeuser:
            fehlt.append("Autohäuser")
        if not leistungen:
            fehlt.append("Leistungen")
        was = " und ".join(fehlt)
        if benutzer["rolle"] == "buero":
            inhalt = leerer_zustand(
                "Noch nicht eingerichtet",
                "Es fehlen %s. Ohne die kann kein Fahrzeug erfasst werden, weil "
                "sonst kein Preis dazu gehört." % was,
                "/verwaltung", "Stammdaten anlegen")
        else:
            inhalt = leerer_zustand(
                "Noch nicht eingerichtet",
                "Es fehlen %s. Das legt das Büro an. Sag dort kurz Bescheid, "
                "danach kann es losgehen." % was)
        return huelle("Fahrzeug erfassen", "<h1>Fahrzeug erfassen</h1>" + inhalt,
                      benutzer, "/erfassen", "", probebetrieb)

    liste = ""
    if letzte:
        fotos = fotos or {}
        liste = '<h2>Zuletzt erfasst</h2><div class="karten">%s</div>' % "".join(
            fahrzeugkarte(z, fotos=fotos.get(z["id"], ())) for z in letzte)

    inhalt = """
<h1>Fahrzeug erfassen</h1>
<p class="unter">FIN scannen oder tippen. Beides zählt gleich viel.</p>
%(meldungen)s
<form class="block" method="post" action="/erfassen" id="erfassung">
  <input type="hidden" name="vorgang" id="vorgang">
  <label><span class="was">FIN</span>
    <input class="fin" name="fin" id="fin" inputmode="latin" autocapitalize="characters"
           autocomplete="off" spellcheck="false" maxlength="24" required
           placeholder="17 Zeichen">
    <span class="hinweis-klein" id="fin-hinweis">Fahrzeug-Identifizierungsnummer, 17 Zeichen.
      I, O und Q gibt es darin nicht, die werden beim Tippen zu 1 und 0.</span>
  </label>
  <div class="kamera">
    <button type="button" class="still" id="scannen" hidden>Barcode scannen</button>
    <button type="button" class="still" id="lesehilfe-knopf">Schild abfotografieren</button>
  </div>
  <figure class="lesehilfe" id="lesehilfe" hidden>
    <img id="lesehilfe-bild" alt="Aufnahme des Typenschilds zum Ablesen"
         src="data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==">
    <figcaption>Zum Vergrößern tippen und aufziehen. Die Aufnahme dient nur zum
      Ablesen und wird nicht gespeichert.
      <button type="button" class="still klein" id="lesehilfe-weg">Schließen</button>
    </figcaption>
  </figure>
  <label><span class="was">Autohaus</span>
    <select name="autohaus_id" required>%(haeuser)s</select>%(vorher)s</label>
  <label><span class="was">Leistung</span>
    <select name="leistung_id" required>%(leistungen)s</select></label>
  <div class="zwei">
    <label><span class="was">Fertig am</span>
      <input type="date" name="fertig_am" value="%(heute)s" required></label>
    <label><span class="was">Kennzeichen (freiwillig)</span>
      <input name="kennzeichen" autocapitalize="characters" autocomplete="off"></label>
  </div>
  <label><span class="was">Notiz (freiwillig)</span>
    <input name="notiz" autocomplete="off" placeholder="z. B. Teppich stark verschmutzt"></label>
</form>
%(liste)s""" % {
        "meldungen": meldungen_block(gute, schlechte),
        "haeuser": haus_punkte, "leistungen": leistung_punkte,
        "vorher": ('<span class="hinweis-klein">Vom letzten Fahrzeug übernommen. '
                   'Bitte prüfen, wenn du das Haus gewechselt hast.</span>')
                  if vorbelegt.get("autohaus_id") else "",
        "heute": e(heute), "liste": liste,
    }
    leiste = ('<div class="leiste">'
              '<button class="haupt" type="submit" form="erfassung">Fahrzeug erfassen</button>'
              '</div>')
    return huelle("Fahrzeug erfassen", inhalt, benutzer, "/erfassen", leiste, probebetrieb)


def fahrzeugkarte(z, mit_aktionen=False, fotos=(), zusatz=""):
    marke = '<span class="marke %s">%s</span>' % (z["status"], z["status"].capitalize())
    # Die Notiz wurde erfasst und nirgends angezeigt. Ein Feld, das nach einer
    # Angabe fragt und sie dann niemandem zeigt, ist ein leeres Versprechen:
    # Die Halle schreibt „Teppich stark verschmutzt" hinein und niemand liest es.
    text = str(z["notiz"] or "").strip() if "notiz" in z.keys() else ""
    notiz = ('<div class="notiz">%s</div>' % e(text)) if text else ""
    knoepfe = ""
    if mit_aktionen and z["status"] == "offen":
        knoepfe = """
  <form method="post" action="/fahrzeuge/%d/verwerfen" style="margin-top:.6rem"
        onsubmit="return confirm('Fahrzeug %s wirklich verwerfen?')">
    <button class="gefahr klein" type="submit">Verwerfen</button>
  </form>""" % (z["id"], e(z["fin"]))

    # Schadenbilder werden gekennzeichnet. Ohne das war die Wahl zwischen
    # Aussen, Innen und Schaden folgenlos: sie stand nur im alt-Text, den
    # niemand sieht, und kostete die Halle trotzdem bei jedem Foto eine
    # Entscheidung.
    bilder = "".join(
        '<a href="/fotos/%s" target="_blank" rel="noopener"%s>'
        '<img src="/fotos/%s" alt="%s" loading="lazy"></a>'
        % (e(f["datei"]), ' class="schaden" title="Schaden"' if f["art"] == "schaden" else "",
           e(f["datei"]), e(ART_NAME.get(f["art"], f["art"])))
        for f in fotos)
    aufnahme = "".join(
        '<button type="button" class="foto-knopf" data-fahrzeug="%d" data-art="%s">%s</button>'
        % (z["id"], art, e(ART_NAME[art])) for art in ARTEN)
    fotoreihe = ('<div class="fotos" data-fuer="%d">%s<span class="foto-wahl">Foto:</span>%s</div>'
                 % (z["id"], bilder, aufnahme))

    return """<article class="fzg">
  <div class="streifen"><span class="nr">Nr. %d</span>%s
    <span class="haus">%s</span></div>
  <div class="fin">%s</div>
  <div class="unterzeile">
    <span>%s</span><span>fertig %s</span><span>%s</span>
    <span class="betrag">%s EUR</span>
  </div>
  %s%s%s%s
</article>""" % (
        z["id"], marke, e(str(z["autohaus_kurz"] if "autohaus_kurz" in z.keys() else "")),
        e(gruppiert(z["fin"])),
        e(str(z["leistung"] if "leistung" in z.keys() else "")),
        e(str(z["fertig_am"])[:10]),
        e(str(z["kennzeichen"] or "")),
        euro(z["netto_cent"]), notiz, zusatz, fotoreihe, knoepfe)


def fahrzeuge(benutzer, zeilen, haeuser, gewaehlt="", von="", bis="", gute=(), schlechte=(),
              probebetrieb=False, fotos=None, ueberhaupt_welche=True):
    haus_punkte = '<option value="">Alle Autohäuser</option>' + "".join(
        '<option value="%d"%s>%s</option>'
        % (h["id"], " selected" if str(h["id"]) == str(gewaehlt) else "", e(h["name"]))
        for h in haeuser)
    summe = sum(int(z["netto_cent"]) for z in zeilen)
    fotos = fotos or {}
    karten = ('<div class="karten">%s</div>'
              % "".join(fahrzeugkarte(z, mit_aktionen=True, fotos=fotos.get(z["id"], ()))
                        for z in zeilen)) if zeilen else\
             (leerer_zustand(
                 "Noch kein Fahrzeug erfasst",
                 "Sobald das erste Auto fertig ist, steht es hier.",
                 "/erfassen", "Erstes Fahrzeug erfassen")
              if not ueberhaupt_welche else leerer_zustand(
                 "Nichts im gewählten Ausschnitt",
                 "Im gewählten Autohaus oder Zeitraum ist gerade nichts offen. "
                 "Filter leeren zeigt wieder alles."))
    inhalt = """
<h1>Offene Fahrzeuge</h1>
<p class="unter">%d Fahrzeuge, zusammen %s EUR netto. Solange nichts abgerechnet ist,
lässt sich hier alles korrigieren.</p>
%s
<form method="get" action="/fahrzeuge" class="block" style="margin-bottom:1.25rem">
  <div class="zwei">
    <label><span class="was">Autohaus</span><select name="autohaus_id">%s</select></label>
    <label><span class="was">Fertig ab</span><input type="date" name="von" value="%s"></label>
    <label><span class="was">Fertig bis</span><input type="date" name="bis" value="%s"></label>
  </div>
  <button class="still" type="submit">Anzeigen</button>
</form>
%s""" % (len(zeilen), euro(summe), meldungen_block(gute, schlechte),
         haus_punkte, e(von), e(bis), karten)
    return huelle("Offene Fahrzeuge", inhalt, benutzer, "/fahrzeuge", "", probebetrieb)


def _steckt_fest(r):
    """Eine freigegebene Rechnung mit vermerktem Fehler ist nicht ausgestellt."""
    if "letzter_fehler" not in r.keys():
        return False
    return bool(str(r["letzter_fehler"] or "").strip()) and r["status"] == "freigegeben"


def buero(benutzer, je_haus, rechnungen, von, bis, gute=(), schlechte=(),
          probebetrieb=False, zeitraeume=(), aktiv=""):
    zeilen = []
    for h in je_haus:
        frueher = h["frueher"] if "frueher" in h.keys() else 0
        # Nachzuegler sichtbar machen: Fahrzeuge, die vor dem gewaehlten Start
        # fertig wurden und noch offen sind, kommen auf diese Rechnung mit.
        # Ohne den Hinweis wundert sich das Buero ueber die Summe.
        vermerk = ('<br><span class="m" style="color:var(--blass)">davon %d aus '
                   'früheren Zeiträumen</span>' % frueher) if frueher else ""
        zeilen.append("""<tr>
  <td><b>%s</b><br><span class="m" style="color:var(--blass)">%s</span>%s</td>
  <td class="z">%d</td><td class="z">%s</td>
  <td class="z"><form method="post" action="/abrechnen">
    <input type="hidden" name="autohaus_id" value="%d">
    <input type="hidden" name="von" value="%s"><input type="hidden" name="bis" value="%s">
    <button class="haupt klein" type="submit"%s>Sammelrechnung</button></form></td>
</tr>""" % (e(h["name"]), e(h["kurz"]), vermerk, h["anzahl"], euro(h["summe"]),
            h["id"], e(von), e(bis), "" if h["anzahl"] else " disabled"))
    tabelle = ('<div class="rahmen"><table><thead><tr><th>Autohaus</th>'
               '<th class="z">Fahrzeuge</th><th class="z">Netto</th><th class="z"></th>'
               '</tr></thead><tbody>%s</tbody></table></div>' % "".join(zeilen)) if zeilen\
        else leerer_zustand(
            "Im Zeitraum ist nichts offen",
            "Entweder ist alles abgerechnet, oder der Zeitraum passt nicht. "
            "Die Knöpfe links springen schnell auf die richtige Woche.")

    liste = []
    for r in rechnungen:
        # Eine Rechnung, die beim Ausstellen haengengeblieben ist, sah hier aus
        # wie eine, die nur auf den Klick wartet. Der Grund stand erst drin,
        # wenn man sie oeffnete. Bei acht Rechnungen die Woche uebersieht man
        # die eine — und dann ist sie nicht gestellt.
        stoerung = ""
        if _steckt_fest(r):
            stoerung = ('<br><span class="marke stoerung">%s</span>'
                        % ("In Lexware nachsehen" if r["fehler_unklar"]
                           else "Nicht ausgestellt"))
        liste.append("""<tr>
  <td><a href="/rechnung/%d"><b>%s</b></a><br>
      <span class="m" style="color:var(--blass)">%s bis %s</span></td>
  <td><span class="marke %s">%s</span>%s%s</td>
  <td class="z">%d</td><td class="z">%s</td>
</tr>""" % (r["id"], e(r["autohaus"]), e(str(r["von"])[:10]), e(str(r["bis"])[:10]),
            "abgerechnet" if r["status"] != "entwurf" else "offen", e(r["status"].capitalize()),
            ('<br><span class="m" style="font-size:.78rem">%s</span>' % e(r["lexware_nummer"]))
            if r["lexware_nummer"] else "", stoerung,
            r["anzahl"], euro(r["netto_cent"])))
    rechnungstabelle = ('<div class="rahmen"><table><thead><tr><th>Rechnung</th><th>Stand</th>'
                        '<th class="z">Pos.</th><th class="z">Netto</th></tr></thead>'
                        '<tbody>%s</tbody></table></div>' % "".join(liste)) if liste\
        else leerer_zustand(
            "Noch keine Rechnung erzeugt",
            "Sobald Fahrzeuge offen sind, entsteht hier je Autohaus eine "
            "Sammelrechnung. Sie beginnt immer als Entwurf.")

    gesamt = sum(int(h["summe"]) for h in je_haus)
    fahrzeuge_offen = sum(int(h["anzahl"]) for h in je_haus)
    haeuser_offen = len([h for h in je_haus if h["anzahl"]])
    entwuerfe = len([r for r in rechnungen if r["status"] == "entwurf"])
    festsitzend = len([r for r in rechnungen if _steckt_fest(r)])

    inhalt = """
<h1>Abrechnen</h1>
<p class="unter">Je Autohaus ein Beleg mit einer Nummer, nicht ein Stapel Einzelrechnungen.</p>
%(meldungen)s
<div class="zahlen">
  <div class="zahl hebt"><b>%(fahrzeuge)d</b><span>Fahrzeuge offen</span></div>
  <div class="zahl"><b>%(gesamt)s</b><span>EUR netto offen</span></div>
  <div class="zahl"><b>%(haeuser)d</b><span>Autohäuser mit offenen</span></div>
  <div class="zahl"><b>%(entwuerfe)d</b><span>Entwürfe wartend</span></div>
  %(festsitzend)s
</div>
<div class="buero">
  <div class="feld">
    <h2>Zeitraum</h2>
    <div class="schnellwahl">%(schnellwahl)s</div>
    <form method="get" action="/buero" class="block">
      <label><span class="was">Fertig ab</span><input type="date" name="von" value="%(von)s"></label>
      <label><span class="was">Fertig bis</span><input type="date" name="bis" value="%(bis)s"></label>
      <button class="still" type="submit">Anzeigen</button>
    </form>
  </div>
  <div>
    <h2 style="margin-top:0">Offen je Autohaus</h2>
    %(tabelle)s
    <h2>Rechnungen</h2>
    %(rechnungen)s
  </div>
</div>""" % {
        "meldungen": meldungen_block(gute, schlechte), "von": e(von), "bis": e(bis),
        "gesamt": euro(gesamt), "fahrzeuge": fahrzeuge_offen, "haeuser": haeuser_offen,
        "entwuerfe": entwuerfe, "tabelle": tabelle, "rechnungen": rechnungstabelle,
        # Nur zeigen, wenn es etwas zu zeigen gibt: eine Null, die immer dasteht,
        # wird nach einer Woche nicht mehr gelesen.
        "festsitzend": ('<div class="zahl warnt"><b>%d</b><span>%s</span></div>'
                        % (festsitzend,
                           "Rechnung nicht ausgestellt" if festsitzend == 1
                           else "Rechnungen nicht ausgestellt")) if festsitzend else "",
        "schnellwahl": "".join(
            '<a href="/buero?zeitraum=%s" class="wahl%s"%s>%s</a>'
            % (schluessel, " an" if schluessel == aktiv else "",
               ' aria-current="true"' if schluessel == aktiv else "", e(beschriftung))
            for schluessel, beschriftung, _, _ in zeitraeume),
    }
    return huelle("Abrechnen", inhalt, benutzer, "/buero", "", probebetrieb)


def rechnung_seite(benutzer, kopf, posten, gute=(), schlechte=(), probebetrieb=False):
    zeilen = []
    for p in posten:
        gut = ' <span class="marke gutschrift">gutgeschrieben</span>' if p["gutgeschrieben"] else ""
        # Eine Gutschrift ohne Belegnummer ist keine: Der Aufruf ist
        # abgebrochen, und es ist offen, ob drueben doch eine entstanden ist.
        # Sie darf nicht wie eine fertige aussehen, und ein zweiter Klick waere
        # womoeglich die zweite Gutschrift fuer dasselbe Fahrzeug.
        offen = p["gutschrift_offen"] if "gutschrift_offen" in p.keys() else 0
        # Die Marke bleibt ein kurzes Etikett (Versalien), die Anweisung steht
        # darunter in normaler Schrift: Ein Satz in Grossbuchstaben liest sich
        # schlecht, und gerade diesen Satz muss das Buero wirklich lesen.
        hinweis = ""
        if offen:
            gut = ' <span class="marke stoerung">Gutschrift offen</span>'
            hinweis = ('<br><span style="color:var(--rot)">vor einem zweiten '
                       'Versuch in Lexware nachsehen</span>')
        aktion = ""
        if kopf["status"] == "finalisiert" and not p["gutgeschrieben"] and not offen:
            aktion = """<form method="post" action="/gutschrift/%d" class="block"
      onsubmit="return confirm('Gutschrift für dieses Fahrzeug erzeugen?')">
      <input name="grund" placeholder="Grund" required style="min-height:2.4rem;font-size:.9rem">
      <button class="gefahr klein" type="submit">Gutschrift</button></form>""" % p["fahrzeug_id"]
        ohne_fin = " | ".join(t for t in str(p["text"]).split(" | ") if t != p["fin"])
        zeilen.append("""<tr>
  <td class="m">%s%s<br><span style="color:var(--blass)">%s</span>%s</td>
  <td>%s</td><td class="z">%s</td><td>%s</td>
</tr>""" % (e(gruppiert(p["fin"])), gut, e(str(p["fertig_am"])[:10]), hinweis,
            e(ohne_fin), euro(p["netto_cent"]), aktion))

    steuer = int(kopf["steuersatz"])
    netto = int(kopf["netto_cent"])
    umsatz = round(netto * steuer / 100)
    knoepfe = []
    if kopf["status"] == "entwurf":
        knoepfe.append('<form method="post" action="/rechnung/%d/freigeben">'
                       '<button class="haupt" type="submit">Freigeben</button></form>' % kopf["id"])
        knoepfe.append('<form method="post" action="/rechnung/%d/stornieren" '
                       'onsubmit="return confirm(\'Entwurf verwerfen? Die Fahrzeuge werden '
                       'wieder offen.\')"><input type="hidden" name="grund" value="Entwurf verworfen">'
                       '<button class="gefahr" type="submit">Entwurf verwerfen</button></form>'
                       % kopf["id"])
    elif kopf["status"] == "freigegeben":
        beschriftung = ("Erneut ausstellen" if kopf["letzter_fehler"]
                        else "Im Rechnungsprogramm ausstellen")
        nachfrage = ""
        if kopf["fehler_unklar"]:
            nachfrage = (" onsubmit=\"return confirm('In Lexware nachgesehen, dass "
                         "noch kein Beleg da ist?')\"")
        knoepfe.append('<form method="post" action="/rechnung/%d/ausstellen"%s>'
                       '<button class="haupt" type="submit">%s</button></form>'
                       % (kopf["id"], nachfrage, e(beschriftung)))

    stoerung = ""
    if kopf["letzter_fehler"] and kopf["status"] == "freigegeben":
        if kopf["fehler_unklar"]:
            rat = ("<b>Vor einem zweiten Versuch in Lexware nachsehen, ob der Beleg "
                   "schon da ist.</b> Die Verbindung ist abgebrochen, bevor eine "
                   "Antwort kam — der Beleg kann drüben bereits angelegt sein. Ein "
                   "blinder zweiter Versuch würde dann eine zweite Rechnung mit "
                   "eigener Nummer erzeugen, und das Autohaus bekäme dieselbe "
                   "Leistung zweimal berechnet.")
        else:
            rat = ("Das Rechnungsprogramm hat die Anfrage abgelehnt, es ist drüben "
                   "nichts entstanden. Ursache beheben und erneut ausstellen.")
        stoerung = """<div class="stoerung">
  <b>Ausstellen fehlgeschlagen</b>
  <p>%s</p>
  <p class="grund">%s</p>
  <p class="wann">Zuletzt versucht: %s. Die Rechnung ist unverändert freigegeben.</p>
</div>""" % (rat, e(kopf["letzter_fehler"]),
             e(str(kopf["letzter_fehler_am"])[:19].replace("T", " ")))

    inhalt = """
<h1>Sammelrechnung %(nummer)s</h1>
<p class="unter">%(haus)s &middot; Leistungszeitraum %(von)s bis %(bis)s &middot;
  Stand: <b>%(stand)s</b>%(beleg)s</p>
%(meldungen)s%(stoerung)s
<div class="buero">
  <div class="feld">
    <div class="summenzeile"><span>Positionen</span><b>%(anzahl)d</b></div>
    <div class="summenzeile"><span>Netto</span><b>%(netto)s EUR</b></div>
    <div class="summenzeile"><span>Umsatzsteuer %(steuer)d %%</span><b>%(ust)s EUR</b></div>
    <div class="summenzeile"><span>Brutto</span><b class="gross">%(brutto)s EUR</b></div>
    <div style="display:grid;gap:.6rem;margin-top:1rem">%(knoepfe)s</div>
  </div>
  <div class="rahmen">
    <table><thead><tr><th>FIN</th><th>Position</th><th class="z">Netto</th><th></th></tr></thead>
    <tbody>%(zeilen)s</tbody></table>
  </div>
</div>
<p class="unter" style="margin-top:1.25rem">
Eine ausgestellte Rechnung wird nicht mehr verändert. Wird ein einzelnes Fahrzeug
reklamiert, entsteht dafür eine Gutschrift; der Rest der Rechnung bleibt unberührt.</p>""" % {
        "nummer": kopf["id"], "haus": e(kopf["autohaus"]),
        "von": e(str(kopf["von"])[:10]), "bis": e(str(kopf["bis"])[:10]),
        "stand": e(kopf["status"].capitalize()),
        "beleg": (" &middot; Beleg %s" % e(kopf["lexware_nummer"])) if kopf["lexware_nummer"] else "",
        "meldungen": meldungen_block(gute, schlechte), "stoerung": stoerung,
        "anzahl": len(posten), "netto": euro(netto), "steuer": steuer,
        "ust": euro(umsatz), "brutto": euro(netto + umsatz),
        "knoepfe": "".join(knoepfe) or
                   '<span class="hinweis-klein">Keine Aktion mehr möglich.</span>',
        "zeilen": "".join(zeilen),
    }
    return huelle("Sammelrechnung %d" % kopf["id"], inhalt, benutzer, "/buero", "", probebetrieb)


def _leerer_satz(_spalten, text):
    return '<div class="satz"><p class="leer-zeile">%s</p></div>' % e(text)


def _an(wert):
    return " checked" if wert else ""


def _autohaus_satz(h, alle, namen):
    """Ein Autohaus als aufklappbarer Satz mit Formular zum Ändern."""
    anschrift = ", ".join(x for x in [h["strasse"] or "",
                                      ("%s %s" % (h["plz"] or "", h["ort"] or "")).strip()]
                          if x.strip())
    marken = ""
    if not h["aktiv"]:
        marken += ' <span class="marke">stillgelegt</span>'
    if h["filiale_von"]:
        marken += ' <span class="marke">Filiale von %s</span>' % e(
            namen.get(h["filiale_von"], "?"))
    # Als Hauptsitz kommen nur Häuser in Frage, die selbst keine Filiale sind.
    moeglich = "".join(
        '<option value="%d"%s>%s</option>'
        % (k["id"], " selected" if h["filiale_von"] == k["id"] else "", e(k["name"]))
        for k in alle if k["id"] != h["id"] and not k["filiale_von"])
    return """<details class="satz">
  <summary><b>%s</b> <span class="m">%s</span>%s<br>
    <span class="m" style="color:var(--blass)">%s &middot; %d Tage Zahlungsziel</span></summary>
  <form class="block" method="post" action="/verwaltung/autohaus/%d">%s
    <div class="zwei">
      <label><span class="was">Name</span><input name="name" value="%s" required></label>
      <label><span class="was">Kürzel</span><input name="kurz" value="%s" required
        maxlength="8" autocapitalize="characters"></label>
      <label><span class="was">Zahlungsziel in Tagen</span>
        <input name="zahlungsziel" type="number" value="%d" min="0" max="180"></label>
    </div>
    <div class="zwei">
      <label><span class="was">Strasse</span><input name="strasse" value="%s"></label>
      <label><span class="was">PLZ</span><input name="plz" value="%s"></label>
      <label><span class="was">Ort</span><input name="ort" value="%s"></label>
    </div>
    <div class="zwei">
      <label><span class="was">Käuferreferenz</span>
        <input name="kaeuferreferenz" value="%s"></label>
      <label><span class="was">Lieferantennummer</span>
        <input name="lieferantennummer" value="%s"></label>
      <label><span class="was">Kontakt im Rechnungsprogramm</span>
        <input name="lexware_kontakt" value="%s"></label>
    </div>
    <div class="zwei">
      <label><span class="was">Filiale von</span>
        <select name="filiale_von"><option value="">Eigenständig</option>%s</select></label>
      <label class="haken"><input type="checkbox" name="sammeln_mit_hauptsitz"%s>
        Mit dem Hauptsitz auf einer Rechnung</label>
      <label class="haken"><input type="checkbox" name="aktiv"%s> In Benutzung</label>
    </div>
    <button class="haupt" type="submit">Speichern</button>
  </form>
</details>""" % (
        e(h["name"]), e(h["kurz"]), marken, e(anschrift or "keine Anschrift hinterlegt"),
        h["zahlungsziel"], h["id"], _stand_felder(h, STAND_AUTOHAUS),
        e(h["name"]), e(h["kurz"]), h["zahlungsziel"],
        e(h["strasse"] or ""), e(h["plz"] or ""), e(h["ort"] or ""),
        e(h["kaeuferreferenz"] or ""), e(h["lieferantennummer"] or ""),
        e(h["lexware_kontakt"] or ""), moeglich,
        _an(h["sammeln_mit_hauptsitz"]), _an(h["aktiv"]))


def _stand_felder(zeile, spalten):
    """Der Zustand, aus dem dieses Formular gebaut wird, reist unsichtbar mit.

    Beim Speichern vergleicht `stammdaten` ihn IM Schreibbefehl mit der Zeile.
    Hat jemand anderes sie inzwischen angefasst, trifft das UPDATE nichts und
    niemand ueberschreibt still eine fremde Aenderung.
    """
    return "".join(
        '<input type="hidden" name="alt_%s" value="%s">'
        % (s, e("" if zeile[s] is None else str(zeile[s])))
        for s in spalten)


def _leistung_satz(l):
    return """<details class="satz">
  <summary><b>%s</b>%s<br><span class="m" style="color:var(--blass)">%s EUR netto</span></summary>
  <form class="block" method="post" action="/verwaltung/leistung/%d">%s
    <div class="zwei">
      <label><span class="was">Bezeichnung</span>
        <input name="bezeichnung" value="%s" required></label>
      <label><span class="was">Standardpreis netto</span>
        <input name="preis" value="%s" required inputmode="decimal"></label>
      <label><span class="was">Reihenfolge</span>
        <input name="sortierung" type="number" value="%d"></label>
    </div>
    <label class="haken"><input type="checkbox" name="aktiv"%s> In Benutzung</label>
    <button class="haupt" type="submit">Speichern</button>
  </form>
</details>""" % (
        e(l["bezeichnung"]),
        "" if l["aktiv"] else ' <span class="marke">stillgelegt</span>',
        euro(l["netto_cent"]), l["id"],
        _stand_felder(l, STAND_LEISTUNG),
        e(l["bezeichnung"]), euro(l["netto_cent"]),
        l["sortierung"], _an(l["aktiv"]))


def verwaltung(benutzer, haeuser, leistungen, leute, gute=(), schlechte=(),
               probebetrieb=False, sicherungen=(), preise=()):
    # Stammdaten liessen sich bis zum 22.09. anlegen, aber nie korrigieren.
    # Ein Tippfehler in der Anschrift stand damit auf jeder Rechnung.
    namen = {h["id"]: h["name"] for h in haeuser}
    haus_zeilen = "".join(_autohaus_satz(h, haeuser, namen) for h in haeuser)
    leistung_zeilen = "".join(_leistung_satz(l) for l in leistungen)
    # Gesetzte Sonderpreise waren bis zum 22.09. unsichtbar: nur beim Erfassen
    # gelesen, nie angezeigt, nie zu entfernen. Steigt der Standardpreis, bleibt
    # ein vergessener Sonderpreis still bestehen und das Autohaus zahlt zu wenig.
    preis_zeilen = "".join("""<tr>
  <td><b>%s</b><br><span class="m" style="color:var(--blass)">%s</span></td>
  <td class="z"><b>%s</b><br><span class="m" style="color:var(--blass)">statt %s</span></td>
  <td class="z"><form method="post" action="/verwaltung/preis/%d/%d/entfernen"
      onsubmit="return confirm('Sonderpreis entfernen? Ab dann gilt der Standardpreis.')">
      <button class="still klein" type="submit">Entfernen</button></form></td>
</tr>""" % (e(p["autohaus"]), e(p["leistung"]), euro(p["netto_cent"]),
            euro(p["standard_cent"]), p["autohaus_id"], p["leistung_id"])
        for p in preise)

    ROLLE_NAME = {"buero": "Büro", "erfasser": "Erfasser"}
    leute_zeilen = "".join(
        '<tr><td><b>%s</b>%s</td><td class="m">%s</td><td>%s</td><td class="z">%s</td></tr>'
        % (e(p["name"]),
           "" if p["aktiv"] else ' <span class="marke">stillgelegt</span>',
           e(p["anmeldename"]), e(ROLLE_NAME.get(p["rolle"], p["rolle"])),
           _zugang_knoepfe(p, benutzer))
        for p in leute)

    inhalt = """
<h1>Stammdaten</h1>
<p class="unter">Autohäuser, Leistungen und Zugänge. Preise je Autohaus schlagen den
Standardpreis der Leistung.</p>
%(meldungen)s

<h2>Autohaus anlegen</h2>
<form class="block" method="post" action="/verwaltung/autohaus">
  <div class="zwei">
    <label><span class="was">Name</span><input name="name" required></label>
    <label><span class="was">Kürzel</span><input name="kurz" required maxlength="8"
      autocapitalize="characters" placeholder="z. B. NORD"></label>
    <label><span class="was">Zahlungsziel in Tagen</span>
      <input name="zahlungsziel" type="number" value="14" min="0" max="120"></label>
  </div>
  <div class="zwei">
    <label><span class="was">Strasse</span><input name="strasse"></label>
    <label><span class="was">PLZ</span><input name="plz"></label>
    <label><span class="was">Ort</span><input name="ort"></label>
  </div>
  <div class="zwei">
    <label><span class="was">Käuferreferenz (Leitweg-ID)</span><input name="kaeuferreferenz">
      <span class="hinweis-klein">Verlangt das Autohaus eine Referenz auf der Rechnung,
      gehört sie hierhin. Sie landet in der E-Rechnung.</span></label>
    <label><span class="was">Lieferantennummer</span>
      <input name="lieferantennummer"></label>
    <label><span class="was">Kontakt-ID</span>
      <input name="lexware_kontakt" placeholder="Kontakt-ID, freiwillig"></label>
  </div>
  <button class="haupt" type="submit">Autohaus anlegen</button>
</form>
<div class="rahmen" style="margin-top:1rem">%(haeuser)s</div>

<h2>Leistung anlegen</h2>
<form class="block" method="post" action="/verwaltung/leistung">
  <div class="zwei">
    <label><span class="was">Bezeichnung</span><input name="bezeichnung" required></label>
    <label><span class="was">Standardpreis netto</span>
      <input name="preis" required inputmode="decimal" placeholder="149,00"></label>
  </div>
  <button class="haupt" type="submit">Leistung anlegen</button>
</form>
<div class="rahmen" style="margin-top:1rem">%(leistungen)s</div>

<h2>Sonderpreis je Autohaus</h2>
<div class="rahmen" style="margin-bottom:1rem"><table><thead><tr><th>Autohaus</th>
  <th class="z">Sonderpreis</th><th class="z"></th></tr></thead>
  <tbody>%(preise)s</tbody></table></div>
<form class="block" method="post" action="/verwaltung/preis">
  <div class="zwei">
    <label><span class="was">Autohaus</span><select name="autohaus_id" required>%(haus_punkte)s
      </select></label>
    <label><span class="was">Leistung</span><select name="leistung_id" required>%(leistung_punkte)s
      </select></label>
    <label><span class="was">Preis netto</span>
      <input name="preis" required inputmode="decimal" placeholder="139,00"></label>
  </div>
  <button class="haupt" type="submit">Preis setzen</button>
</form>

<h2>Sicherung</h2>
<p class="unter" style="margin-bottom:.9rem">Legt eine Kopie der Datenbank an und
sieht sofort hinein: innere Unversehrtheit und Zeilenzahlen. Eine ungeprüfte
Sicherung ist keine Sicherung. Nachts läuft das ohnehin von selbst, dieser Knopf
ist für den Moment vor einer Änderung.</p>
<form method="post" action="/sicherung" style="margin-bottom:.9rem">
  <button class="haupt" type="submit">Jetzt sichern und prüfen</button>
</form>
<div class="rahmen"><table><thead><tr><th>Vorhandene Sicherungen</th>
  <th class="z">Grösse</th></tr></thead><tbody>%(sicherungen)s</tbody></table></div>

<h2>Alle Daten mitnehmen</h2>
<p class="unter" style="margin-bottom:.9rem">Eine Datei mit allem: je Tabelle eine
CSV für Excel, dazu sämtliche Fotos. Ohne Passwörter, die gehören in keinen
Export. Jederzeit, ohne Nachfrage bei uns.</p>
<a class="knopf haupt" href="/export">Datenexport herunterladen</a>

<h2>Zugang anlegen</h2>
<form class="block" method="post" action="/verwaltung/benutzer">
  <div class="zwei">
    <label><span class="was">Name</span><input name="name" required></label>
    <label><span class="was">Anmeldename</span><input name="anmeldename" required
      autocapitalize="none"></label>
    <label><span class="was">Passwort</span><input name="passwort" type="password" required
      minlength="8"></label>
  </div>
  <label><span class="was">Rolle</span><select name="rolle">
    <option value="erfasser">Erfasser, darf nur erfassen</option>
    <option value="buero">Büro, darf abrechnen</option></select></label>
  <button class="haupt" type="submit">Zugang anlegen</button>
</form>
<div class="rahmen" style="margin-top:1rem"><table><thead><tr><th>Name</th>
  <th>Anmeldename</th><th>Rolle</th><th class="z">Zugang</th></tr></thead>
  <tbody>%(leute)s</tbody></table></div>

<h2>Passwort für einen Zugang neu setzen</h2>
<p class="unter" style="margin-bottom:.9rem">Für den Fall, dass ein Passwort
vergessen wurde oder ein Handy abhanden gekommen ist. Alle bestehenden
Anmeldungen dieses Zugangs enden damit sofort.</p>
<form class="block" method="post" action="/verwaltung/passwort">
  <div class="zwei">
    <label><span class="was">Zugang</span><select name="benutzer_id" required>%(zugaenge)s
      </select></label>
    <label><span class="was">Neues Passwort</span>
      <input name="passwort" type="password" required minlength="8"></label>
    <label><span class="was">Noch einmal</span>
      <input name="wiederholung" type="password" required minlength="8"></label>
  </div>
  <button class="haupt" type="submit">Passwort setzen</button>
</form>""" % {
        "meldungen": meldungen_block(gute, schlechte),
        "haeuser": haus_zeilen or _leerer_satz(
            4, "Noch kein Autohaus angelegt. Ohne Autohaus kann nichts erfasst werden."),
        "leistungen": leistung_zeilen or _leerer_satz(
            2, "Noch keine Leistung angelegt. Ohne Leistung gibt es keinen Preis."),
        "leute": leute_zeilen or leere_zeile(4, "Noch kein Zugang angelegt."),
        "zugaenge": "".join('<option value="%d">%s (%s)</option>'
                            % (p["id"], e(p["name"]), e(p["anmeldename"]))
                            for p in leute),
        "preise": preis_zeilen or leere_zeile(
            3, "Kein Sonderpreis gesetzt. Überall gilt der Standardpreis der Leistung."),
        "sicherungen": "".join(
            '<tr><td class="m">%s</td><td class="z">%d kB</td></tr>'
            % (e(sicherung["datei"]), sicherung["groesse"] // 1024)
            for sicherung in sicherungen) or leere_zeile(
                2, "Noch keine Sicherung angelegt."),
        "haus_punkte": "".join('<option value="%d">%s</option>' % (h["id"], e(h["name"]))
                               for h in haeuser),
        "leistung_punkte": "".join('<option value="%d">%s</option>' % (l["id"], e(l["bezeichnung"]))
                                   for l in leistungen),
    }
    return huelle("Stammdaten", inhalt, benutzer, "/verwaltung", "", probebetrieb)


def protokoll_seite(benutzer, zeilen, probebetrieb=False):
    reihen = "".join(
        '<tr><td class="m">%s</td><td>%s</td><td><b>%s</b><br>'
        '<span style="color:var(--blass)">%s</span></td><td>%s</td></tr>'
        % (e(str(z["zeit"])[:19].replace("T", " ")), e(z["benutzer_name"]), e(z["was"]),
           e(z["gegenstand"] or ""), e(z["einzelheiten"] or "")) for z in zeilen)
    inhalt = """
<h1>Protokoll</h1>
<p class="unter">Wer wann was. Wird nicht gelöscht und nicht geändert.</p>
<div class="rahmen"><table><thead><tr><th>Zeit</th><th>Wer</th><th>Was</th>
  <th>Einzelheiten</th></tr></thead><tbody>%s</tbody></table></div>""" % (
        reihen or leere_zeile(4, "Noch nichts passiert. Hier steht später jeder Schritt."))
    return huelle("Protokoll", inhalt, benutzer, "/protokoll", "", probebetrieb)


def suche(benutzer, frage, treffer, probebetrieb=False):
    if not frage:
        ergebnis = ('<div class="leer">FIN oder Kennzeichen eingeben. '
                    'Ein Teil genügt, zum Beispiel die letzten sechs Stellen.</div>')
    elif len(frage.strip()) < 3:
        ergebnis = '<div class="leer">Bitte mindestens drei Zeichen eingeben.</div>'
    elif not treffer:
        ergebnis = ('<div class="leer">Nichts gefunden zu „%s". Trenner und Gross- oder '
                    'Kleinschreibung sind egal, O und 0 werden beide gefunden.</div>'
                    % e(frage))
    else:
        stuecke = []
        for z in treffer:
            wo = []
            if z["rechnung_status"]:
                beleg = z["lexware_nummer"] or ("Entwurf Nr. %s" % z["rechnung_id"])
                wo.append("Auf Rechnung %s (%s bis %s)"
                          % (e(beleg), e(str(z["rechnung_von"])[:10]),
                             e(str(z["rechnung_bis"])[:10])))
            else:
                wo.append("Noch auf keiner Rechnung")
            if z["gutgeschrieben"]:
                wo.append("gutgeschrieben")
            wo.append("erfasst von %s" % e(z["erfasser"]))
            stuecke.append(fahrzeugkarte(
                z, zusatz='<div class="fundort">%s</div>' % " &middot; ".join(wo)))
        ergebnis = '<div class="karten">%s</div>' % "".join(stuecke)

    inhalt = """
<h1>Suche</h1>
<p class="unter">Über alle Fahrzeuge, auch über längst abgerechnete.</p>
<form method="get" action="/suche" class="block" style="margin-bottom:1.25rem">
  <label><span class="was">FIN oder Kennzeichen</span>
    <input name="q" value="%s" autocapitalize="characters" autocomplete="off"
           spellcheck="false" placeholder="z. B. 386752 oder SZ-AB 123" autofocus></label>
  <button class="haupt" type="submit">Suchen</button>
</form>
%s""" % (e(frage or ""), ergebnis)
    return huelle("Suche", inhalt, benutzer, "/suche", "", probebetrieb)


def _zugang_knoepfe(zeile, ich):
    """Der eigene Zugang bekommt keinen Stilllegen-Knopf — man würde sich
    selbst aussperren, und der Knopf wäre eine Falle."""
    if int(zeile["id"]) == int(ich["id"]):
        return '<span class="hinweis-klein">das bist du</span>'
    if zeile["aktiv"]:
        return ('<form method="post" action="/verwaltung/benutzer/%d/stilllegen" '
                'onsubmit="return confirm(\'Zugang %s stilllegen? Bestehende '
                'Anmeldungen enden sofort.\')">'
                '<button class="gefahr klein" type="submit">Stilllegen</button></form>'
                % (zeile["id"], e(zeile["anmeldename"])))
    return ('<form method="post" action="/verwaltung/benutzer/%d/aktivieren">'
            '<button class="still klein" type="submit">Wieder öffnen</button></form>'
            % zeile["id"])


def konto(benutzer, gute=(), schlechte=(), probebetrieb=False):
    inhalt = """
<h1>Mein Zugang</h1>
<p class="unter">Angemeldet als <b>%(name)s</b>, Rolle %(rolle)s.</p>
%(meldungen)s
<div class="feld" style="max-width:34rem">
  <h2 style="margin-top:0">Passwort ändern</h2>
  <p class="unter" style="margin-bottom:1rem">Nach der Änderung wirst du auf
  anderen Geräten abgemeldet. Hier bleibst du angemeldet.</p>
  <form class="block" method="post" action="/konto">
    <label><span class="was">Bisheriges Passwort</span>
      <input name="alt" type="password" required autocomplete="current-password"></label>
    <label><span class="was">Neues Passwort</span>
      <input name="neu" type="password" required minlength="8"
             autocomplete="new-password"></label>
    <label><span class="was">Noch einmal</span>
      <input name="wiederholung" type="password" required minlength="8"
             autocomplete="new-password"></label>
    <button class="haupt" type="submit">Passwort ändern</button>
  </form>
</div>""" % {
        "name": e(benutzer["name"]),
        "rolle": "Büro" if benutzer["rolle"] == "buero" else "Erfasser",
        "meldungen": meldungen_block(gute, schlechte),
    }
    return huelle("Mein Zugang", inhalt, benutzer, "", "", probebetrieb)

"""Sammelrechnung je Autohaus und Zeitraum.

Der Unterschied zum Wettbewerb steht in der Bauliste: Die Sammelrechnung ist
EIN Beleg mit EINER Nummer und EINER Zahlung, kein Stapel Einzelrechnungen.

Zustaende: entwurf -> freigegeben -> finalisiert.
Storniert werden kann nur, solange nicht finalisiert ist. Danach gibt es
ausschliesslich die Gutschrift, weil eine ausgestellte Rechnung nach GoBD
unveränderbar bleibt.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from . import datenbank, protokoll


# So lange gilt ein Ausstellungsversuch als laufend. Danach darf ein zweiter
# Versuch nachruecken, bekommt aber den Hinweis, vorher in Lexware nachzusehen.
ANSPRUCH_MINUTEN = 5


class Abgelehnt(ValueError):
    pass


def _autohaus_kreis(verb, autohaus_id: int):
    """Hauptsitz plus die Filialen, die mit ihm gesammelt abgerechnet werden."""
    haus = verb.execute("SELECT * FROM autohaus WHERE id=?", (autohaus_id,)).fetchone()
    if not haus:
        raise Abgelehnt("Autohaus nicht gefunden.")
    if haus["filiale_von"] and haus["sammeln_mit_hauptsitz"]:
        raise Abgelehnt(
            "%s wird mit dem Hauptsitz abgerechnet. Bitte die Rechnung dort erzeugen."
            % haus["name"])
    ids = [autohaus_id]
    for zeile in verb.execute(
            "SELECT id FROM autohaus WHERE filiale_von=? AND sammeln_mit_hauptsitz=1",
            (autohaus_id,)):
        ids.append(zeile["id"])
    return haus, ids


def tag(wert, name: str) -> str:
    """ISO-Datum oder eine klare Absage. Ohne das lautete die Meldung bei einem
    Tippfehler wie `2026-9-21` „kein offenes Fahrzeug erfasst", und das Büro
    suchte am falschen Ende."""
    try:
        return date.fromisoformat(str(wert)[:10]).isoformat()
    except (ValueError, TypeError):
        raise Abgelehnt("%s ist kein gültiges Datum. Erwartet wird 2026-09-21." % name)


def abrechenbare(verb, autohaus_id: int, bis: str):
    """Alle offenen Fahrzeuge, die bis zum Stichtag fertig waren.

    Bewusst OHNE untere Grenze. Vorher stand hier `BETWEEN von AND bis`, und
    damit fiel jedes nachträglich erfasste Fahrzeug durch: Wer am Dienstag ein
    Auto einträgt, das vergangenen Donnerstag fertig wurde, dessen Woche aber
    schon abgerechnet ist, bekam es auf keine Rechnung mehr — die neue Woche
    greift es nicht, und die alte ist durch den Eindeutigkeitsschlüssel
    gesperrt. Gemessen: das Fahrzeug blieb dauerhaft auf „offen" stehen, und
    niemand sah es, weil die Übersicht nur die gewählte Woche zählt.
    """
    _, ids = _autohaus_kreis(verb, autohaus_id)
    platz = ",".join("?" * len(ids))
    return verb.execute(
        "SELECT f.*, a.kurz AS autohaus_kurz, l.bezeichnung AS leistung"
        " FROM fahrzeug f"
        " JOIN autohaus a ON a.id=f.autohaus_id"
        " JOIN leistung l ON l.id=f.leistung_id"
        " WHERE f.autohaus_id IN (%s) AND f.status='offen'"
        "   AND date(f.fertig_am) <= date(?)"
        " ORDER BY date(f.fertig_am), f.id" % platz,
        tuple(ids) + (bis,)).fetchall()


def je_haus(verb, von: str, bis: str):
    """Die Übersicht fürs Büro: je Autohaus, was auf die nächste Rechnung käme.

    Steht bewusst NEBEN `abrechenbare`, weil beide dieselbe Menge treffen
    müssen. Zwei Abweichungen waren drin, beide zulasten des Betriebs:
    (1) Die Übersicht zählte nur den gewählten Zeitraum, die Rechnung nimmt
        alles Offene bis zum Stichtag.
    (2) Fahrzeuge einer Filiale, die mit dem Hauptsitz abgerechnet wird,
        tauchten NIRGENDS auf: die Filiale ist aus der Liste gefiltert, und an
        den Hauptsitz war sie nicht angehängt. Auf seiner Rechnung standen sie
        trotzdem.
    `frueher` zählt die Nachzügler, damit die Seite sie benennen kann.
    """
    return verb.execute(
        "SELECT a.id, a.name, a.kurz,"
        " COUNT(f.id) AS anzahl, COALESCE(SUM(f.netto_cent),0) AS summe,"
        " COALESCE(SUM(CASE WHEN date(f.fertig_am) < date(?) THEN 1 ELSE 0 END),0) AS frueher"
        " FROM autohaus a"
        " LEFT JOIN fahrzeug f"
        "   ON (f.autohaus_id=a.id OR f.autohaus_id IN ("
        "        SELECT k.id FROM autohaus k"
        "         WHERE k.filiale_von=a.id AND k.sammeln_mit_hauptsitz=1))"
        "  AND f.status='offen' AND date(f.fertig_am) <= date(?)"
        " WHERE a.aktiv=1 AND NOT (a.filiale_von IS NOT NULL AND a.sammeln_mit_hauptsitz=1)"
        " GROUP BY a.id ORDER BY anzahl DESC, a.name", (von, bis)).fetchall()


def erzeugen(verb, benutzer, autohaus_id: int, von: str, bis: str, steuersatz: int = 19):
    """Erzeugt den Entwurf. Laeuft ganz oder gar nicht."""
    if benutzer["rolle"] != "buero":
        raise Abgelehnt("Rechnungen erzeugt nur das Büro.")
    von, bis = tag(von, "Das Startdatum"), tag(bis, "Das Enddatum")
    if von > bis:
        raise Abgelehnt("Der Zeitraum läuft rückwärts.")

    verb.execute("BEGIN IMMEDIATE")
    try:
        haus, _ = _autohaus_kreis(verb, autohaus_id)
        posten = abrechenbare(verb, autohaus_id, bis)
        if not posten:
            raise Abgelehnt(
                "Bis %s ist für %s kein offenes Fahrzeug erfasst."
                % (bis, haus["name"]))

        # Kommen Nachzügler aus der Zeit vor dem gewählten Start mit, wandert
        # der Anfang des Leistungszeitraums mit nach vorn. Sonst stuende auf der
        # Rechnung ein Zeitraum, der eigene Posten nicht abdeckt — bei einer
        # E-Rechnung mit Leistungszeitraum ist das schlicht falsch.
        nachzuegler = [f for f in posten if str(f["fertig_am"])[:10] < von]
        if nachzuegler:
            von = min(str(f["fertig_am"])[:10] for f in posten)

        try:
            zeiger = verb.execute(
                "INSERT INTO rechnung (autohaus_id, von, bis, status, netto_cent, steuersatz,"
                " erstellt_von, erstellt_am) VALUES (?,?,?,'entwurf',0,?,?,?)",
                (autohaus_id, von, bis, steuersatz, benutzer["id"], datenbank.jetzt()))
        except sqlite3.IntegrityError:
            raise Abgelehnt(
                "Für %s gibt es für %s bis %s schon eine Rechnung. Eine zweite wird nicht erzeugt."
                % (haus["name"], von, bis))

        rechnung_id = zeiger.lastrowid
        summe = 0
        for fahrzeug in posten:
            text = "%s | %s | %s" % (
                fahrzeug["fertig_am"][:10], fahrzeug["fin"], fahrzeug["leistung"])
            if fahrzeug["kennzeichen"]:
                text += " | %s" % fahrzeug["kennzeichen"]
            verb.execute(
                "INSERT INTO rechnungsposition (rechnung_id, fahrzeug_id, text, netto_cent)"
                " VALUES (?,?,?,?)",
                (rechnung_id, fahrzeug["id"], text, fahrzeug["netto_cent"]))
            gesetzt = verb.execute(
                "UPDATE fahrzeug SET status='abgerechnet', rechnung_id=?"
                " WHERE id=? AND status='offen'",
                (rechnung_id, fahrzeug["id"]))
            if gesetzt.rowcount != 1:
                raise Abgelehnt("Fahrzeug %d wurde zwischenzeitlich abgerechnet." % fahrzeug["id"])
            summe += int(fahrzeug["netto_cent"])

        verb.execute("UPDATE rechnung SET netto_cent=? WHERE id=?", (summe, rechnung_id))
        protokoll.notieren(verb, benutzer, "Sammelrechnung erzeugt",
                           "Rechnung %d" % rechnung_id,
                           "%s, %d Fahrzeuge, %s EUR netto%s"
                           % (haus["name"], len(posten), datenbank.euro(summe),
                              ", davon %d aus früheren Zeiträumen" % len(nachzuegler)
                              if nachzuegler else ""))
        verb.execute("COMMIT")
    except Exception:
        verb.execute("ROLLBACK")
        raise

    return verb.execute("SELECT * FROM rechnung WHERE id=?", (rechnung_id,)).fetchone()


def mit_positionen(verb, rechnung_id: int):
    kopf = verb.execute(
        "SELECT r.*, a.name AS autohaus, a.kurz AS autohaus_kurz, a.strasse, a.plz, a.ort,"
        " a.land, a.kaeuferreferenz, a.lieferantennummer, a.lexware_kontakt, a.zahlungsziel"
        " FROM rechnung r JOIN autohaus a ON a.id=r.autohaus_id WHERE r.id=?",
        (rechnung_id,)).fetchone()
    if not kopf:
        raise Abgelehnt("Rechnung nicht gefunden.")
    posten = verb.execute(
        "SELECT p.*, f.fin, f.fertig_am, f.kennzeichen,"
        " (SELECT COUNT(*) FROM gutschrift g WHERE g.fahrzeug_id=p.fahrzeug_id"
        "    AND g.lexware_id <> '') AS gutgeschrieben,"
        " (SELECT COUNT(*) FROM gutschrift g WHERE g.fahrzeug_id=p.fahrzeug_id"
        "    AND g.lexware_id = '') AS gutschrift_offen"
        " FROM rechnungsposition p JOIN fahrzeug f ON f.id=p.fahrzeug_id"
        " WHERE p.rechnung_id=? ORDER BY date(f.fertig_am), p.id", (rechnung_id,)).fetchall()
    return kopf, posten


def kaeuferreferenz_pruefen(kopf):
    """Die Käuferreferenz geht nur mit einem Kontakt im Rechnungsprogramm.

    Am 23.09. im ersten echten Ausstelllauf gegen Lexware belegt: Wird eine
    Käuferreferenz ohne `contactId` mitgeschickt, antwortet Lexware

        HTTP 406 — Referenced customer does not have XRechnung attributes set.
        Cannot set buyerReference in created invoice.

    Die Käuferreferenz ist die Nummer, unter der das Autohaus die Rechnung in
    seiner eigenen Buchhaltung erwartet (BT-10 der EN 16931). Ohne sie bleibt
    die E-Rechnung dort im Wareneingang liegen. Sie einfach wegzulassen wäre
    deshalb keine Lösung, sondern eine still nicht bezahlte Rechnung — und die
    rohe 406 aus dem Rechnungsprogramm sagt dem Büro nicht, was zu tun ist.
    """
    if not str(kopf["kaeuferreferenz"] or "").strip():
        return
    if str(kopf["lexware_kontakt"] or "").strip():
        return
    raise Abgelehnt(
        "Für %s ist eine Käuferreferenz hinterlegt, aber kein Lexware-Kontakt. "
        "Lexware nimmt die Käuferreferenz nur an, wenn das Autohaus dort als "
        "Kontakt mit E-Rechnungs-Angaben liegt. Entweder den Kontakt in Lexware "
        "anlegen und seine Nummer bei den Stammdaten eintragen, oder die "
        "Käuferreferenz entfernen." % kopf["autohaus"])


def anschrift_pruefen(kopf):
    """Ohne Anschrift des Empfängers ist eine Rechnung unvollständig.

    § 14 Abs. 4 Nr. 1 UStG verlangt den vollständigen Namen **und die
    Anschrift** des Leistungsempfängers. Ein Autohaus liess sich bisher mit
    Name und Kürzel allein anlegen — im Formular sind Strasse, PLZ und Ort
    nicht als Pflicht markiert — und danach ganz normal abrechnen. Der Beleg
    ginge mit leerer Anschrift zu Lexware und wäre formal defekt, ohne dass
    jemand etwas merkt.

    Ist ein Kontakt im Rechnungsprogramm hinterlegt, liegt die Anschrift dort;
    dann wird hier nichts verlangt.
    """
    if str(kopf["lexware_kontakt"] or "").strip():
        return
    fehlend = [name for name, wert in (("Strasse", kopf["strasse"]),
                                       ("PLZ", kopf["plz"]),
                                       ("Ort", kopf["ort"]))
               if not str(wert or "").strip()]
    if fehlend:
        raise Abgelehnt(
            "Für %s fehlt die Anschrift (%s). Eine Rechnung ohne Anschrift des "
            "Empfängers ist nach § 14 Abs. 4 UStG unvollständig. Bitte unter "
            "Stammdaten ergänzen." % (kopf["autohaus"], ", ".join(fehlend)))


def freigeben(verb, benutzer, rechnung_id: int):
    if benutzer["rolle"] != "buero":
        raise Abgelehnt("Freigeben darf nur das Büro.")
    kopf, _posten = mit_positionen(verb, rechnung_id)
    if kopf["status"] != "entwurf":
        raise Abgelehnt("Nur ein Entwurf kann freigegeben werden, diese ist %s." % kopf["status"])
    # Lieber hier auffallen als beim Ausstellen: Danach ist der Beleg fest.
    anschrift_pruefen(kopf)
    kaeuferreferenz_pruefen(kopf)
    # Zustandswechsel und Protokolleintrag gehören zusammen. Ohne Transaktion
    # sind es zwei Schreibvorgänge: Stirbt der Vorgang dazwischen, ist die
    # Rechnung freigegeben und **niemand steht dafür im Protokoll**.
    verb.execute("BEGIN IMMEDIATE")
    try:
        verb.execute(
            "UPDATE rechnung SET status='freigegeben', freigegeben_von=?, freigegeben_am=?"
            " WHERE id=? AND status='entwurf'",
            (benutzer["id"], datenbank.jetzt(), rechnung_id))
        protokoll.notieren(verb, benutzer, "Rechnung freigegeben",
                           "Rechnung %d" % rechnung_id,
                           "%s, %s EUR netto" % (kopf["autohaus"],
                                                 datenbank.euro(kopf["netto_cent"])))
        verb.execute("COMMIT")
    except Exception:
        verb.execute("ROLLBACK")
        raise
    return verb.execute("SELECT * FROM rechnung WHERE id=?", (rechnung_id,)).fetchone()


def stornieren(verb, benutzer, rechnung_id: int, grund: str = ""):
    """Nur vor der Finalisierung. Danach ist die Gutschrift der einzige Weg."""
    if benutzer["rolle"] != "buero":
        raise Abgelehnt("Stornieren darf nur das Büro.")
    verb.execute("BEGIN IMMEDIATE")
    try:
        kopf = verb.execute("SELECT * FROM rechnung WHERE id=?", (rechnung_id,)).fetchone()
        if not kopf:
            raise Abgelehnt("Rechnung nicht gefunden.")
        if kopf["status"] == "finalisiert":
            raise Abgelehnt(
                "Diese Rechnung ist ausgestellt und bleibt unverändert. "
                "Für ein einzelnes Fahrzeug gibt es die Gutschrift.")
        if kopf["status"] == "storniert":
            raise Abgelehnt("Schon storniert.")
        verb.execute(
            "UPDATE fahrzeug SET status='offen', rechnung_id=NULL WHERE rechnung_id=?",
            (rechnung_id,))
        inhalt = verb.execute(
            "SELECT COUNT(*) AS anzahl, COALESCE(SUM(netto_cent),0) AS summe"
            " FROM rechnungsposition WHERE rechnung_id=?", (rechnung_id,)).fetchone()
        verb.execute("DELETE FROM rechnungsposition WHERE rechnung_id=?", (rechnung_id,))
        verb.execute("UPDATE rechnung SET status='storniert', netto_cent=0 WHERE id=?",
                     (rechnung_id,))
        # Nach dem Storno sind die Positionen weg. Was daraufstand, muss
        # trotzdem nachlesbar bleiben.
        protokoll.notieren(
            verb, benutzer, "Rechnungsentwurf storniert", "Rechnung %d" % rechnung_id,
            "%d Fahrzeuge, %s EUR netto%s"
            % (inhalt["anzahl"], datenbank.euro(inhalt["summe"]),
               (", Grund: %s" % grund) if grund else ""))
        verb.execute("COMMIT")
    except Exception:
        verb.execute("ROLLBACK")
        raise


def finalisieren(verb, benutzer, rechnung_id: int, dienst):
    """Stellt die Rechnung im Rechnungsprogramm aus. Danach unveränderbar."""
    if benutzer["rolle"] != "buero":
        raise Abgelehnt("Ausstellen darf nur das Büro.")
    kopf, posten = mit_positionen(verb, rechnung_id)
    if kopf["status"] == "finalisiert":
        raise Abgelehnt("Diese Rechnung ist bereits ausgestellt (%s)." % kopf["lexware_nummer"])
    if kopf["status"] != "freigegeben":
        raise Abgelehnt("Erst freigeben, dann ausstellen. Diese ist %s." % kopf["status"])
    if len(posten) > 300:
        raise Abgelehnt(
            "%d Positionen. Das Rechnungsprogramm trägt rund 300 je Beleg. "
            "Bitte den Zeitraum teilen." % len(posten))
    anschrift_pruefen(kopf)
    kaeuferreferenz_pruefen(kopf)

    # Erst den Anspruch holen, dann erst anrufen. Ohne diesen Schritt erzeugen
    # zwei schnelle Klicks ZWEI Rechnungen mit zwei Nummern bei Lexware, und das
    # Autohaus bekommt dieselbe Leistung zweimal berechnet. Die Pruefungen oben
    # helfen dagegen nicht: zwischen Pruefung und Aufruf liegt sonst nichts.
    jetzt = datenbank.jetzt()
    anspruch = verb.execute(
        "UPDATE rechnung SET ausstellung_seit=? WHERE id=? AND status='freigegeben'"
        " AND (ausstellung_seit='' OR ausstellung_seit IS NULL"
        "      OR datetime(ausstellung_seit) < datetime(?, '-%d minute'))"
        % ANSPRUCH_MINUTEN,
        (jetzt, rechnung_id, jetzt))
    if anspruch.rowcount != 1:
        raise Abgelehnt(
            "Diese Rechnung wird gerade ausgestellt. Bitte einen Moment warten "
            "und die Seite neu laden, statt ein zweites Mal zu klicken.")
    # War die Marke alt, ist ein frueherer Versuch mitten im Aufruf abgebrochen.
    # Dann ist unklar, ob drueben schon ein Beleg liegt.
    vorher = kopf["ausstellung_seit"] if "ausstellung_seit" in kopf.keys() else ""
    if vorher:
        verb.execute(
            "UPDATE rechnung SET fehler_unklar=1, letzter_fehler=?, letzter_fehler_am=?"
            " WHERE id=? AND letzter_fehler=''",
            ("Ein früherer Versuch ist mitten im Aufruf abgebrochen.",
             jetzt, rechnung_id))

    try:
        antwort = dienst.rechnung_ausstellen(kopf, posten)
    except Exception as fehler:
        # Die Rechnung bleibt freigegeben, nichts wird halb umgestellt. Der Grund
        # wird an der Rechnung vermerkt, damit er nicht nur in der Adresszeile
        # steht und beim nächsten Laden verschwindet.
        # Ein sauberes Nein von drueben beweist nur etwas ueber DIESEN Versuch.
        # Stand vorher schon ein Zweifel im Raum — ein mitten im Aufruf
        # abgestorbener Versuch (`vorher`) oder ein frueher vermerkter
        # (`fehler_unklar`) —, dann bleibt er stehen. Vorher setzte ein 4xx
        # beim zweiten Versuch den Zweifel des ersten auf 0 zurueck: Das Buero
        # las „gefahrlos wiederholen" und haette dem Autohaus dieselbe Leistung
        # ein zweites Mal berechnet.
        frueherer_zweifel = bool(vorher) or bool(
            kopf["fehler_unklar"] if "fehler_unklar" in kopf.keys() else 0)
        unklar = 0 if (getattr(fehler, "sicher_nicht_angelegt", False)
                       and not frueherer_zweifel) else 1
        verb.execute(
            "UPDATE rechnung SET letzter_fehler=?, letzter_fehler_am=?, fehler_unklar=?"
            " WHERE id=?",
            (str(fehler)[:1000], datenbank.jetzt(), unklar, rechnung_id))
        protokoll.notieren(
            verb, benutzer, "Ausstellen fehlgeschlagen", "Rechnung %d" % rechnung_id,
            ("unklar, ob drüben schon ein Beleg entstanden ist: " if unklar else "")
            + str(fehler)[:300])
        # Anspruch wieder freigeben, sonst ist ein zweiter Versuch blockiert.
        verb.execute("UPDATE rechnung SET ausstellung_seit='' WHERE id=?",
                     (rechnung_id,))
        raise

    verb.execute("BEGIN IMMEDIATE")
    try:
        verb.execute(
            "UPDATE rechnung SET status='finalisiert', finalisiert_am=?, lexware_id=?,"
            " lexware_nummer=?, lexware_uri=?, letzter_fehler='', letzter_fehler_am='',"
            " fehler_unklar=0, ausstellung_seit='' WHERE id=? AND status='freigegeben'",
            (datenbank.jetzt(), antwort.get("id", ""), antwort.get("nummer", ""),
             antwort.get("uri", ""), rechnung_id))
        protokoll.notieren(verb, benutzer, "Rechnung ausgestellt",
                           "Rechnung %d" % rechnung_id,
                           "Beleg %s" % antwort.get("nummer", antwort.get("id", "")))
        verb.execute("COMMIT")
    except Exception:
        verb.execute("ROLLBACK")
        raise
    return verb.execute("SELECT * FROM rechnung WHERE id=?", (rechnung_id,)).fetchone()


def gutschrift(verb, benutzer, fahrzeug_id: int, grund: str, dienst):
    """Gutschrift für EIN Fahrzeug. Die übrige Rechnung bleibt unberührt."""
    if benutzer["rolle"] != "buero":
        raise Abgelehnt("Gutschriften erzeugt nur das Büro.")
    if not (grund or "").strip():
        raise Abgelehnt("Eine Gutschrift braucht einen Grund.")

    zeile = verb.execute(
        "SELECT f.*, p.netto_cent AS positionsbetrag, p.text AS positionstext,"
        " r.id AS rid, r.status AS rstatus, r.steuersatz, r.lexware_nummer,"
        " a.name AS autohaus, a.strasse, a.plz, a.ort, a.land, a.kaeuferreferenz,"
        " a.lieferantennummer, a.lexware_kontakt"
        " FROM fahrzeug f"
        " JOIN rechnungsposition p ON p.fahrzeug_id=f.id"
        " JOIN rechnung r ON r.id=p.rechnung_id"
        " JOIN autohaus a ON a.id=r.autohaus_id"
        " WHERE f.id=?", (fahrzeug_id,)).fetchone()
    if not zeile:
        raise Abgelehnt("Dieses Fahrzeug steht auf keiner Rechnung.")
    if zeile["rstatus"] != "finalisiert":
        raise Abgelehnt(
            "Die Rechnung ist noch nicht ausgestellt. Solange genügt es, den Entwurf zu "
            "stornieren und neu zu erzeugen.")
    # Für eine Rechnungskorrektur gelten dieselben Pflichtangaben wie für die
    # Rechnung (§ 14 Abs. 4 UStG). Die Anschrift kann zwischen Ausstellen und
    # Gutschrift verschwunden sein: Im Stammdatenformular sind Strasse, PLZ und
    # Ort nicht als Pflicht markiert, damit sich auch ein falscher Name allein
    # korrigieren lässt. Hier, wo der Beleg hinausgeht, wird sie verlangt.
    anschrift_pruefen(zeile)
    kaeuferreferenz_pruefen(zeile)
    # Zuerst die Zeile anlegen, DANN anrufen. Der eindeutige Schluessel auf
    # gutschrift.fahrzeug_id ist damit der Anspruch: Ein zweiter gleichzeitiger
    # Klick scheitert hier und ruft gar nicht erst bei Lexware an. Umgekehrt
    # erzeugten zwei Klicks zwei Gutschriften mit zwei Nummern.
    try:
        verb.execute(
            "INSERT INTO gutschrift (fahrzeug_id, rechnung_id, netto_cent, grund,"
            " erstellt_von, erstellt_am, lexware_id, lexware_nummer)"
            " VALUES (?,?,?,?,?,?,'','')",
            (fahrzeug_id, zeile["rid"], int(zeile["positionsbetrag"]), grund.strip(),
             benutzer["id"], datenbank.jetzt()))
    except sqlite3.IntegrityError:
        raise Abgelehnt("Für dieses Fahrzeug gibt es schon eine Gutschrift.")

    try:
        antwort = dienst.gutschrift_ausstellen(zeile, grund)
    except Exception as fehler:
        # Frueher wurde hier **nur geloescht** — kein Vermerk, kein
        # Protokolleintrag. Eine Gutschrift, die drueben vielleicht doch
        # entstanden ist, hinterliess damit keine einzige Spur: Der naechste
        # Klick haette eine ZWEITE erzeugt und dem Autohaus zweimal Geld
        # gutgeschrieben. Die Rechnung kennt diese Unterscheidung seit dem
        # ersten Durchlauf; die Gutschrift war der dritte Ausgang, an dem die
        # Regel fehlte.
        sicher = bool(getattr(fehler, "sicher_nicht_angelegt", False))
        if sicher:
            verb.execute("DELETE FROM gutschrift WHERE fahrzeug_id=? AND lexware_id=''",
                         (fahrzeug_id,))
        else:
            verb.execute(
                "UPDATE gutschrift SET letzter_fehler=?, letzter_fehler_am=?,"
                " fehler_unklar=1 WHERE fahrzeug_id=? AND lexware_id=''",
                (str(fehler)[:1000], datenbank.jetzt(), fahrzeug_id))
        protokoll.notieren(
            verb, benutzer, "Gutschrift fehlgeschlagen", "Fahrzeug %d" % fahrzeug_id,
            ("nichts angelegt, ein zweiter Versuch ist gefahrlos: "
             if sicher else
             "unklar, ob drüben schon eine Gutschrift entstanden ist — vor einem "
             "zweiten Versuch in Lexware nachsehen: ") + str(fehler)[:300])
        raise
    verb.execute(
        "UPDATE gutschrift SET lexware_id=?, lexware_nummer=? WHERE fahrzeug_id=?",
        (antwort.get("id", ""), antwort.get("nummer", ""), fahrzeug_id))
    protokoll.notieren(verb, benutzer, "Gutschrift erzeugt", "Fahrzeug %d" % fahrzeug_id,
                       "%s EUR netto, Grund: %s"
                       % (datenbank.euro(int(zeile["positionsbetrag"])), grund.strip()))
    return verb.execute(
        "SELECT * FROM gutschrift WHERE fahrzeug_id=?", (fahrzeug_id,)).fetchone()


def uebersicht(verb, grenze: int = 100):
    return verb.execute(
        "SELECT r.*, a.name AS autohaus, a.kurz AS autohaus_kurz,"
        " (SELECT COUNT(*) FROM rechnungsposition p WHERE p.rechnung_id=r.id) AS anzahl"
        " FROM rechnung r JOIN autohaus a ON a.id=r.autohaus_id"
        " ORDER BY r.id DESC LIMIT ?", (grenze,)).fetchall()

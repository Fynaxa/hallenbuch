/* Hallenbuch am Handy.
   Ohne JavaScript funktioniert das Formular ganz normal als POST. Mit
   JavaScript kommt dazu: ein Vorgangsschluessel gegen Doppelerfassung, eine
   Warteschlange fuer Netzausfaelle und, wo der Browser es kann, das Lesen des
   Barcodes am Typenschild. */
(function () {
  "use strict";

  var LAGER = "hallenbuch.warteschlange";

  function lagerName() {
    /* Die Warteschlange gehoert dem ANGEMELDETEN ZUGANG, nicht dem Geraet.
       Vorher lag sie unter einem einzigen Schluessel: Wer offline erfasst
       hatte und sich dann abmeldete, dessen Fahrzeuge gingen unter dem
       naechsten Namen raus, der sich an diesem Handy anmeldete. Im Protokoll
       stand dann der Falsche — genau das, was die Anleitung ausschliesst. */
    var wer = (document.body && document.body.getAttribute("data-nutzer")) || "0";
    return LAGER + "." + wer;
  }

  function altbestandUebernehmen() {
    /* Vor dem 22.09. lag die Schlange unter einem Schluessel OHNE Zugang. Diese
       Eintraege haben keinen Besitzer, also bekommt sie einmalig der, der sich
       als naechstes anmeldet. Danach gilt die Trennung je Zugang. */
    var alt;
    try { alt = JSON.parse(localStorage.getItem(LAGER) || "[]"); } catch (e) { return; }
    if (!alt || !alt.length) return;
    if (lagerSchreiben(lagerLesen().concat(alt))) {
      try { localStorage.removeItem(LAGER); } catch (e) { /* egal */ }
    }
  }

  function fremdeSchlangen() {
    /* Wartende Eintraege anderer Zugaenge auf diesem Geraet. Sie werden NICHT
       mitgeschickt und auch nicht geloescht: Geld darf nicht verschwinden,
       nur weil jemand anders am Handy ist. */
    var gefunden = [];
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var name = localStorage.key(i);
        if (name && name.indexOf(LAGER + ".") === 0 && name !== lagerName()) {
          var wie_viele = (JSON.parse(localStorage.getItem(name) || "[]") || []).length;
          if (wie_viele) gefunden.push(wie_viele);
        }
      }
    } catch (e) { return []; }
    return gefunden;
  }
  var ABGELEHNT = "hallenbuch.abgelehnt";

  function abgelehntName() {
    var wer = (document.body && document.body.getAttribute("data-nutzer")) || "0";
    return ABGELEHNT + "." + wer;
  }

  function abgelehntLesen() {
    try { return JSON.parse(localStorage.getItem(abgelehntName()) || "[]"); }
    catch (e) { return []; }
  }

  function abgelehntSchreiben(liste) {
    try {
      localStorage.setItem(abgelehntName(), JSON.stringify(liste));
      return true;
    } catch (e) {
      return false;
    }
  }

  function abgelehntMerken(posten, grund) {
    /* Eine fachlich abgelehnte Erfassung war bis zum 23.09. nach dem Nachladen
       WEG: aus der Schlange geloescht, und der einzige Nachweis war eine
       Meldung am Bildschirm, die die naechste Erfassung ueberschreibt. Wer sie
       nicht in dem Moment las, dessen Fahrzeug wurde nie berechnet und niemand
       erfuhr davon. Jetzt bleibt sie stehen, bis ein Mensch sie wegnimmt. */
    var liste = abgelehntLesen();
    liste.push({ vorgang: posten.vorgang, fin: posten.fin || "",
                 kennzeichen: posten.kennzeichen || "",
                 grund: grund || "abgelehnt", wann: new Date().toISOString() });
    return abgelehntSchreiben(liste);
  }

  function abgelehntEntfernen(vorgang) {
    return abgelehntSchreiben(abgelehntLesen().filter(function (p) {
      return p.vorgang !== vorgang;
    }));
  }

  var LEER = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";

  function schluessel() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "v-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }

  function lagerLesen() {
    try { return JSON.parse(localStorage.getItem(lagerName()) || "[]"); }
    catch (e) { return []; }
  }

  function lagerSchreiben(liste) {
    /* Gibt zurueck, ob es geklappt hat. Frueher wurde ein voller Speicher
       stillschweigend verschluckt — das Fahrzeug war dann einfach weg. */
    try {
      localStorage.setItem(lagerName(), JSON.stringify(liste));
      return true;
    } catch (e) {
      return false;
    }
  }

  function lagerAnhaengen(posten) {
    var liste = lagerLesen();
    liste.push(posten);
    return lagerSchreiben(liste);
  }

  function lagerEntfernen(vorgang) {
    /* Immer frisch lesen und nur DIESEN einen Eintrag entfernen.
       Frueher schrieb das Nachladen eine vorher gelesene Restliste zurueck.
       Wer waehrenddessen ein neues Fahrzeug erfasste, verlor es lautlos. */
    var liste = lagerLesen().filter(function (p) { return p.vorgang !== vorgang; });
    return lagerSchreiben(liste);
  }

  var quittungUhr = null;

  function spueren(schlecht) {
    /* Kurzes Rütteln als zweiter Kanal. iOS kann das nicht, deshalb ist die
       Quittung selbst so gebaut, dass sie auch ohne Vibration auffällt. */
    try {
      if (navigator.vibrate) navigator.vibrate(schlecht ? [60, 40, 60] : 35);
    } catch (e) { /* manche Browser werfen ohne Nutzergeste */ }
  }

  function melden(text, schlecht, betonung) {
    /* Text wird als TEXT gesetzt, nicht als HTML. Er stammt teils aus
       Serverantworten, die Nutzereingaben zurueckgeben; mit innerHTML waere
       das eine offene Tuer. `betonung` ist die hervorgehobene FIN. */
    var quittung = document.getElementById("quittung");
    var ziel = quittung;
    if (!ziel) {
      ziel = document.getElementById("warteschlange-melder");
      if (!ziel) {
        ziel = document.createElement("div");
        ziel.id = "warteschlange-melder";
        var haupt = document.querySelector("main");
        if (haupt) haupt.insertBefore(ziel, haupt.firstChild);
      }
    }
    if (!ziel) return;

    while (ziel.firstChild) ziel.removeChild(ziel.firstChild);
    var etikett = document.createElement("b");
    etikett.textContent = schlecht ? "Achtung" : (quittung ? "Erfasst" : "Erledigt");
    ziel.appendChild(etikett);
    ziel.appendChild(document.createTextNode(" " + text));
    if (betonung) {
      var stelle = document.createElement("span");
      stelle.className = quittung ? "fin-quittung" : "";
      stelle.textContent = betonung;
      ziel.appendChild(document.createTextNode(" "));
      ziel.appendChild(stelle);
    }

    if (quittung) {
      ziel.className = "quittung" + (schlecht ? " schlecht" : "");
      ziel.hidden = false;
      spueren(schlecht);
      if (quittungUhr) clearTimeout(quittungUhr);
      // Fehler bleiben stehen, bis der Nächste etwas tut. Erfolg verschwindet.
      if (!schlecht) {
        quittungUhr = setTimeout(function () { ziel.hidden = true; }, 4500);
      }
    } else {
      ziel.className = "meldung " + (schlecht ? "schlecht" : "gut");
    }
  }

  var laedtNach = false;

  function nachladen() {
    /* Arbeitet die Warteschlange ab. Drei Regeln, jede aus einem Fehler geboren:
       1. Jeder Eintrag wird EINZELN und erst NACH seinem Erfolg entfernt.
       2. Eine fachliche Ablehnung (400) wird NICHT stillschweigend verworfen —
          sie wird gemeldet, mit FIN und Grund, damit der Wagen neu erfasst
          werden kann. Sonst verschwindet er und niemand merkt es.
       3. Es laeuft immer nur ein Durchgang.
       4. Kennzahl 401 heisst abgemeldet: alles bleibt liegen, der Durchgang
          bricht ab und der Grund wird gesagt. Frueher antwortete der Server
          mit einer Umleitung auf die Anmeldeseite, fetch folgte ihr und lieferte
          200 — die Eintraege galten als gespeichert und wurden geloescht. */
    if (laedtNach) return Promise.resolve();
    var liste = lagerLesen();
    if (!liste.length) return Promise.resolve();
    laedtNach = true;
    var erledigt = 0;
    var abgelehnt = [];
    var abgemeldet = false;

    return liste.reduce(function (kette, posten) {
      return kette.then(function () {
        if (abgemeldet) return null;     /* ohne Anmeldung hat der Rest keinen Zweck */
        return fetch("/api/erfassen", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(posten)
        }).then(function (a) {
          return a.json().catch(function () { return {}; }).then(function (d) {
            if (a.ok) {
              lagerEntfernen(posten.vorgang);
              erledigt++;
            } else if (a.status === 400) {
              /* Erst sichern, dann loeschen. Klappt das Sichern nicht (voller
                 Speicher), bleibt der Eintrag lieber in der Schlange stehen
                 und wird erneut versucht, als dass er verschwindet. */
              if (abgelehntMerken(posten, d.fehler)) {
                lagerEntfernen(posten.vorgang);
                abgelehnt.push({ fin: posten.fin || "(ohne FIN)",
                                 grund: d.fehler || "abgelehnt" });
              }
            } else if (a.status === 401) {
              abgemeldet = true;
            }
            // 5xx: bleibt liegen, wird beim naechsten Mal erneut versucht.
          });
        }).catch(function () { /* kein Netz: bleibt liegen */ });
      });
    }, Promise.resolve()).then(function () {
      laedtNach = false;
      zaehlerZeigen();
      abgelehnteZeigen();
      if (abgemeldet) {
        melden("Du bist nicht mehr angemeldet. Bitte neu anmelden. Das Gemerkte "
               + "bleibt gespeichert und geht danach von selbst raus.", true);
      } else if (abgelehnt.length) {
        melden(abgelehnt.length + (abgelehnt.length === 1 ? " gemerkte Erfassung"
                                   : " gemerkte Erfassungen")
               + " konnte der Server nicht annehmen und " +
               (abgelehnt.length === 1 ? "ist" : "sind") +
               " NICHT gespeichert. Bitte neu erfassen: "
               + abgelehnt.map(function (a) { return a.fin + " (" + a.grund + ")"; })
                          .join("; "), true);
      } else if (erledigt) {
        melden(erledigt === 1 ? "Die gemerkte Erfassung ist nachgeladen."
               : erledigt + " gemerkte Erfassungen sind nachgeladen.", false);
      }
    }, function () { laedtNach = false; });
  }

  function fremdeMelden() {
    var fremd = fremdeSchlangen();
    if (!fremd.length) return;
    var summe = fremd.reduce(function (a, b) { return a + b; }, 0);
    melden(summe + (summe === 1 ? " gemerkte Erfassung gehört" : " gemerkte Erfassungen gehören")
           + " zu einem anderen Zugang auf diesem Handy. Sie gehen erst raus, wenn sich "
           + "dieser Zugang hier wieder anmeldet. Gelöscht wird nichts.", true);
  }

  function abgelehnteZeigen() {
    /* Steht auf der Erfassungsseite, solange etwas offen ist. Kein Zeitablauf,
       keine Quittung, die es wegwischt: Die Zeile verschwindet erst, wenn
       jemand „Erledigt" tippt. */
    var kasten = document.getElementById("abgelehnte");
    var liste = abgelehntLesen();
    if (!kasten) return;
    while (kasten.firstChild) kasten.removeChild(kasten.firstChild);
    kasten.hidden = !liste.length;
    if (!liste.length) return;
    var titel = document.createElement("b");
    titel.textContent = "Nicht gespeichert";
    kasten.appendChild(titel);
    var satz = document.createElement("p");
    satz.textContent = liste.length === 1
      ? "Diese Erfassung hat der Server nicht angenommen. Bitte neu erfassen."
      : liste.length + " Erfassungen hat der Server nicht angenommen. "
        + "Bitte neu erfassen.";
    kasten.appendChild(satz);
    liste.forEach(function (p) {
      var zeile = document.createElement("p");
      zeile.className = "grund";
      zeile.textContent = (p.fin || "ohne FIN") + " — " + p.grund;
      var weg = document.createElement("button");
      weg.type = "button";
      weg.className = "klein";
      weg.textContent = "Erledigt";
      weg.addEventListener("click", function () {
        abgelehntEntfernen(p.vorgang);
        abgelehnteZeigen();
      });
      zeile.appendChild(document.createTextNode(" "));
      zeile.appendChild(weg);
      kasten.appendChild(zeile);
    });
  }

  function zaehlerZeigen() {
    var anzahl = lagerLesen().length;
    var marke = document.getElementById("warteschlange-zahl");
    if (!marke) return;
    marke.textContent = anzahl ? (anzahl === 1 ? "1 Fahrzeug wartet auf Netz"
                                  : anzahl + " Fahrzeuge warten auf Netz") : "";
    marke.hidden = !anzahl;
  }

  function formularDaten(form) {
    var daten = {};
    new FormData(form).forEach(function (wert, name) { daten[name] = wert; });
    return daten;
  }

  /* Nur fuer Tests: die Warteschlangenlogik von aussen erreichbar machen.
     Sie ist der Teil, an dem Datenverlust haengt, und gehoert geprueft. */
  if (typeof window !== "undefined") {
    window.__hallenbuch = {
      lagerLesen: lagerLesen, lagerSchreiben: lagerSchreiben,
      lagerAnhaengen: lagerAnhaengen, lagerEntfernen: lagerEntfernen,
      nachladen: nachladen, melden: melden, LAGER: LAGER,
      lagerName: lagerName, fremdeSchlangen: fremdeSchlangen,
      fremdeMelden: fremdeMelden, altbestandUebernehmen: altbestandUebernehmen,
      fotoKnoten: fotoKnoten, scannen: scannen,
      ABGELEHNT: ABGELEHNT, abgelehntName: abgelehntName,
      abgelehntLesen: abgelehntLesen, abgelehntMerken: abgelehntMerken,
      abgelehntEntfernen: abgelehntEntfernen, abgelehnteZeigen: abgelehnteZeigen
    };
  }

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () { /* egal */ });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.getElementById("erfassung");
    if (!form) { return; }

    var vorgang = document.getElementById("vorgang");
    if (vorgang && !vorgang.value) vorgang.value = schluessel();

    var marke = document.createElement("div");
    marke.id = "warteschlange-zahl";
    marke.className = "hinweis-klein";
    marke.hidden = true;
    form.appendChild(marke);
    zaehlerZeigen();

    var kasten = document.createElement("div");
    kasten.id = "abgelehnte";
    kasten.className = "stoerung";
    kasten.hidden = true;
    form.appendChild(kasten);
    abgelehnteZeigen();

    var fin = document.getElementById("fin");
    if (fin) {
      fin.addEventListener("input", function () {
        var vorher = fin.value;
        var nachher = vorher.toUpperCase()
          .replace(/[\s\-_.]/g, "")
          .replace(/I/g, "1").replace(/[OQ]/g, "0");
        if (nachher !== vorher) fin.value = nachher;
        var hinweis = document.getElementById("fin-hinweis");
        if (hinweis) {
          hinweis.textContent = nachher.length === 17
            ? "17 Zeichen, passt."
            : nachher.length + " von 17 Zeichen.";
        }
      });
    }

    form.addEventListener("submit", function (ereignis) {
      ereignis.preventDefault();
      var daten = formularDaten(form);
      if (!daten.vorgang) daten.vorgang = schluessel();

      fetch("/api/erfassen", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(daten)
      }).then(function (antwort) {
        return antwort.json().then(function (d) {
          if (!d.ok) { melden(d.fehler || "Abgelehnt.", true); return; }
          var text = "Nr. " + d.id;
          if (d.hinweise && d.hinweise.length) text += " " + d.hinweise.join(" ");
          melden(text, false, d.fin);
          if (fin) fin.value = "";
          var kz = form.querySelector('[name="kennzeichen"]');
          var notiz = form.querySelector('[name="notiz"]');
          if (kz) kz.value = "";
          if (notiz) notiz.value = "";
          document.getElementById("vorgang").value = schluessel();
          if (fin) fin.focus();
        });
      }).catch(function () {
        if (!lagerAnhaengen(daten)) {
          melden("Kein Netz UND der Speicher im Handy ist voll. Dieses Fahrzeug "
                 + "ist NICHT gespeichert. Bitte auf Papier notieren und später "
                 + "nachtragen.", true, daten.fin);
          return;
        }
        zaehlerZeigen();
        melden("Kein Netz. Fahrzeug ist gemerkt und wird nachgeladen, sobald wieder "
               + "Empfang da ist. Weiter geht es ganz normal.", false, daten.fin);
        if (fin) fin.value = "";
        document.getElementById("vorgang").value = schluessel();
        if (fin) fin.focus();
      });
    });

    window.addEventListener("online", nachladen);
    altbestandUebernehmen();
    nachladen();
    fremdeMelden();

    fotosVerdrahten();
    lesehilfeVerdrahten(fin);

    // Barcode am Typenschild, wo der Browser es kann. Tippen bleibt gleichwertig.
    var knopf = document.getElementById("scannen");
    if (knopf && "BarcodeDetector" in window) {
      knopf.hidden = false;
      knopf.addEventListener("click", function () { scannen(knopf, fin); });
    }
  });

  /* Lesehilfe. Der Browser kann keine Texterkennung: BarcodeDetector ist da,
     TextDetector hat es nie in die Auslieferung geschafft. Wir versuchen ihn,
     wo er wider Erwarten existiert, und zeigen sonst schlicht das Bild gross an,
     damit man es vom Bildschirm abtippt statt sich vor die Scheibe zu bücken. */
  function lesehilfeVerdrahten(fin) {
    var knopf = document.getElementById("lesehilfe-knopf");
    var kasten = document.getElementById("lesehilfe");
    var bild = document.getElementById("lesehilfe-bild");
    var weg = document.getElementById("lesehilfe-weg");
    var feld = document.getElementById("foto-datei");
    if (!knopf || !kasten || !bild || !feld) return;
    var wartet = false;

    knopf.addEventListener("click", function () {
      wartet = true;
      feld.value = "";
      feld.click();
    });

    weg.addEventListener("click", function () {
      kasten.hidden = true;
      bild.src = LEER;   // kein src-loses img zuruecklassen
    });

    feld.addEventListener("change", function () {
      if (!wartet) return;
      wartet = false;
      var datei = feld.files && feld.files[0];
      if (!datei) return;
      knopf.disabled = true;
      verkleinern(datei, 1600, 0.85).then(function (daten) {
        bild.src = daten;
        kasten.hidden = false;
        kasten.scrollIntoView({ block: "nearest" });
        return textErkennen(bild);
      }).then(function (treffer) {
        if (treffer && fin) {
          fin.value = treffer;
          fin.dispatchEvent(new Event("input"));
          melden("Aus dem Bild gelesen: " + treffer + ". Bitte gegenlesen.", false);
        }
      }).catch(function () {
        melden("Die Aufnahme hat nicht geklappt. Bitte tippen.", true);
      }).then(function () { knopf.disabled = false; });
    }, true);
  }

  function textErkennen(bild) {
    if (!("TextDetector" in window)) return Promise.resolve(null);
    return new window.TextDetector().detect(bild).then(function (bloecke) {
      var alles = bloecke.map(function (b) { return b.rawValue || ""; }).join(" ");
      var roh = alles.toUpperCase().replace(/[\s\-_.]/g, "")
        .replace(/I/g, "1").replace(/[OQ]/g, "0");
      var gefunden = roh.match(/[ABCDEFGHJKLMNPRSTUVWXYZ0-9]{17}/);
      return gefunden ? gefunden[0] : null;
    }).catch(function () { return null; });
  }

  /* Fotos am Fahrzeug. Das Bild wird VOR dem Senden verkleinert: ein Handyfoto
     hat gern 5 MB, und in der Halle haengt das Netz ohnehin. */
  function verkleinern(datei, kante, guete) {
    return new Promise(function (fertig, daneben) {
      var leser = new FileReader();
      leser.onerror = function () { daneben(new Error("Datei nicht lesbar.")); };
      leser.onload = function () {
        var bild = new Image();
        bild.onerror = function () { daneben(new Error("Kein lesbares Bild.")); };
        bild.onload = function () {
          var f = Math.min(1, kante / Math.max(bild.width, bild.height));
          var leinwand = document.createElement("canvas");
          leinwand.width = Math.round(bild.width * f);
          leinwand.height = Math.round(bild.height * f);
          leinwand.getContext("2d").drawImage(bild, 0, 0, leinwand.width, leinwand.height);
          fertig(leinwand.toDataURL("image/jpeg", guete));
        };
        bild.src = leser.result;
      };
      leser.readAsDataURL(datei);
    });
  }

  function fotosVerdrahten() {
    var feld = document.getElementById("foto-datei");
    if (!feld) return;
    var offen = null;

    document.addEventListener("click", function (ereignis) {
      var knopf = ereignis.target.closest ? ereignis.target.closest(".foto-knopf") : null;
      if (!knopf) return;
      offen = knopf;
      feld.value = "";
      feld.click();
    });

    feld.addEventListener("change", function () {
      var datei = feld.files && feld.files[0];
      if (!datei || !offen) return;   // Lesehilfe hat keinen Knopf gesetzt
      var knopf = offen;
      var fahrzeug = knopf.getAttribute("data-fahrzeug");
      var art = knopf.getAttribute("data-art");
      knopf.setAttribute("aria-busy", "true");
      knopf.disabled = true;

      verkleinern(datei, 1600, 0.82).then(function (daten) {
        return fetch("/api/foto", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ fahrzeug_id: fahrzeug, art: art, daten: daten })
        }).then(function (a) { return a.json(); });
      }).then(function (d) {
        if (!d.ok) { melden(d.fehler || "Foto abgelehnt.", true); return; }
        var reihe = document.querySelector('.fotos[data-fuer="' + fahrzeug + '"]');
        if (reihe) {
          reihe.insertBefore(fotoKnoten(d.adresse, knopf.textContent, art),
                             reihe.querySelector(".foto-wahl"));
        }
        melden("Foto gespeichert.", false);
      }).catch(function () {
        melden("Foto konnte nicht gesendet werden. Ohne Netz geht das nicht, "
               + "das Fahrzeug selbst bleibt aber erfasst.", true);
      }).then(function () {
        knopf.removeAttribute("aria-busy");
        knopf.disabled = false;
        offen = null;
      });
    });
  }

  function fotoKnoten(adresse, beschriftung, art) {
    /* Dasselbe Markup wie auf der vom Server gebauten Seite. Ohne diese
       Funktion fehlte dem gerade hochgeladenen Schadenbild die rote Umrandung,
       bis jemand die Seite neu lud — ausgerechnet dem Bild, auf das es
       ankommt. */
    var a = document.createElement("a");
    a.href = adresse;
    a.target = "_blank";
    a.rel = "noopener";
    if (art === "schaden") { a.className = "schaden"; a.title = "Schaden"; }
    var bild = document.createElement("img");
    bild.src = adresse;
    bild.alt = beschriftung;
    a.appendChild(bild);
    return a;
  }

  function scannen(knopf, fin) {
    var leser = new window.BarcodeDetector({
      formats: ["code_128", "code_39", "data_matrix", "qr_code", "pdf417", "itf"]
    });
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } })
      .then(function (strom) {
        var video = document.createElement("video");
        video.setAttribute("playsinline", "");
        video.style.cssText = "position:fixed;inset:0;width:100%;height:100%;"
          + "object-fit:cover;z-index:50;background:#000";
        document.body.appendChild(video);
        var abbrechen = document.createElement("button");
        abbrechen.textContent = "Abbrechen";
        abbrechen.className = "knopf";
        abbrechen.style.cssText = "position:fixed;left:1rem;right:1rem;bottom:1.5rem;z-index:51";
        document.body.appendChild(abbrechen);

        var laeuft = null;
        var beendet = false;

        function aufraeumen() {
          /* Der Abbrechen-Knopf hielt frueher nur die Kamera an, NICHT die
             Suchschleife. Die lief danach dreimal je Sekunde weiter und griff
             auf ein entferntes Videobild zu — jeder Versuch scheiterte still,
             und das bis die Seite geschlossen wurde. Auf einem Hallenhandy,
             das den ganzen Tag offen liegt, ist das der Akku. */
          if (beendet) return;
          beendet = true;
          if (laeuft) { clearInterval(laeuft); laeuft = null; }
          strom.getTracks().forEach(function (s) { s.stop(); });
          video.remove();
          abbrechen.remove();
        }
        abbrechen.addEventListener("click", aufraeumen);

        video.srcObject = strom;
        video.play();
        laeuft = setInterval(function () {
          leser.detect(video).then(function (treffer) {
            for (var i = 0; i < treffer.length; i++) {
              var roh = (treffer[i].rawValue || "").toUpperCase()
                .replace(/[\s\-_.]/g, "").replace(/I/g, "1").replace(/[OQ]/g, "0");
              var gefunden = roh.match(/[ABCDEFGHJKLMNPRSTUVWXYZ0-9]{17}/);
              if (gefunden) {
                aufraeumen();
                if (fin) { fin.value = gefunden[0]; fin.dispatchEvent(new Event("input")); }
                return;
              }
            }
          }).catch(function () { /* naechster Versuch */ });
        }, 350);
      })
      .catch(function () {
        knopf.textContent = "Kamera nicht verfügbar, bitte tippen";
        knopf.disabled = true;
      });
  }
})();

/* Prueft die Warteschlange aus app.js in einer nachgebauten Handy-Umgebung.
   Kein Browser noetig, keine Fremdpakete: nur Node und ein paar Attrappen.
   Aufgerufen von tests/test_warteschlange.py. */
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const hier = path.dirname(fileURLToPath(import.meta.url));
const quelle = fs.readFileSync(
  path.join(hier, '..', 'hallenbuch', 'statisch', 'app.js'), 'utf8');

let fehler = 0;
function pruefe(was, bedingung, zusatz) {
  if (bedingung) { console.log('  ok   ' + was); }
  else { console.log('  FEHL ' + was + (zusatz ? ' — ' + zusatz : '')); fehler++; }
}

function knoten() {
  const k = {
    children: [], className: '', textContent: '', hidden: true, id: '',
    href: '', src: '', alt: '', title: '', target: '', rel: '', style: {},
    remove() { k.__entfernt = true; },
    play() { k.__laeuft = true; },
    srcObject: null,
    insertBefore(kind) { k.children.unshift(kind); return kind; },
    get firstChild() { return k.children[0] || null; },
    appendChild(kind) { k.children.push(kind); return kind; },
    removeChild(kind) { k.children = k.children.filter(x => x !== kind); },
    __hoerer: {},
    addEventListener(art, fn) { k.__hoerer[art] = fn; },
    querySelector() { return null; },
    setAttribute() {}, removeAttribute() {},
  };
  return k;
}

function umgebung(antworten, geteilt, nutzer) {
  const speicher = geteilt || new Map();
  const meldungen = [];
  const quittung = knoten();
  const kontext = {
    console,
    setTimeout: (fn, ms) => { kontext.__uhren.push({ fn, ms }); return kontext.__uhren.length; },
    clearTimeout: (id) => { if (id) kontext.__uhren[id - 1] = null; },
    setInterval: (fn, ms) => { kontext.__takte.push({ fn, ms }); return kontext.__takte.length; },
    clearInterval: (id) => { if (id) kontext.__takte[id - 1] = null; },
    localStorage: {
      getItem: (k) => (speicher.has(k) ? speicher.get(k) : null),
      setItem: (k, v) => {
        /* `__voll` = der ganze Speicher ist dicht. `__vollFuer` = nur Schluessel
           mit diesem Anfang koennen nicht mehr wachsen. Letzteres ist der echte
           Fall: Loeschen gibt Platz frei, Anlegen nicht. */
        if (kontext.__voll) throw new Error('QuotaExceeded');
        if (kontext.__vollFuer && k.indexOf(kontext.__vollFuer) === 0) {
          throw new Error('QuotaExceeded');
        }
        speicher.set(k, v);
      },
      removeItem: (k) => { speicher.delete(k); },
      get length() { return speicher.size; },
      key: (i) => Array.from(speicher.keys())[i] ?? null,
    },
    navigator: { vibrate: () => { kontext.__geruettelt = true; } },
    document: {
      body: Object.assign(knoten(), {
        getAttribute: (name) => (name === 'data-nutzer' ? (nutzer || '0') : null),
      }),
      addEventListener() {},
      getElementById: (id) => (id === 'quittung' ? quittung : null),
      querySelector: () => null,
      createElement: () => knoten(),
      createTextNode: (t) => ({ text: t }),
    },
    fetch: (weg, gaben) => antworten(JSON.parse(gaben.body)),
    __voll: false,
    __vollFuer: '',
    __uhren: [],
    __takte: [],
    __meldungen: meldungen,
    __quittung: quittung,
  };
  kontext.window = kontext;
  vm.createContext(kontext);
  vm.runInContext(quelle, kontext);
  return kontext;
}

function antwort(status, koerper) {
  return Promise.resolve({
    ok: status >= 200 && status < 300, status,
    json: () => Promise.resolve(koerper),
  });
}

function text(k) {
  return k.children.map(c => (c.text !== undefined ? c.text : c.textContent)).join('');
}

/* --- 1. Anhaengen und Lesen --- */
{
  const u = umgebung(() => antwort(200, { ok: true }));
  const h = u.window.__hallenbuch;
  pruefe('leere Warteschlange liest sich als leere Liste', h.lagerLesen().length === 0);
  pruefe('anhaengen meldet Erfolg', h.lagerAnhaengen({ vorgang: 'a', fin: 'X' }) === true);
  pruefe('der Eintrag ist da', h.lagerLesen().length === 1);
}

/* --- 2. Voller Speicher wird gemeldet, nicht verschluckt --- */
{
  const u = umgebung(() => antwort(200, { ok: true }));
  u.__voll = true;
  pruefe('voller Speicher meldet Misserfolg',
         u.window.__hallenbuch.lagerAnhaengen({ vorgang: 'b' }) === false);
}

/* --- 3. Nachladen verliert nichts, was waehrenddessen dazukommt --- */
{
  let erster = true;
  let freigeben;
  const warten = new Promise(r => { freigeben = r; });
  const u = umgebung(() => {
    if (erster) { erster = false; return warten.then(() => antwort(200, { ok: true })); }
    return antwort(200, { ok: true });
  });
  const h = u.window.__hallenbuch;
  h.lagerAnhaengen({ vorgang: 'alt-1', fin: 'A' });
  const lauf = h.nachladen();
  // Waehrend der erste Eintrag unterwegs ist, erfasst jemand ein neues Auto:
  h.lagerAnhaengen({ vorgang: 'neu-1', fin: 'B' });
  freigeben();
  await lauf;
  const uebrig = h.lagerLesen().map(p => p.vorgang);
  pruefe('das waehrenddessen erfasste Fahrzeug ist NICHT verloren',
         uebrig.includes('neu-1'), 'uebrig: ' + JSON.stringify(uebrig));
  pruefe('das nachgeladene ist entfernt', !uebrig.includes('alt-1'));
}

/* --- 4. Fachliche Ablehnung wird gemeldet, nicht stillschweigend verworfen --- */
{
  const u = umgebung((koerper) =>
    koerper.vorgang === 'schlecht'
      ? antwort(400, { ok: false, fehler: 'Fertig am liegt in der Zukunft' })
      : antwort(200, { ok: true }));
  const h = u.window.__hallenbuch;
  h.lagerAnhaengen({ vorgang: 'gut', fin: 'GUT' });
  h.lagerAnhaengen({ vorgang: 'schlecht', fin: 'WVWZZZ1JZ3W386752' });
  await h.nachladen();
  const gesagt = text(u.__quittung);
  pruefe('die Ablehnung wird deutlich gemeldet', /NICHT gespeichert/.test(gesagt), gesagt);
  pruefe('die FIN des abgelehnten Fahrzeugs steht in der Meldung',
         gesagt.includes('WVWZZZ1JZ3W386752'), gesagt);
  pruefe('der Grund steht dabei', gesagt.includes('Zukunft'), gesagt);
  pruefe('die Meldung ist als Fehler gekennzeichnet',
         /schlecht/.test(u.__quittung.className), u.__quittung.className);
  pruefe('die Warteschlange ist leer, nichts haengt fest',
         h.lagerLesen().length === 0);
}

/* --- 4b. Die Ablehnung ueberlebt die naechste Meldung und den Neustart --- */
{
  const speicher = new Map();
  const u = umgebung((koerper) =>
    koerper.vorgang === 'schlecht'
      ? antwort(400, { ok: false, fehler: 'Fertig am liegt in der Zukunft' })
      : antwort(200, { ok: true }), speicher, '7');
  const h = u.window.__hallenbuch;
  h.lagerAnhaengen({ vorgang: 'schlecht', fin: 'WVWZZZ1JZ3W386752' });
  await h.nachladen();
  pruefe('die abgelehnte Erfassung ist gemerkt, nicht weg',
         h.abgelehntLesen().length === 1, JSON.stringify(h.abgelehntLesen()));
  const vermerk = h.abgelehntLesen()[0] || {};
  pruefe('mit FIN und Grund',
         vermerk.fin === 'WVWZZZ1JZ3W386752' && /Zukunft/.test(vermerk.grund || ''));

  /* Die naechste Erfassung ueberschreibt die Bildschirmmeldung. Frueher war
     das der Moment, in dem das Fahrzeug endgueltig unsichtbar wurde. */
  h.melden('Nr. 42', false, 'WAUZZZ8V1JA123456');
  pruefe('die Bildschirmmeldung ist weg', !/NICHT gespeichert/.test(text(u.__quittung)));
  pruefe('der Vermerk ist trotzdem noch da', h.abgelehntLesen().length === 1);

  /* Neustart der App: dieselbe Ablage, frischer Lauf. */
  const zwei = umgebung(() => antwort(200, { ok: true }), speicher, '7');
  pruefe('nach dem Neustart steht er immer noch da',
         zwei.window.__hallenbuch.abgelehntLesen().length === 1);

  /* Und er gehoert dem Zugang, nicht dem Geraet. */
  const fremd = umgebung(() => antwort(200, { ok: true }), speicher, '8');
  pruefe('ein anderer Zugang sieht ihn nicht',
         fremd.window.__hallenbuch.abgelehntLesen().length === 0);

  pruefe('erst ein Mensch nimmt ihn weg',
         zwei.window.__hallenbuch.abgelehntEntfernen('schlecht')
         && zwei.window.__hallenbuch.abgelehntLesen().length === 0);
}

/* --- 4c. Geht das Merken nicht, wird auch nicht geloescht --- */
{
  const u = umgebung(() => antwort(400, { ok: false, fehler: 'Fertig am liegt in der Zukunft' }));
  const h = u.window.__hallenbuch;
  h.lagerAnhaengen({ vorgang: 'schlecht', fin: 'WVWZZZ1JZ3W386752' });
  /* Nur der Vermerk laesst sich nicht mehr schreiben; die Schlange sehr wohl.
     Genau so verhaelt sich ein Browser am Rand des Speicherplatzes. */
  u.__vollFuer = 'hallenbuch.abgelehnt';
  await h.nachladen();
  pruefe('laesst sich der Vermerk nicht schreiben, bleibt der Eintrag stehen',
         h.lagerLesen().length === 1, JSON.stringify(h.lagerLesen()));
  pruefe('und ist damit beim naechsten Versuch noch da',
         h.lagerLesen()[0] && h.lagerLesen()[0].vorgang === 'schlecht');
}

/* --- 5. Serverfehler bleibt liegen und wird spaeter erneut versucht --- */
{
  let versuche = 0;
  const u = umgebung(() => { versuche++; return antwort(503, {}); });
  const h = u.window.__hallenbuch;
  h.lagerAnhaengen({ vorgang: 'spaeter', fin: 'S' });
  await h.nachladen();
  pruefe('bei 503 bleibt der Eintrag in der Warteschlange',
         h.lagerLesen().length === 1);
  await h.nachladen();
  pruefe('und wird erneut versucht', versuche === 2, 'Versuche: ' + versuche);
}

/* --- 5b. Abgelaufene Sitzung: nichts loeschen, Grund sagen, abbrechen --- */
{
  let aufrufe = 0;
  const u = umgebung(() => { aufrufe++; return antwort(401, { ok: false, fehler: 'Nicht mehr angemeldet.' }); });
  const h = u.window.__hallenbuch;
  h.lagerAnhaengen({ vorgang: 'nacht-1', fin: 'WVWZZZ1JZ3W386752' });
  h.lagerAnhaengen({ vorgang: 'nacht-2', fin: 'WAUZZZ8V1JA123456' });
  await h.nachladen();
  pruefe('bei 401 bleibt NICHTS auf der Strecke', h.lagerLesen().length === 2,
         'uebrig: ' + h.lagerLesen().length);
  const gesagt = text(u.__quittung);
  pruefe('der Grund wird gesagt', /neu anmelden/.test(gesagt), gesagt);
  pruefe('und nicht als Erfolg verkauft', !/nachgeladen/.test(gesagt), gesagt);
  pruefe('die Meldung ist als Fehler gekennzeichnet',
         /schlecht/.test(u.__quittung.className), u.__quittung.className);
  pruefe('der Durchgang bricht nach dem ersten 401 ab', aufrufe === 1,
         'Aufrufe: ' + aufrufe);
}

/* --- 5c. Die Warteschlange gehoert dem Zugang, nicht dem Handy --- */
{
  const geteilt = new Map();
  const geschickt = [];
  const antwort200 = (k) => { geschickt.push(k.vorgang); return antwort(200, { ok: true }); };

  // Halle 1 erfasst offline zwei Fahrzeuge.
  const a = umgebung(antwort200, geteilt, '2');
  a.window.__hallenbuch.lagerAnhaengen({ vorgang: 'a-1', fin: 'AAA' });
  a.window.__hallenbuch.lagerAnhaengen({ vorgang: 'a-2', fin: 'BBB' });
  pruefe('der erste Zugang hat zwei Eintraege',
         a.window.__hallenbuch.lagerLesen().length === 2);

  // Halle 2 meldet sich am SELBEN Handy an.
  const b = umgebung(antwort200, geteilt, '3');
  const h = b.window.__hallenbuch;
  pruefe('der zweite Zugang sieht die fremden Eintraege NICHT',
         h.lagerLesen().length === 0);
  await h.nachladen();
  pruefe('und schickt sie auch nicht unter seinem Namen los',
         geschickt.length === 0, 'geschickt: ' + JSON.stringify(geschickt));

  h.fremdeMelden();
  const gesagt = text(b.__quittung);
  pruefe('aber er erfaehrt, dass da etwas wartet', /anderen Zugang/.test(gesagt), gesagt);
  pruefe('mit der richtigen Zahl', /2 gemerkte Erfassungen/.test(gesagt), gesagt);
  pruefe('und nichts wurde geloescht',
         umgebung(antwort200, geteilt, '2').window.__hallenbuch.lagerLesen().length === 2);
}

/* --- 5d. Altbestand ohne Zugang geht an den naechsten Anmelder --- */
{
  const geteilt = new Map();
  geteilt.set('hallenbuch.warteschlange',
              JSON.stringify([{ vorgang: 'alt-1', fin: 'ALT' }]));
  const u = umgebung(() => antwort(200, { ok: true }), geteilt, '2');
  const h = u.window.__hallenbuch;
  h.altbestandUebernehmen();
  pruefe('der Altbestand liegt jetzt beim angemeldeten Zugang',
         h.lagerLesen().length === 1);
  pruefe('und der alte Schluessel ist weg',
         geteilt.get('hallenbuch.warteschlange') === undefined);
  pruefe('er gilt nicht als fremd', h.fremdeSchlangen().length === 0);
}

/* --- 6. Kein Netz: bleibt liegen --- */
{
  const u = umgebung(() => Promise.reject(new Error('offline')));
  const h = u.window.__hallenbuch;
  h.lagerAnhaengen({ vorgang: 'offline-1', fin: 'O' });
  await h.nachladen();
  pruefe('ohne Netz bleibt alles gemerkt', h.lagerLesen().length === 1);
}

/* --- 7. Zwei gleichzeitige Durchgaenge arbeiten nicht doppelt --- */
{
  let aufrufe = 0;
  let freigeben;
  const warten = new Promise(r => { freigeben = r; });
  const u = umgebung(() => { aufrufe++; return warten.then(() => antwort(200, { ok: true })); });
  const h = u.window.__hallenbuch;
  h.lagerAnhaengen({ vorgang: 'x', fin: 'X' });
  const a = h.nachladen();
  const b = h.nachladen();
  freigeben();
  await Promise.all([a, b]);
  pruefe('der zweite Durchgang laeuft nicht parallel mit', aufrufe === 1,
         'Aufrufe: ' + aufrufe);
}

/* --- 8. Quittung: Erfolg blendet sich aus, Fehler bleibt stehen --- */
{
  const u = umgebung(() => antwort(200, { ok: true }));
  const h = u.window.__hallenbuch;

  h.melden('alles gut', false);
  pruefe('Erfolg wird angezeigt', u.__quittung.hidden === false);
  pruefe('Erfolg plant ein Ausblenden', u.__uhren.filter(Boolean).length === 1);
  u.__uhren.filter(Boolean)[0].fn();
  pruefe('und blendet sich dann aus', u.__quittung.hidden === true);

  u.__uhren.length = 0;
  h.melden('geht nicht', true);
  pruefe('Fehler wird angezeigt', u.__quittung.hidden === false);
  pruefe('Fehler blendet sich NICHT aus', u.__uhren.filter(Boolean).length === 0);
  pruefe('Fehler ist als solcher gekennzeichnet',
         /schlecht/.test(u.__quittung.className));
  pruefe('das Handy ruettelt', u.__geruettelt === true);
}

/* --- 9. Ein Rüttler, der wirft, darf die Erfassung nicht abbrechen --- */
{
  const u = umgebung(() => antwort(200, { ok: true }));
  u.navigator.vibrate = () => { throw new Error('ohne Nutzergeste'); };
  let geplatzt = false;
  try { u.window.__hallenbuch.melden('trotzdem', false); }
  catch (e) { geplatzt = true; }
  pruefe('ein werfendes navigator.vibrate bricht nichts ab', geplatzt === false);
  pruefe('die Meldung steht trotzdem da', u.__quittung.hidden === false);
}

/* --- 10. Meldungen werden als Text gesetzt, nicht als HTML --- */
{
  const u = umgebung(() => antwort(200, { ok: true }));
  u.window.__hallenbuch.melden('<img src=x onerror=boom>', true);
  const inhalt = u.__quittung.children;
  const roh = inhalt.map(c => (c.text !== undefined ? c.text : c.textContent)).join('');
  pruefe('der Text landet unveraendert als Text, nicht als Markup',
         roh.includes('<img src=x onerror=boom>'), roh);
  pruefe('es entsteht kein zusaetzlicher Knoten aus dem Markup',
         inhalt.filter(c => c.text === undefined).length === 1, 
         'Knoten: ' + inhalt.length);
}

/* --- 9. Das frisch hochgeladene Schadenbild ist gleich markiert --- */
{
  const u = umgebung(() => antwort(200, { ok: true }));
  const h = u.window.__hallenbuch;
  const schaden = h.fotoKnoten('/fotos/a.jpg', 'Schaden', 'schaden');
  const normal = h.fotoKnoten('/fotos/b.jpg', 'Aussen', 'aussen');
  pruefe('das Schadenbild traegt die Markierung', schaden.className === 'schaden',
         schaden.className);
  pruefe('und einen Titel', schaden.title === 'Schaden');
  pruefe('ein gewoehnliches Bild traegt sie nicht', normal.className === '');
  pruefe('beide haengen ihr Bild ein', schaden.children.length === 1
         && normal.children.length === 1);
  pruefe('die Adresse steht drin', schaden.href === '/fotos/a.jpg');
}

/* --- 10. Abbrechen haelt auch die Suchschleife an --- */
{
  const u = umgebung(() => antwort(200, { ok: true }));
  const gestoppt = [];
  const strom = { getTracks: () => [{ stop: () => gestoppt.push(1) }] };
  u.navigator.mediaDevices = { getUserMedia: () => Promise.resolve(strom) };
  u.window.BarcodeDetector = function () { this.detect = () => Promise.resolve([]); };
  const knopf = knoten();
  u.window.__hallenbuch.scannen(knopf, knoten());
  await new Promise((r) => setTimeout(r, 0));

  const laufende = u.__takte.filter(Boolean).length;
  pruefe('die Suchschleife laeuft', laufende === 1, 'Takte: ' + laufende);

  // Der Abbrechen-Knopf ist der zuletzt angehaengte Knoten.
  const abbrechen = u.document.body.children[u.document.body.children.length - 1];
  pruefe('es gibt einen Abbrechen-Knopf', abbrechen && abbrechen.textContent === 'Abbrechen');
  abbrechen.__hoerer.click();

  pruefe('die Kamera ist aus', gestoppt.length === 1);
  pruefe('und die Suchschleife steht', u.__takte.filter(Boolean).length === 0,
         'noch laufend: ' + u.__takte.filter(Boolean).length);
}

console.log(fehler ? '\n' + fehler + ' Pruefung(en) fehlgeschlagen'
                   : '\nalle Pruefungen bestanden');
process.exit(fehler ? 1 : 0);

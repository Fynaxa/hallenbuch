/* Haelt die Oberflaeche offline bereit.

   WICHTIG, und beim ersten Anlauf falsch gemacht: NICHT zuerst den
   Zwischenspeicher fragen. Wer das tut, liefert nach einer Aenderung am Aussehen
   ewig die alte Fassung aus, und niemand merkt es. Also: erst das Netz, und der
   Zwischenspeicher ist nur der Notnagel, wenn kein Netz da ist.

   Daten gehen nie ueber den Zwischenspeicher, dafuer ist die Warteschlange in
   app.js zustaendig. */
var LAGER = "hallenbuch-v2";

self.addEventListener("install", function (e) {
  e.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (namen) {
    return Promise.all(namen.filter(function (n) { return n !== LAGER; })
      .map(function (n) { return caches.delete(n); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener("fetch", function (e) {
  var ziel = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (!ziel.pathname.startsWith("/statisch/")) return;
  e.respondWith(
    fetch(e.request).then(function (antwort) {
      if (antwort && antwort.ok) {
        var kopie = antwort.clone();
        caches.open(LAGER).then(function (c) { c.put(e.request, kopie); });
      }
      return antwort;
    }).catch(function () {
      return caches.match(e.request, { ignoreSearch: true });
    })
  );
});

/* Service worker de la App Mayorista (se sirve en /mayorista/sw.js con alcance /mayorista).
 *
 * Estrategia: red primero. Los datos (precios, inventario, pedidos) siempre se piden
 * al servidor; la caché solo guarda la página "sin conexión" y los íconos.
 */
'use strict';

var CACHE = 'mayorista-v2';
var OFFLINE_URL = '/mayorista/offline';
var PRECACHE = [OFFLINE_URL, '/mayorista/icon/192.png', '/mayorista/icon/512.png'];

self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(CACHE)
            .then(function (cache) { return cache.addAll(PRECACHE); })
            .then(function () { return self.skipWaiting(); })
    );
});

self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(keys.filter(function (key) {
                return key.indexOf('mayorista-') === 0 && key !== CACHE;
            }).map(function (key) { return caches.delete(key); }));
        }).then(function () { return self.clients.claim(); })
    );
});

self.addEventListener('fetch', function (event) {
    var request = event.request;
    if (request.method !== 'GET') {
        return;
    }
    // Navegación entre pantallas: red; si no hay conexión, página sin conexión.
    if (request.mode === 'navigate') {
        event.respondWith(fetch(request).catch(function () {
            return caches.match(OFFLINE_URL);
        }));
        return;
    }
    var url = new URL(request.url);
    if (url.pathname.indexOf('/mayorista/icon/') === 0) {
        event.respondWith(caches.match(request).then(function (cached) {
            return cached || fetch(request);
        }));
    }
});

/* SAT-Maule Service Worker: modo offline + Web Push en segundo plano.
 *
 * Estrategias de caché:
 *  - App shell (HTML, JS, CSS, íconos, Leaflet): se precarga al instalar; cache-first.
 *  - /api/map y /api/zones (zonas, puntos seguros): network-first con respaldo en caché.
 *    Las respuestas desde caché llevan la cabecera X-SAT-Offline: 1.
 *  - Teselas del mapa: stale-while-revalidate, máximo MAX_TILES (sólo lo ya visitado,
 *    sin descargas masivas, según la política de uso de los proveedores de teselas).
 *  - Resto de /api/*: siempre red (un diagnóstico viejo no debe parecer actual).
 */

const VERSION = 'v2.11.0';
const STATIC_CACHE = `sat-maule-static-${VERSION}`;
const DATA_CACHE = 'sat-maule-data';
const TILE_CACHE = 'sat-maule-tiles';
const PREFS_CACHE = 'sat-prefs';
const MAX_TILES = 400;

const APP_SHELL = [
  '/',
  '/index.html',
  '/styles.css',
  '/app.js',
  '/js/api.js',
  '/js/config.js',
  '/js/geo.js',
  '/js/map.js',
  '/js/push.js',
  '/js/storage.js',
  '/js/ui.js',
  '/manifest.json',
  '/vendor/leaflet/leaflet.js',
  '/vendor/leaflet/leaflet.css',
  '/vendor/fonts/atkinson-400.woff2',
  '/vendor/fonts/atkinson-700.woff2',
  '/icons/icon.svg',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/badge-72.png',
];
const OFFLINE_DATA = ['/api/map', '/api/zones'];

const VIBRATION = {
  ROJO: [500, 200, 500, 200, 500, 600, 1000, 200, 1000],
  NARANJA: [400, 200, 400, 200, 400],
  AMARILLO: [200, 100, 200],
  VERDE: [100],
};

// ---------------------------------------------------------------- ciclo de vida

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(STATIC_CACHE);
      // cache: 'reload' salta la caché HTTP del navegador: si no, una versión nueva
      // podía guardar el CSS viejo y mezclarlo con el HTML nuevo.
      await cache.addAll(APP_SHELL.map((path) => new Request(path, { cache: 'reload' })));
      // Zonas seguras disponibles offline desde el primer uso (si el servidor responde).
      const data = await caches.open(DATA_CACHE);
      await Promise.allSettled(
        OFFLINE_DATA.map(async (path) => {
          const response = await fetch(path);
          if (response.ok) await data.put(path, response);
        }),
      );
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((key) => key.startsWith('sat-maule-static-') && key !== STATIC_CACHE).map((key) => caches.delete(key)),
      );
      await self.clients.claim();
    })(),
  );
});

// ---------------------------------------------------------------- fetch

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  const sameOrigin = url.origin === self.location.origin;

  if (sameOrigin && OFFLINE_DATA.includes(url.pathname)) {
    event.respondWith(networkFirst(request, url.pathname));
  } else if (sameOrigin && url.pathname.startsWith('/api/')) {
    return; // red directa
  } else if (url.hostname === 'tile.openstreetmap.org') {
    event.respondWith(staleWhileRevalidate(event, request));
  } else if (request.mode === 'navigate') {
    event.respondWith(navigation(request));
  } else if (sameOrigin) {
    event.respondWith(cacheFirst(request));
  }
});

async function networkFirst(request, cacheKey) {
  const cache = await caches.open(DATA_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(cacheKey, response.clone());
    return response;
  } catch {
    const cached = await cache.match(cacheKey);
    if (cached) return withHeader(cached, 'X-SAT-Offline', '1');
    return new Response(JSON.stringify({ error: 'offline', detail: 'Sin conexión y sin datos guardados.' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}

async function navigation(request) {
  try {
    return await fetch(request);
  } catch {
    const cache = await caches.open(STATIC_CACHE);
    return (await cache.match('/index.html')) ?? (await cache.match('/')) ?? Response.error();
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(request, { ignoreSearch: true });
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) await cache.put(request, response.clone());
  return response;
}

async function staleWhileRevalidate(event, request) {
  const cache = await caches.open(TILE_CACHE);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then(async (response) => {
      if (response.ok) {
        await cache.put(request, response.clone());
        await trimCache(cache, MAX_TILES);
      }
      return response;
    })
    .catch(() => cached ?? Response.error());
  if (cached) {
    event.waitUntil(network);
    return cached;
  }
  return network;
}

async function trimCache(cache, max) {
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - max; i += 1) await cache.delete(keys[i]);
}

function withHeader(response, name, value) {
  const headers = new Headers(response.headers);
  headers.set(name, value);
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

// ---------------------------------------------------------------- Web Push

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: 'SAT-Chile', body: event.data ? event.data.text() : '' };
  }
  const level = data.level ?? 'AMARILLO';
  const urgent = level === 'ROJO' || level === 'NARANJA';
  const options = {
    body: data.body ?? 'Abra SAT-Chile para ver las instrucciones.',
    icon: '/icons/icon-192.png',
    badge: '/icons/badge-72.png',
    tag: data.tag ?? 'sat-maule',
    renotify: true, // vuelve a sonar/vibrar aunque reemplace una notificación de la misma zona
    requireInteraction: urgent, // no desaparece sola en alertas graves
    vibrate: VIBRATION[level] ?? VIBRATION.AMARILLO,
    timestamp: Date.now(),
    data: { url: data.url ?? '/', level },
    actions: [{ action: 'open', title: 'Ver qué hacer' }],
  };
  event.waitUntil(
    Promise.all([
      self.registration.showNotification(data.title ?? 'SAT-Chile', options),
      notifyOpenClients({ type: 'SAT_ALERT', payload: data }),
    ]),
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url ?? '/', self.location.origin).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
      const existing = windows.find((client) => new URL(client.url).origin === self.location.origin);
      if (existing) {
        await existing.focus();
        if ('navigate' in existing) await existing.navigate(target).catch(() => undefined);
        return;
      }
      await self.clients.openWindow(target);
    })(),
  );
});

// El navegador renovó la suscripción: se registra la nueva en el servidor sin intervención del usuario.
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil(
    (async () => {
      const keyResponse = await fetch('/api/push/public-key');
      if (!keyResponse.ok) return;
      const { public_key: publicKey } = await keyResponse.json();
      const subscription = await self.registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
      const prefsResponse = await (await caches.open(PREFS_CACHE)).match('/__sat_prefs');
      const prefs = prefsResponse ? await prefsResponse.json() : {};
      await fetch('/api/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subscription: subscription.toJSON(),
          location: prefs.location ?? null,
          min_risk_level: prefs.min_risk_level ?? 'AMARILLO',
        }),
      });
    })(),
  );
});

async function notifyOpenClients(message) {
  const windows = await self.clients.matchAll({ type: 'window' });
  for (const client of windows) client.postMessage(message);
}

function urlBase64ToUint8Array(base64) {
  const padding = '='.repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

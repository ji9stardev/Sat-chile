// Suscripción Web Push (VAPID) y detección de plataforma.
import { api } from './api.js';

const PREFS_CACHE = 'sat-prefs';
const PREFS_URL = '/__sat_prefs';

export const isPushSupported = () =>
  'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

export const isIOS = () =>
  /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

export const isStandalone = () =>
  window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

/**
 * Pide permiso de notificaciones. Debe llamarse directamente desde el toque del usuario
 * (Safari rechaza la solicitud si llega después de otras esperas).
 * @returns {Promise<'granted'|'denied'|'default'|'unsupported'>}
 */
export function requestNotificationPermission() {
  if (!isPushSupported()) return Promise.resolve('unsupported');
  if (Notification.permission !== 'default') return Promise.resolve(Notification.permission);
  return Notification.requestPermission();
}

function urlBase64ToUint8Array(base64) {
  const padding = '='.repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

function sameKey(current, expected) {
  if (!current) return false;
  const a = new Uint8Array(current);
  return a.length === expected.length && a.every((v, i) => v === expected[i]);
}

/** Guarda preferencias donde el Service Worker pueda leerlas (no tiene acceso a localStorage). */
async function savePrefsForServiceWorker(prefs) {
  if (!('caches' in window)) return;
  const cache = await caches.open(PREFS_CACHE);
  await cache.put(PREFS_URL, new Response(JSON.stringify(prefs), { headers: { 'Content-Type': 'application/json' } }));
}

/**
 * Suscribe este dispositivo y registra su ubicación base en el servidor.
 * @returns {Promise<{ok: boolean, reason?: string}>}
 */
export async function enablePush(position, permission) {
  if (!isPushSupported()) {
    return { ok: false, reason: isIOS() && !isStandalone() ? 'ios-install' : 'unsupported' };
  }
  if (permission !== 'granted') return { ok: false, reason: permission === 'denied' ? 'denied' : 'dismissed' };

  const registration = await navigator.serviceWorker.ready;
  const { data } = await api.publicKey();
  const key = urlBase64ToUint8Array(data.public_key);

  let subscription = await registration.pushManager.getSubscription();
  if (subscription && !sameKey(subscription.options.applicationServerKey, key)) {
    await subscription.unsubscribe(); // el servidor cambió sus claves VAPID
    subscription = null;
  }
  subscription ??= await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });

  const location = position ? { lat: Number(position.lat.toFixed(5)), lon: Number(position.lon.toFixed(5)) } : null;
  await api.subscribe({ subscription: subscription.toJSON(), location, min_risk_level: 'AMARILLO' });
  await savePrefsForServiceWorker({ location, min_risk_level: 'AMARILLO' });
  return { ok: true };
}

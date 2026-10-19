// Persistencia local tolerante a fallos (modo privado, almacenamiento lleno o bloqueado).

const PREFIX = 'sat-maule.';

export const KEYS = Object.freeze({
  lastDiagnosis: 'lastDiagnosis',
  protection: 'protection',
  deviceId: 'deviceId',
  zones: 'zones',
  mapLayers: 'mapLayers',
  reportQueue: 'reportQueue',
  manualPlace: 'manualPlace',
  regionOverview: 'regionOverview',
  seenOfficial: 'seenOfficial',
});

export function load(key, fallback = null) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function save(key, value) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    // Sin almacenamiento la app sigue funcionando; sólo pierde la caché local.
  }
}

/** Identificador aleatorio del dispositivo (el servidor lo anonimiza con HMAC). */
export function deviceId() {
  let id = load(KEYS.deviceId);
  if (typeof id !== 'string' || id.length < 16) {
    id = crypto.randomUUID
      ? crypto.randomUUID()
      : Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('');
    save(KEYS.deviceId, id);
  }
  return id;
}

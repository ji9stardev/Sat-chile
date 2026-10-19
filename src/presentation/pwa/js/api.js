// Cliente de la API FastAPI (mismo origen). Todas las llamadas tienen timeout y errores en español.

export class ApiError extends Error {
  /** @param {number} status 0 = sin conexión / timeout */
  constructor(status, message) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }

  get isNetwork() {
    return this.status === 0;
  }
}

function detailOf(data) {
  if (!data) return null;
  if (typeof data.detail === 'string') return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg ?? String(d)).join('; ');
  return null;
}

async function request(path, { method = 'GET', body, timeoutMs = 15000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      throw new ApiError(response.status, detailOf(data) ?? `El servidor respondió con error ${response.status}.`);
    }
    // El Service Worker marca con esta cabecera las respuestas servidas desde la caché offline.
    return { data, offline: response.headers.get('X-SAT-Offline') === '1' };
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error.name === 'AbortError') throw new ApiError(0, 'El servidor tardó demasiado en responder.');
    throw new ApiError(0, 'Sin conexión con el servidor.');
  } finally {
    clearTimeout(timer);
  }
}

const coord = (value) => Number(value).toFixed(5);

export const api = {
  // Timeout amplio: un hosting gratuito "dormido" puede tardar ~1 minuto en despertar.
  gpsCheck: (lat, lon) => request(`/api/gps-check?lat=${coord(lat)}&lon=${coord(lon)}`, { timeoutMs: 70000 }),
  regionOverview: (code) => request(`/api/region-overview?region=${encodeURIComponent(code)}`, { timeoutMs: 30000 }),
  refuges: (lat, lon, radiusKm = 10) =>
    request(`/api/refuges?lat=${coord(lat)}&lon=${coord(lon)}&radius_km=${radiusKm}`, { timeoutMs: 15000 }),
  windyStudy: (lat, lon) => request(`/api/windy-study?lat=${coord(lat)}&lon=${coord(lon)}`, { timeoutMs: 20000 }),
  places: (query) => request(`/api/places?q=${encodeURIComponent(query)}`, { timeoutMs: 10000 }),
  mapLayers: () => request('/api/map'),
  publicKey: () => request('/api/push/public-key'),
  subscribe: (payload) => request('/api/subscribe', { method: 'POST', body: payload }),
  reportWaterLevel: (payload) => request('/api/report-water-level', { method: 'POST', body: payload }),
};

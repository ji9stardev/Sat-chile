// Mapa Leaflet (zonas, cauces, puntos seguros, usuario) y radar Windy bajo demanda.
import { CHILE_CENTER, LEVELS, WATERCOURSE_KINDS } from './config.js';

const SAFE_COLOR = '#1E7F4F';
const USER_COLOR = '#1F4E8C';

let map = null;
let groups = null;
let userLayer = null;
let radarKey = null;
// La primera vez se encuadra al usuario; después sólo cuando él lo pide (no en refrescos de fondo).
let centeredOnUser = false;
let fittedNearby = false;

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export function directionsUrl(lat, lon) {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}&travelmode=walking`;
}

export function initMap(container) {
  if (map) return map;
  if (!window.L) {
    container.replaceChildren(el('p', 'No se pudo cargar el mapa.', 'muted'));
    return null;
  }
  map = L.map(container, { preferCanvas: true, zoomControl: true }).setView(CHILE_CENTER, 8);
  // Teselas estándar de OpenStreetMap (sin clave).
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    crossOrigin: true, // respuesta CORS (no opaca): el Service Worker la guarda para usar sin señal
    className: 'map-tiles',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  groups = {
    zones: L.layerGroup().addTo(map),
    rivers: L.layerGroup().addTo(map),
    safe: L.layerGroup().addTo(map),
    refuges: L.layerGroup().addTo(map),
  };
  return map;
}

function safePointPopup(point) {
  const box = el('div');
  box.append(el('strong', point.name));
  box.append(el('div', `${point.commune} · ${point.available_capacity} de ${point.capacity} cupos`));
  if (!point.is_operational) box.append(el('div', 'No operativo en este momento'));
  const link = el('a', 'Cómo llegar a pie');
  link.href = directionsUrl(point.lat, point.lon);
  link.target = '_blank';
  link.rel = 'noopener';
  box.append(link);
  return box;
}

export function renderLayers(layers) {
  if (!map || !layers) return;
  groups.zones.clearLayers();
  groups.rivers.clearLayers();
  groups.safe.clearLayers();

  for (const zone of layers.zones ?? []) {
    const color = LEVELS[zone.risk_level]?.color ?? LEVELS.SIN_DATOS.color;
    L.polygon(zone.ring, { color, weight: 2, fillColor: color, fillOpacity: 0.18 })
      .bindTooltip(`${zone.name}: ${zone.risk_level}`, { sticky: true })
      .addTo(groups.zones);
  }
  for (const river of layers.watercourses ?? []) {
    const kind = WATERCOURSE_KINDS[river.kind] ?? WATERCOURSE_KINDS.ESTERO;
    L.polyline(river.path, { color: kind.color, weight: kind.weight, opacity: 0.9, dashArray: kind.dash })
      .bindTooltip(`${river.name} · ${kind.label}`, { sticky: true })
      .addTo(groups.rivers);
  }
  for (const point of layers.safe_points ?? []) {
    L.circleMarker([point.lat, point.lon], {
      radius: 10,
      color: '#ffffff',
      weight: 2,
      fillColor: point.is_operational ? SAFE_COLOR : '#6b7280',
      fillOpacity: 1,
    })
      .bindPopup(safePointPopup(point))
      .addTo(groups.safe);
  }
}

const REFUGE_STYLE = Object.freeze({
  PUNTO_ENCUENTRO: { color: '#0E7490', label: 'Punto de encuentro' },
  ALBERGUE: { color: '#7C3AED', label: 'Albergue' },
});

function refugePopup(refuge) {
  const style = REFUGE_STYLE[refuge.kind] ?? REFUGE_STYLE.ALBERGUE;
  const box = el('div');
  box.append(el('strong', refuge.name));
  box.append(el('div', refuge.detail ? `${style.label} · ${refuge.detail}` : style.label));
  const link = el('a', 'Cómo llegar a pie');
  link.href = directionsUrl(refuge.latitude, refuge.longitude);
  link.target = '_blank';
  link.rel = 'noopener';
  box.append(link);
  return box;
}

/** Dibuja refugios y puntos de encuentro (reemplaza los anteriores). Con ``fitAround`` encuadra ese
 * punto y el refugio más cercano (útil cuando el más cercano está lejos y quedaría fuera de la vista). */
export function showRefuges(refuges, { fitAround = null } = {}) {
  if (!map) return;
  groups.refuges.clearLayers();
  for (const refuge of refuges ?? []) {
    const style = REFUGE_STYLE[refuge.kind] ?? REFUGE_STYLE.ALBERGUE;
    // Cuadrados, para distinguirlos de los puntos seguros oficiales (círculos verdes).
    L.marker([refuge.latitude, refuge.longitude], {
      icon: L.divIcon({
        className: 'refuge-icon',
        html: `<span style="display:block;width:14px;height:14px;border-radius:3px;border:2px solid #fff;background:${style.color}"></span>`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      }),
      keyboard: false,
    })
      .bindPopup(refugePopup(refuge))
      .addTo(groups.refuges);
  }
  const nearest = refuges?.[0];
  if (fitAround && nearest) {
    map.fitBounds(L.latLngBounds([[fitAround.lat, fitAround.lon], [nearest.latitude, nearest.longitude]]), {
      padding: [40, 40],
      maxZoom: 15,
    });
  }
}

/** Centro actual del mapa (para buscar en la zona que el usuario está mirando). */
export function mapCenter() {
  if (!map) return null;
  const center = map.getCenter();
  return { lat: center.lat, lon: center.lng };
}

export function showUser(position, { recenter = true } = {}) {
  if (!map || !position) return;
  userLayer?.remove();
  userLayer = L.layerGroup([
    L.circle([position.lat, position.lon], {
      radius: Math.min(position.accuracy ?? 50, 500),
      color: USER_COLOR,
      weight: 1,
      fillOpacity: 0.12,
    }),
    L.circleMarker([position.lat, position.lon], {
      radius: 9,
      color: '#ffffff',
      weight: 3,
      fillColor: USER_COLOR,
      fillOpacity: 1,
    }).bindTooltip('Usted está aquí'),
  ]).addTo(map);
  if (recenter || !centeredOnUser) map.setView([position.lat, position.lon], 13);
  centeredOnUser = true;
}

let nearbyLayer = null;

/** ¿Es este tramo el cauce que gobierna el riesgo? (nombre y distancia; hay muchos "sin nombre"). */
export function isGoverning(item, governing) {
  return Boolean(governing) && item.name === governing.name && Math.abs(item.distance_km - governing.distance_km) < 0.005;
}

/** Dibuja los cauces cercanos al usuario y encuadra el mapa en un radio de ~1,5 km. */
export function showNearby(nearby, position, governingCourse, { fit = true } = {}) {
  if (!map) return;
  nearbyLayer?.remove();
  nearbyLayer = L.layerGroup();
  for (const item of nearby ?? []) {
    const kind = WATERCOURSE_KINDS[item.kind] ?? WATERCOURSE_KINDS.ESTERO;
    const governing = isGoverning(item, governingCourse);
    L.polyline(item.path, {
      color: kind.color,
      weight: governing ? kind.weight + 3 : kind.weight,
      opacity: 0.95,
      dashArray: kind.dash,
    })
      .bindTooltip(`${item.name} · ${kind.label} · ${item.distance_km < 1 ? Math.round(item.distance_km * 1000) + ' m' : item.distance_km.toFixed(1) + ' km'}`, { sticky: true })
      .addTo(nearbyLayer);
  }
  nearbyLayer.addTo(map);
  if (userLayer) {
    userLayer.remove(); // re-agregar para que el marcador quede sobre los cauces
    userLayer.addTo(map);
  }
  if (position && (fit || !fittedNearby)) {
    map.fitBounds(L.latLng(position.lat, position.lon).toBounds(3000));
    fittedNearby = true;
  }
}

/** Arma un único toque en el mapa para elegir una ubicación. Devuelve false si no hay mapa. */
let pickHandler = null;

export function pickOnMap(onPick) {
  if (!map) return false;
  cancelPick();
  map.getContainer().classList.add('picking');
  pickHandler = (event) => {
    cancelPick();
    onPick({ lat: event.latlng.lat, lon: event.latlng.lng });
  };
  map.on('click', pickHandler);
  return true;
}

/** Desarma «elegir en el mapa» (p. ej. si el usuario se fue a otra pestaña). */
export function cancelPick() {
  if (!map || !pickHandler) return;
  map.off('click', pickHandler);
  pickHandler = null;
  map.getContainer().classList.remove('picking');
}

export function refreshSize() {
  map?.invalidateSize();
}

/** Inserta el mapa de Windy sólo cuando el usuario lo pide (ahorra datos móviles). */
export function loadRadar(container, position, overlay = 'rain') {
  const key = `${position?.lat ?? ''},${position?.lon ?? ''},${overlay}`;
  if (radarKey === key) return;
  if (!navigator.onLine) {
    container.replaceChildren(el('p', 'El radar requiere conexión a internet.', 'muted'));
    return;
  }
  const [lat, lon] = position ? [position.lat, position.lon] : CHILE_CENTER;
  const params = new URLSearchParams({
    type: 'map',
    location: 'coordinates',
    metricRain: 'mm',
    metricTemp: '°C',
    metricWind: 'km/h',
    zoom: position ? '9' : '7',
    overlay,
    product: 'ecmwf',
    level: 'surface',
    lat: lat.toFixed(3),
    lon: lon.toFixed(3),
    marker: position ? 'true' : '',
    detail: position ? 'true' : '',
    detailLat: lat.toFixed(3),
    detailLon: lon.toFixed(3),
    message: 'true',
  });
  const iframe = document.createElement('iframe');
  iframe.src = `https://embed.windy.com/embed.html?${params}`;
  iframe.title = 'Mapa y pronóstico de Windy para su ubicación';
  iframe.loading = 'lazy';
  iframe.referrerPolicy = 'strict-origin-when-cross-origin';
  container.replaceChildren(iframe);
  radarKey = key;
}

// Renderizado de la interfaz. Sólo textContent: ningún dato del servidor se inserta como HTML.
import {
  CALM_STEPS,
  FLOW_TRENDS,
  LEVEL_ORDER,
  LEVELS,
  MISSING_SOURCE_LABELS,
  MODEL_NAMES,
  NO_DATA,
  PHONES,
  ROUTE_HINTS,
  THREAT_LABELS,
  THREAT_TEXT,
  VIBRATION,
  WALK_KMH,
  WATERCOURSE_KINDS,
  WIND_GUST_LEVELS,
  SERVICE_KINDS,
  HAIL_SIZES,
} from './config.js';
import { directionsUrl, isGoverning } from './map.js';
import { KEYS, load, save } from './storage.js';

const $ = (id) => document.getElementById(id);

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

const timeFmt = new Intl.DateTimeFormat('es-CL', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const dateTimeFmt = new Intl.DateTimeFormat('es-CL', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const num = (value, digits = 0) => Number(value).toLocaleString('es-CL', { maximumFractionDigits: digits });
const mm = (value) => `${num(value, value < 10 ? 1 : 0)} mm`;
const meters = (km) => (km < 1 ? `${Math.round(km * 1000)} m` : `${num(km, 1)} km`);
const walkMinutes = (km) => Math.max(1, Math.round((km / WALK_KMH) * 60));
const severity = (level) => LEVEL_ORDER.indexOf(level);

const hourFmt = new Intl.DateTimeFormat('es-CL', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const dayFmt = new Intl.DateTimeFormat('es-CL', { weekday: 'long', day: 'numeric' });

/** "Hoy de 16:00 a 20:00", "Mañana de …" o "sábado 19 de …". */
export function formatWindow(startIso, endIso) {
  const start = new Date(startIso);
  const today = new Date();
  const tomorrow = new Date(today.getTime() + 86400000);
  const day = start.toDateString() === today.toDateString()
    ? 'Hoy'
    : start.toDateString() === tomorrow.toDateString()
      ? 'Mañana'
      : dayFmt.format(start).replace(/^./, (c) => c.toUpperCase());
  return `${day} de ${hourFmt.format(start)} a ${hourFmt.format(new Date(endIso))}`;
}

export function formatTime(iso) {
  const date = new Date(iso);
  const sameDay = date.toDateString() === new Date().toDateString();
  return (sameDay ? timeFmt : dateTimeFmt).format(date);
}

// ---------------------------------------------------------------- lectura del diagnóstico

/** Amenaza que explica el nivel personal: la que alcanza ese nivel, priorizando aluvión. */
export function mainThreat(d) {
  if (!d || d.personal_risk_level === 'VERDE') return 'NONE';
  const level = d.personal_risk_level;
  const official = officialDriver(d);
  if (official) {
    const h = official.hazard.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    if (h.includes('remocion') || h.includes('aluvion')) return nearestOf(d, 'QUEBRADA') ? 'ALUVION' : 'DESLIZAMIENTO';
    if (h.includes('tormenta')) return 'TORMENTA';
    return 'DESBORDE';
  }
  if (d.debris_flow?.level === level) return 'ALUVION';
  if (d.landslide?.level === level) return 'DESLIZAMIENTO';
  if (d.consensus?.outcome === 'AVISO_TORMENTA_ELECTRICA' && level === 'AMARILLO') return 'TORMENTA';
  const alert = d.active_alerts?.[0];
  if (alert?.threat_type === 'REMOCION_EN_MASA') return 'ALUVION';
  if (alert?.threat_type === 'TORMENTA_ELECTRICA') return 'TORMENTA';
  return 'DESBORDE';
}

/** La alerta oficial de su comuna que fija el nivel actual (si el cálculo propio es menor). */
function officialDriver(d) {
  const map = { AMARILLA: 'AMARILLO', ROJA: 'ROJO' };
  return (d.official_alerts ?? []).find((a) => a.sets_level && map[a.level] === d.personal_risk_level) ?? null;
}

/** Alerta amarilla o roja de lluvia/crecida/aluvión para toda la región: se informa, sin cambiar el nivel de su punto. */
const regionalNotice = (d) =>
  (d.official_alerts ?? []).find((a) => a.hydromet && a.applies_to === 'TODA_REGION' && (a.level === 'AMARILLA' || a.level === 'ROJA')) ?? null;

// Con probabilidades (71 corridas) manda el ensemble: tormenta sólo si 30 % o más de las corridas
// traen inestabilidad *y* lluvia en la misma hora. Sin ensemble, se usa el pronóstico principal.
const STORM_ALERT_PCT = 30;
/** SENAPRED anuncia tormentas eléctricas para su comuna o región (en la amenaza o en el texto). */
const officialStorm = (d) => (d.official_alerts ?? []).find((a) => /tormenta/i.test(`${a.hazard} ${a.title} ${a.summary}`)) ?? null;

const stormLikely = (d) =>
  officialStorm(d) ? true
  : d.probabilities?.storm_pct != null
    ? d.probabilities.storm_pct >= STORM_ALERT_PCT
    : d.consensus?.outcome === 'AVISO_TORMENTA_ELECTRICA' || Boolean(d.storm_window);
const withPct = (text, pct) => (pct == null ? text : `${text} · probabilidad ${pct} %`);
const effectiveRain = (d) => Math.max(d.consensus?.weighted_precipitation_mm ?? 0, d.conditions?.recent_precipitation_mm ?? 0);
const nearestOf = (d, kind) => (d.nearby_watercourses ?? []).filter((c) => c.kind === kind).sort((a, b) => a.distance_km - b.distance_km)[0];
const slopeMatters = (d) => d.landslide && d.landslide.susceptibility !== 'BAJA';

function threatText(threat, level, d) {
  if (threat === 'NONE') {
    let title = 'No hay riesgo de crecida, aluvión ni deslizamiento en su ubicación ahora.';
    if (d.debris_flow?.susceptible) title = 'Hay una quebrada cerca, pero la lluvia no alcanza para un aluvión.';
    else if (slopeMatters(d)) title = 'Está en una ladera, pero la lluvia no alcanza para un deslizamiento.';
    return { word: 'Sin alerta', title, steps: CALM_STEPS };
  }
  const byLevel = THREAT_TEXT[threat] ?? THREAT_TEXT.DESBORDE;
  return byLevel[level] ?? byLevel.AMARILLO ?? THREAT_TEXT.DESBORDE[level];
}

// ---------------------------------------------------------------- tarjeta de nivel (Inicio)

let lastShownLevel = null;

function applyLevel(level) {
  const known = Boolean(LEVELS[level]) && level !== 'SIN_DATOS';
  document.body.dataset.level = known ? level : 'SIN_DATOS';
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', level === 'ROJO' ? LEVELS.ROJO.color : '#F6F5F1');
  if (lastShownLevel && level !== lastShownLevel && VIBRATION[level] && 'vibrate' in navigator) {
    navigator.vibrate(VIBRATION[level]);
  }
  lastShownLevel = level;
}

export function renderEmpty() {
  applyLevel('SIN_DATOS');
  $('hero').dataset.threat = 'SIN_DATOS';
  $('hero-threat').textContent = 'SIN DATOS';
  $('level-word').textContent = NO_DATA.word;
  $('level-title').textContent = NO_DATA.title;
  $('hero-facts').hidden = true;
  $('hero-meter').hidden = true;
  $('hero-updated').hidden = true;
}

function fact(label, value) {
  const box = el('div', null, 'fact');
  box.append(el('span', label), el('strong', value));
  return box;
}

function renderHero(d, { savedAt, stale }) {
  const level = d.personal_risk_level;
  const threat = mainThreat(d);
  const text = threatText(threat, level, d);
  applyLevel(level);

  $('hero').dataset.threat = threat;
  const official = officialDriver(d);
  $('hero-threat').textContent = threat === 'NONE'
    ? 'SIN ALERTA · VERDE'
    : official
      ? `SENAPRED · ${OFFICIAL_WORDS[official.level]}`
      : `${THREAT_LABELS[threat]} · NIVEL ${level}`;
  if (official) {
    const where = official.applies_to === 'COMUNA' ? `en ${d.area?.comuna ?? 'su comuna'}` : 'en toda la región';
    const word = (OFFICIAL_WORDS[official.level] ?? 'Alerta').toLowerCase();
    $('level-word').textContent = `${word.charAt(0).toUpperCase()}${word.slice(1)} ${where}`;
    $('level-title').textContent = `Por ${official.hazard.toLowerCase()}. Siga las indicaciones de SENAPRED y su municipio.`;
  } else {
    $('level-word').textContent = text.word;
    $('level-title').textContent = text.title;
    const regional = threat === 'NONE' ? regionalNotice(d) : null;
    if (regional) {
      const word = (OFFICIAL_WORDS[regional.level] ?? 'Alerta').toLowerCase();
      $('level-title').textContent =
        `${text.title} SENAPRED mantiene ${word} en toda la región por ${regional.hazard.toLowerCase()}: revise sus indicaciones.`;
    }
  }

  const facts = [];
  if (d.consensus) facts.push(fact('Lluvia 24 h', mm(d.consensus.weighted_precipitation_mm)));
  if (d.conditions) facts.push(fact('Isoterma 0°', `${num(d.conditions.freezing_level_m)} m`));
  const quebrada = nearestOf(d, 'QUEBRADA');
  if (quebrada) facts.push(fact('Quebrada', meters(quebrada.distance_km)));
  else if (slopeMatters(d)) facts.push(fact('Pendiente', `${num(d.landslide.slope_deg)}°`));
  else if (d.watercourse) facts.push(fact(WATERCOURSE_KINDS[d.watercourse.kind]?.label.split(' ')[0] ?? 'Cauce', meters(d.watercourse.distance_km)));
  $('hero-facts').replaceChildren(...facts);
  $('hero-facts').hidden = !facts.length;

  renderMeter(d);

  const updated = $('hero-updated');
  updated.hidden = false;
  updated.textContent = stale && savedAt
    ? `Último diagnóstico guardado: ${formatTime(savedAt)} (sin conexión)`
    : `Lluvia caída + pronóstico · actualizado ${formatTime(d.evaluated_at)}`;

}

/** Barra de "cuánto falta": sólo donde hay quebrada o ladera, que es donde aplica el umbral de lluvia. */
function renderMeter(d) {
  const meter = $('hero-meter');
  const t = d.rain_thresholds;
  // Con una alerta oficial al mando, «alerta amarilla si llega a 15 mm» contradiría a SENAPRED.
  if (officialDriver(d) || !t || !d.conditions || !d.consensus || !(d.debris_flow?.susceptible || slopeMatters(d))) {
    meter.hidden = true;
    return;
  }
  const rain = effectiveRain(d);
  // El nivel que se muestra puede ser menor que el de la lluvia sola (modelos en desacuerdo, pendiente media).
  const shown = [d.debris_flow?.level, d.landslide?.level].reduce((a, b) => (severity(b) > severity(a) ? b : a), 'VERDE');
  let text;
  if (d.conditions.freezing_level_m < t.min_freezing_level_m) text = 'Nieva en la cordillera: sin alerta aunque llueva';
  else if (rain >= t.amarillo_mm && shown === 'VERDE') {
    // Sólo la pendiente media rebaja por sí sola; en otro caso la causa es el desacuerdo entre modelos.
    const onlyMediumSlope = !d.debris_flow?.susceptible && d.landslide?.susceptibility === 'MEDIA';
    text = onlyMediumSlope
      ? `Supera ${num(t.amarillo_mm)} mm, pero la pendiente es moderada: sin alerta por ahora`
      : `Supera ${num(t.amarillo_mm)} mm, pero los modelos no coinciden: sin alerta por ahora`;
  } else if (rain < t.amarillo_mm) text = `Alerta amarilla si la lluvia llega a ${num(t.amarillo_mm)} mm`;
  else if (rain < t.naranja_mm) text = `Sube a naranja con ${num(t.naranja_mm)} mm`;
  else if (rain < t.rojo_mm) text = `Sube a rojo con ${num(t.rojo_mm)} mm`;
  else text = `Lluvia sobre ${num(t.rojo_mm)} mm: nivel máximo`;
  $('meter-fill').style.width = `${Math.min(rain / (t.rojo_mm * 1.2), 1) * 100}%`;
  $('meter-text').textContent = text;
  $('meter-bar').setAttribute('aria-label', `${mm(rain)} de lluvia. ${text}`);
  meter.hidden = false;
}

function probSummary(d) {
  const p = d.probabilities;
  if (!p) return null;
  const parts = [
    p.storm_pct >= 20 ? `tormenta ${p.storm_pct} %` : null,
    p.hail_pct >= 20 ? `granizo ${p.hail_pct} %` : null,
    p.intense_pct >= 20 ? `lluvia muy intensa ${p.intense_pct} %` : null,
    !p.snow && p.debris_pct[0] >= 20 ? `aluvión ${p.debris_pct[0]} %` : null,
  ].filter(Boolean);
  return parts.length ? `Probabilidad: ${parts.join(' · ')}` : null;
}

function renderHomeLinks(d) {
  const link = $('link-monitor');
  const storm = stormLikely(d);
  link.dataset.storm = String(storm);
  $('link-monitor-title').textContent = storm ? 'También: tormenta eléctrica' : 'Ver lluvia y tormentas';
  $('link-monitor-sub').textContent = storm
    ? d.storm_window ? `Posible ${formatWindow(d.storm_window.start, d.storm_window.end).toLowerCase()}` : 'Posible en las próximas 24 horas'
    : probSummary(d) ?? 'Pronóstico por hora y alertas';

  const urgent = severity(d.personal_risk_level) >= severity('NARANJA');
  $('cta-title').textContent = urgent ? 'Ver punto seguro cercano' : 'Ver mi ruta segura';
  const point = d.safe_points?.[0];
  $('cta-sub').textContent = point
    ? `Punto seguro · ${meters(point.distance_km)} · ${walkMinutes(point.distance_km)} min caminando`
    : 'Busque terreno alto, lejos de cauces y laderas';
}

// ---------------------------------------------------------------- alertas oficiales SENAPRED

const OFFICIAL_WORDS = Object.freeze({
  TEMPRANA_PREVENTIVA: 'ALERTA TEMPRANA PREVENTIVA',
  AMARILLA: 'ALERTA AMARILLA',
  ROJA: 'ALERTA ROJA',
});
/** Nivel de la app equivalente; la temprana preventiva informa pero no sube el nivel. */
const OFFICIAL_TO_LEVEL = Object.freeze({ AMARILLA: 'AMARILLO', ROJA: 'ROJO' });

const dateFmt = new Intl.DateTimeFormat('es-CL', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

/** Dónde rige una alerta oficial respecto de la comuna del usuario. */
function officialWhere(a, d) {
  const comuna = d.area?.comuna ?? 'su comuna';
  if (a.applies_to === 'COMUNA') return `Rige en ${comuna}`;
  if (a.applies_to === 'TODA_REGION') return `Rige en toda la región (incluye ${comuna})`;
  return `Para ${a.scope || 'parte de su región'}`;
}

function renderOfficial(d) {
  const alerts = d.official_alerts ?? [];
  $('official').hidden = !alerts.length;
  $('official-list').replaceChildren(
    ...alerts.map((a) => {
      const li = el('li', null, 'official-item');
      li.dataset.level = a.level;
      const where = officialWhere(a, d);
      li.append(
        el('span', OFFICIAL_WORDS[a.level] ?? a.level, 'official-badge'),
        el('strong', `${a.hazard} · ${where}`),
      );
      if (a.summary) li.append(el('p', a.summary));
      const meta = el('p');
      meta.append(`Actualizada ${dateFmt.format(new Date(a.published_at))} · `);
      const link = el('a', 'Ver en senapred.cl');
      link.href = a.url;
      link.target = '_blank';
      link.rel = 'noopener';
      meta.append(link);
      li.append(meta);
      return li;
    }),
  );
}

// ---------------------------------------------------------------- nombre del lugar

/** «Vilches · San Clemente»: localidad más cercana y comuna. Una comuna buscada se muestra tal cual. */
export function placeName(d, manual = null) {
  if (manual?.kind === 'comuna') return d.area?.comuna ?? manual.name;
  if (manual?.kind === 'localidad') return d.area?.comuna && d.area.comuna !== manual.name ? `${manual.name} · ${d.area.comuna}` : manual.name;
  const comuna = d.area?.comuna;
  const loc = d.locality;
  if (loc && comuna && loc.name !== comuna) return `${loc.near ? 'Cerca de ' : ''}${loc.name} · ${comuna}`;
  if (comuna) return comuna;
  if (loc) return `${loc.near ? 'Cerca de ' : ''}${loc.name}`;
  return d.within_chile === false ? 'Fuera de Chile: cobertura limitada' : 'Ubicación sin nombre';
}

// ---------------------------------------------------------------- diagnóstico completo

export function renderDiagnosis(d, { savedAt = null, stale = false, manual = null } = {}) {
  $('place').textContent = placeName(d, manual);
  $('manual-banner').hidden = !manual;
  if (manual) $('manual-name').textContent = placeName(d, manual);

  renderOfficial(d);
  renderHero(d, { savedAt, stale });
  renderHomeLinks(d);
  renderQuality(d, { stale });
  renderMonitor(d, { savedAt, stale });
  renderSafe(d);
  renderNearby(d);
  renderLocalServices(d);
}

// ---------------------------------------------------------------- emergencias cerca

/** Número marcable: deja dígitos y +, y agrega +56 a los números chilenos de 9 dígitos. */
export function dialable(phone) {
  const digits = String(phone).replace(/[^\d+]/g, '');
  if (digits.startsWith('+')) return digits;
  if (digits.startsWith('56') && digits.length === 11) return `+${digits}`;
  if (digits.length === 9) return `+56${digits}`;
  return digits;
}

let hasOfficialSafePoint = false;

export function renderRefuges(refuges) {
  $('refuges-block').hidden = !refuges?.length;
  const nearest = refuges?.[0];
  $('safe-points').closest('section').hidden = !hasOfficialSafePoint && Boolean(nearest);
  if (!hasOfficialSafePoint && nearest) {
    // Sin punto seguro oficial: se indica el refugio más cercano y su ruta.
    const kind = (SERVICE_KINDS[nearest.kind] ?? 'refugio').toLowerCase();
    $('safe-meta').textContent = nearest.distance_km > 5
      ? `El ${kind} registrado más cercano está a ${num(nearest.distance_km)} km. Ante una crecida no viaje hasta allá: suba a terreno alto y firme cerca de usted.`
      : `El más cercano es un ${kind} a ${meters(nearest.distance_km)} ` +
        `(${walkMinutes(nearest.distance_km)} min caminando). Si va hacia allá, busque terreno alto y firme.`;
    const route = $('safe-route');
    route.href = directionsUrl(nearest.latitude, nearest.longitude);
    route.setAttribute('aria-label', `Abrir la ruta a ${nearest.name}`);
    route.hidden = false;
  }
  $('refuges').replaceChildren(
    ...(refuges ?? []).slice(0, 8).map((r) => {
      const li = el('li', null, 'service');
      const text = el('span', null, 'service-text');
      const kind = SERVICE_KINDS[r.kind] ?? r.kind;
      const unnamed = r.name === kind;
      const parts = [unnamed ? 'Sin nombre' : kind, r.detail ? `para ${r.detail}` : null, meters(r.distance_km)];
      text.append(el('strong', r.name), el('span', parts.filter(Boolean).join(' · ')));
      const go = el('a', 'Ir', 'go');
      go.href = directionsUrl(r.latitude, r.longitude);
      go.target = '_blank';
      go.rel = 'noopener';
      go.setAttribute('aria-label', `Cómo llegar a ${r.name}`);
      li.append(text, go);
      return li;
    }),
  );
}

function renderLocalServices(d) {
  const services = d.emergency_services ?? [];
  const muni = d.municipal_phone;
  $('local-services').hidden = !services.length && !muni;
  const muniRows = [];
  if (muni) {
    const li = el('li', null, 'service');
    const text = el('span', null, 'service-text');
    text.append(
      el('strong', `Seguridad municipal ${muni.phone}`),
      el('span', `Municipalidad de ${muni.comuna}${muni.verified ? '' : ' · número por confirmar'}`),
    );
    const call = el('a', 'Llamar', 'call');
    call.href = `tel:${muni.phone}`;
    call.setAttribute('aria-label', `Llamar a seguridad municipal de ${muni.comuna}, ${muni.phone}`);
    li.append(text, call);
    muniRows.push(li);
  }
  $('muni-note').hidden = !muni?.note;
  $('muni-note').textContent = muni?.note ?? '';
  $('local-list').replaceChildren(
    ...muniRows,
    ...services.map((s) => {
      const li = el('li', null, 'service');
      const text = el('span', null, 'service-text');
      text.append(el('strong', s.name), el('span', `${SERVICE_KINDS[s.kind] ?? s.kind} · ${meters(s.distance_km)}`));
      li.append(text);
      if (s.phone) {
        const call = el('a', 'Llamar', 'call');
        call.href = `tel:${dialable(s.phone)}`;
        call.setAttribute('aria-label', `Llamar a ${s.name}, ${s.phone}`);
        li.append(call);
      }
      const go = el('a', 'Ir', 'go');
      go.href = directionsUrl(s.latitude, s.longitude);
      go.target = '_blank';
      go.rel = 'noopener';
      go.setAttribute('aria-label', `Cómo llegar a ${s.name}`);
      li.append(go);
      return li;
    }),
  );
}

// ---------------------------------------------------------------- probabilidades

const band = (pct) => (pct >= 60 ? 'alta' : pct >= 30 ? 'media' : 'baja');
const bandWord = (pct) => (pct >= 60 ? 'Alta' : pct >= 30 ? 'Media' : pct >= 10 ? 'Baja' : 'Muy baja');

function probRow(title, pct, detail, { neutral = false } = {}) {
  const li = el('li');
  const head = el('div', null, 'prob-head');
  head.append(el('strong', title), el('span', pct == null ? 'Sin datos' : `${pct} %`, 'prob-pct'));
  li.append(head);
  if (pct != null) {
    const bar = el('div', null, 'prob-bar');
    bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label', `${title}: probabilidad ${bandWord(pct).toLowerCase()}, ${pct} por ciento`);
    const fill = el('i');
    fill.style.width = `${Math.max(pct, 2)}%`;
    fill.dataset.band = neutral ? 'baja' : band(pct);
    bar.append(fill);
    li.append(bar);
  }
  li.append(el('p', detail, 'prob-detail'));
  return li;
}

function renderProbabilities(d) {
  const p = d.probabilities;
  $('mon-prob').hidden = !p;
  if (!p) return;
  const t = d.rain_thresholds;
  const [amarillo, naranja, rojo] = p.debris_pct;
  const nearQuebrada = d.debris_flow?.susceptible;
  const debrisDetail = p.snow
    ? 'Nieva en la cordillera: el agua no baja por las quebradas.'
    : `Lluvia de ${num(t?.amarillo_mm ?? 0)} mm o más (alerta amarilla). Naranja ${naranja} % · roja ${rojo} %.` +
      (nearQuebrada === false ? ' No hay quebradas a menos de 500 m de este punto.' : '');
  const stormDetail = p.storm_pct == null
    ? 'El modelo no entregó datos de inestabilidad para calcularla.'
    : d.storm_window && p.storm_pct >= 10
      ? `Horario más probable: ${formatWindow(d.storm_window.start, d.storm_window.end).toLowerCase()}. Con truenos, entre a una casa o auto.`
      : 'Rayos y truenos con chubascos. Si oye truenos, entre a una casa o auto y aléjese de cerros descubiertos.';
  const hailDetail = p.hail_pct == null
    ? 'El modelo no entregó datos de inestabilidad para calcularlo.'
    : p.hail_size
      ? `Si cae, sería ${HAIL_SIZES[p.hail_size]}. Suele durar de 5 a 15 minutos. Tamaño orientativo.`
      : 'No se ven las condiciones (tormenta fuerte con chubasco) para granizo.';
  $('prob-list').replaceChildren(
    probRow('Lluvia', p.rain_pct, `Al menos 1 mm. La lluvia más probable: ${mm(p.rain_median_mm)}; escenario lluvioso: hasta ${mm(p.rain_p90_mm)}.`, { neutral: true }),
    probRow('Mucha agua en poco tiempo', p.intense_pct, `${num(p.intense_hourly_mm)} mm o más en una sola hora: calles anegadas y quebradas que crecen de golpe.`),
    probRow('Aluvión', amarillo, debrisDetail),
    probRow('Tormenta eléctrica', p.storm_pct, stormDetail),
    probRow('Granizo', p.hail_pct, hailDetail),
  );
  $('prob-note').textContent = `Calculado con ${p.members} corridas de los modelos ICON y GFS: es la parte de las corridas que llega a cada situación.`;
}

// ---------------------------------------------------------------- comunas de la región

const dayHourFmt = new Intl.DateTimeFormat('es-CL', { weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

/** Estudio por modelos de Windy (GFS, ICON); `null` si no hay clave o no respondió. */
export function renderWindyStudy(study) {
  $('windy-study').hidden = !study;
  if (!study) return;
  $('windy-verdict').textContent = study.verdict;
  $('windy-models').replaceChildren(
    ...study.models.map((m) => {
      const parts = [`${num(m.rain_mm, 1)} mm en 24 h`, `máx. ${num(m.max_3h_mm, 1)} mm en 3 h`];
      if (m.convective_mm > 0) parts.push(`${num(m.convective_mm, 1)} mm de chubasco`);
      if (m.max_cape_j_kg != null) parts.push(`CAPE ${m.max_cape_j_kg} J/kg`);
      if (m.max_gust_kmh != null) parts.push(`ráfagas ${m.max_gust_kmh} km/h`);
      if (m.first_storm_at) parts.unshift(`tormenta desde ${dayHourFmt.format(new Date(m.first_storm_at))}`);
      return row(m.label, parts.join(' · '), m.storm_periods ? 'AMARILLO' : 'VERDE');
    }),
  );
}

export function renderRegion(overview, { myComuna = null, offline = false, savedAt = null } = {}) {
  if (!overview) {
    $('region-title').textContent = 'Comunas de su región';
    $('region-sub').textContent = 'Active su ubicación para verlas';
    $('region-list').replaceChildren();
    return;
  }
  const count = (level) => overview.comunas.filter((c) => c.level === level).length;
  const byLevel = ['ROJO', 'NARANJA', 'AMARILLO']
    .map((level) => [count(level), LEVELS[level]?.word?.toLowerCase() ?? level.toLowerCase()])
    .filter(([n]) => n)
    .map(([n, word]) => `${n} en ${word}`);
  const stormy = overview.comunas.filter((c) => c.storm).length;
  $('region-title').textContent = `Comunas de ${overview.region}`;
  $('region-sub').textContent =
    `${overview.comunas.length} comunas · ${byLevel.length ? byLevel.join(', ') : 'todas en verde'}` +
    (stormy ? ` · ${stormy} con tormenta posible` : '') +
    (offline ? ` · guardado ${savedAt ? formatTime(savedAt) : ''}` : '');
  $('region-list').replaceChildren(
    ...overview.comunas.map((c) => {
      const notes = [];
      if (c.official_level) notes.push(`SENAPRED: ${(OFFICIAL_WORDS[c.official_level] ?? c.official_level).toLowerCase()} por ${c.official_hazard?.toLowerCase() ?? 'emergencia'}`);
      notes.push(`${mm(c.rain_mm)} en 24 h`);
      if (c.max_hourly_mm >= 10) notes.push(`chubasco de ${num(c.max_hourly_mm)} mm/h`);
      if (c.storm) notes.push('tormenta posible');
      if (c.snow) notes.push('nieva en altura');
      const item = row(c.comuna === myComuna ? `${c.comuna} (usted)` : c.comuna, notes.join(' · '), c.level);
      if (c.comuna === myComuna) item.classList.add('row-me');
      return item;
    }),
  );
}

function renderQuality(d, { stale }) {
  const note = $('quality-note');
  if (stale) {
    note.textContent = 'Sin conexión: este es su último diagnóstico guardado. Puede no reflejar la situación actual.';
    note.hidden = false;
  } else if (d.data_quality === 'PARCIAL') {
    const missing = d.missing_sources.map((s) => MISSING_SOURCE_LABELS[s] ?? s).join(', ');
    note.textContent = `Diagnóstico parcial: no se pudo obtener ${missing}. Ante la duda, siga las indicaciones de la autoridad.`;
    note.hidden = false;
  } else {
    note.hidden = true;
  }
}

// ---------------------------------------------------------------- Monitoreo

// ---------------------------------------------------------------- alertas dinámicas

const LEVEL_RANK = { ROJO: 4, NARANJA: 3, AMARILLO: 2, INFO: 1, VERDE: 0 };
const officialKey = (a) => `${a.url}|${a.level}`;

/** Alertas oficiales nuevas o que cambiaron de nivel (marcadas «NUEVA» durante 1 hora). Avisa con un toast. */
function newOfficialKeys(d) {
  const now = Date.now();
  const stored = load(KEYS.seenOfficial);
  const seen = stored && !Array.isArray(stored) ? stored : null; // formato anterior (lista): se reinicia
  const firstRun = seen == null;
  const next = {};
  for (const [k, at] of Object.entries(seen ?? {})) if (now - at < 30 * 24 * 3600000) next[k] = at;
  const arrived = [];
  for (const a of d.official_alerts ?? []) {
    const k = officialKey(a);
    if (!(k in next)) {
      next[k] = firstRun ? 0 : now; // la primera vez nada es «nuevo»
      // Sólo interrumpe lo que rige donde está el usuario y es de lluvia/crecida/aluvión/tormenta.
      if (!firstRun && a.hydromet && a.applies_to !== 'REGION') arrived.push(a);
    }
  }
  save(KEYS.seenOfficial, next);
  if (arrived.length) toast(`Nueva alerta SENAPRED: ${OFFICIAL_WORDS[arrived[0].level] ?? arrived[0].level} por ${arrived[0].hazard.toLowerCase()}.`, 'error');
  return new Set((d.official_alerts ?? []).map(officialKey).filter((k) => now - next[k] < 3600000));
}

/** Arriba sólo lo que está activo, de más grave a menos; lo que está en verde queda plegado en una línea. */
function renderAlertList(rows) {
  if (!rows.length) {
    $('mon-alerts').replaceChildren(el('li', 'Sin datos del pronóstico en este momento.', 'muted'));
    $('mon-alerts-title').textContent = 'Alertas activas';
    return;
  }
  const levelOf = (r) => r.querySelector('.level-pill')?.dataset.level ?? 'INFO';
  const active = rows
    .filter((r) => levelOf(r) !== 'VERDE')
    .map((r, i) => ({ r, i }))
    .sort((a, b) => LEVEL_RANK[levelOf(b.r)] - LEVEL_RANK[levelOf(a.r)] || (b.r.dataset.official === 'true') - (a.r.dataset.official === 'true') || a.i - b.i)
    .map(({ r }) => r);
  const calm = rows.filter((r) => levelOf(r) === 'VERDE');
  for (const r of active) r.querySelector('.level-pill[data-level="INFO"]')?.remove();
  const items = [...active];
  if (calm.length) {
    const names = calm.map((r) => r.querySelector('strong')?.firstChild?.textContent.replace(/ en su punto$/, '')).join(', ');
    const details = el('details');
    const summary = el('summary');
    summary.append(el('strong', 'Sin alerta: '), names);
    const list = el('ul', null, 'rows');
    list.append(...calm);
    details.append(summary, list);
    const group = el('li', null, 'calm-group');
    group.append(details);
    items.push(group);
  }
  $('mon-alerts').replaceChildren(...items);
  const counted = active.filter((r) => levelOf(r) !== 'INFO').length;
  $('mon-alerts-title').textContent = counted ? `Alertas activas (${counted})` : active.length ? 'Avisos de SENAPRED' : 'Sin alertas activas';
}

const relFmt = new Intl.RelativeTimeFormat('es-CL', { numeric: 'auto' });

/** «hace 5 minutos», «hace 12 horas», «ayer». */
function relTime(iso) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (Math.abs(minutes) < 60) return relFmt.format(-minutes, 'minute');
  const hours = Math.round(minutes / 60);
  if (hours < 24) return relFmt.format(-hours, 'hour');
  return relFmt.format(-Math.round(hours / 24), 'day');
}

let liveTimer = null;

/** Línea «En vivo · revisado hace 1 minuto»: se actualiza sola cada 30 s. */
function startLiveLine(d, { savedAt, stale }) {
  const line = $('mon-live');
  line.hidden = false;
  const paint = () => {
    const at = stale && savedAt ? savedAt : d.evaluated_at;
    const old = stale || Date.now() - new Date(at).getTime() > 10 * 60000;
    line.dataset.stale = String(old);
    $('mon-live-text').textContent = old
      ? `Sin conexión · datos de ${relTime(at)}`
      : `En vivo · revisado ${relTime(at)} · SENAPRED y pronóstico se revisan cada 3 minutos`;
    for (const r of document.querySelectorAll('#mon-alerts .row[data-official="true"]')) {
      const span = r.querySelector('.row-text > span');
      if (span) span.textContent = span.textContent.replace(/ · (hace|ayer|anteayer|ahora|este|dentro de).*$/, '') + ` · ${relTime(r.dataset.published)}`;
    }
  };
  clearInterval(liveTimer);
  paint();
  liveTimer = setInterval(paint, 30000);
}

function row(name, detail, level, value = null) {
  const li = el('li', null, 'row');
  const text = el('span', null, 'row-text');
  text.append(el('strong', name), el('span', detail));
  li.append(text);
  if (value != null) li.append(el('span', value, 'row-value'));
  if (level) {
    const pill = el('span', LEVELS[level]?.word ?? level, 'level-pill');
    pill.dataset.level = level;
    li.append(pill);
  }
  return li;
}

function renderMonitor(d, { savedAt, stale }) {
  const where = d.area ? `${placeName(d)}, ${d.area.region}` : placeName(d);
  $('mon-sub').textContent = `${where} · ${stale && savedAt ? `guardado ${formatTime(savedAt)}` : `actualizado ${formatTime(d.evaluated_at)}`}`;

  // Alertas por amenaza en la ubicación del usuario.
  const rows = [];
  const quebrada = nearestOf(d, 'QUEBRADA');
  // Aluvión y deslizamiento van juntos (remoción en masa): una sola fila con el peor de los dos.
  if (d.debris_flow || d.landslide) {
    const parts = [];
    if (d.debris_flow?.susceptible) parts.push(quebrada ? `quebrada a ${meters(quebrada.distance_km)}` : 'cauce de montaña cerca');
    else if (d.debris_flow) parts.push('sin quebradas a menos de 500 m');
    if (slopeMatters(d)) parts.push(`ladera de ${num(d.landslide.slope_deg)}°`);
    else if (d.landslide) parts.push('terreno plano o de poca pendiente');
    const level = [d.debris_flow?.level, d.landslide?.level].reduce((a, b) => (severity(b) > severity(a) ? b : a), 'VERDE');
    const debrisPct = d.debris_flow?.susceptible && d.probabilities && !d.probabilities.snow ? d.probabilities.debris_pct[0] : null;
    const detail = parts.join(' · ');
    rows.push(row('Aluvión', withPct(detail.charAt(0).toUpperCase() + detail.slice(1), debrisPct), level));
  }
  if (d.consensus) {
    const level = d.consensus.outcome === 'ALERTA_DESBORDE' ? d.consensus.risk_level : 'VERDE';
    const intense = d.probabilities?.intense_pct;
    const lowered = floodReduction(d);
    rows.push(row(
      'Lluvia intensa y desborde',
      `${mm(d.consensus.weighted_precipitation_mm)} en 24 horas${lowered ? ` · ${lowered}` : ''}${intense != null ? ` · ${intense} % de chubasco fuerte` : ''}`,
      level,
    ));
    const when = d.storm_window ? formatWindow(d.storm_window.start, d.storm_window.end) : 'Posible en las próximas 24 horas';
    const stormPct = d.probabilities?.storm_pct;
    const stormText = officialStorm(d) && !(stormPct >= STORM_ALERT_PCT)
      ? 'SENAPRED anuncia probables tormentas eléctricas'
      : stormLikely(d) ? when : stormPct >= 10 ? 'Poco probable' : 'Sin señales de tormenta';
    rows.push(row('Tormenta eléctrica', withPct(stormText, stormPct), stormLikely(d) ? 'AMARILLO' : 'VERDE'));
  }
  const gust = d.conditions?.max_wind_gust_kmh;
  if (gust != null) {
    const level = WIND_GUST_LEVELS.find((w) => gust >= w.min)?.level ?? 'VERDE';
    rows.push(row('Viento fuerte', `Ráfagas de hasta ${num(gust)} km/h`, level));
  }
  // Los avisos de zona se integran en la fila de su amenaza (no se repiten como fila aparte).
  const ROW_OF_THREAT = {
    TORMENTA_ELECTRICA: 'Tormenta eléctrica',
    DESBORDE_RIO: 'Lluvia intensa y desborde',
    INUNDACION: 'Lluvia intensa y desborde',
    REMOCION_EN_MASA: 'Aluvión',
  };
  const rowName = (r) => r.querySelector('strong')?.textContent.replace(/ en su punto$/, '');
  // Las alertas oficiales van en filas propias, arriba: rigen para toda la comuna o región, mientras
  // las filas de abajo describen su punto exacto (así no se mezclan «Roja» con «sin quebradas»).
  const fresh = newOfficialKeys(d);
  // Incendios, volcanes o emergencias sanitarias quedan en la tarjeta de SENAPRED de Inicio, no aquí.
  const officialRows = (d.official_alerts ?? []).filter((a) => a.hydromet).map((a) => {
    const r = row(
      `${a.hazard} · SENAPRED`,
      `${OFFICIAL_WORDS[a.level] ?? a.level} · ${officialWhere(a, d)} · ${relTime(a.published_at)}`,
      // Sólo la alerta que nombra su comuna pinta la fila; la regional se informa.
      a.sets_level ? OFFICIAL_TO_LEVEL[a.level] : 'INFO',
    );
    r.dataset.official = 'true';
    r.dataset.published = a.published_at;
    if (fresh.has(officialKey(a))) {
      r.dataset.new = 'true';
      r.querySelector('strong').append(el('span', 'NUEVA', 'new-badge'));
    }
    return r;
  });
  if (officialRows.length) {
    for (const r of rows) {
      const name = r.querySelector('strong');
      if (name && ['Aluvión', 'Lluvia intensa y desborde'].includes(name.textContent)) {
        name.textContent = `${name.textContent} en su punto`;
      }
    }
  }
  rows.unshift(...officialRows);
  const allAlerts = [...(d.active_alerts ?? []), ...(d.comuna_alerts ?? [])];
  for (const alert of allAlerts) {
    const target = rows.find((r) => rowName(r) === ROW_OF_THREAT[alert.threat_type]);
    const where = alert.zone_name ? ` en ${alert.zone_name}` : '';
    const until = `aviso${where} vigente hasta ${formatTime(alert.valid_until)}`;
    if (!target) {
      rows.push(row(alert.title, until.replace(/^a/, 'A'), alert.risk_level));
      continue;
    }
    const detail = target.querySelector('.row-text span');
    detail.textContent = `${detail.textContent} · ${until}`;
    const pill = target.querySelector('.level-pill');
    // Un aviso de otro sector de la comuna informa, pero no sube el nivel de su ubicación.
    if (!alert.zone_name && pill && severity(alert.risk_level) > severity(pill.dataset.level)) {
      pill.dataset.level = alert.risk_level;
      pill.textContent = LEVELS[alert.risk_level]?.word ?? alert.risk_level;
    }
  }
  renderAlertList(rows);
  startLiveLine(d, { savedAt, stale });

  renderRain(d);
  renderHourly(d);
  renderIsotherm(d);
  renderStorm(d);
  renderProbabilities(d);
  renderFlow(d);
  renderModels(d);
  renderDetails(d);
}

const MACROZONE_NAMES = Object.freeze({
  NORTE_GRANDE: 'Norte Grande',
  NORTE_CHICO: 'Norte Chico',
  CENTRO: 'Centro',
  SUR: 'Sur',
  AUSTRAL: 'Austral',
});

function renderDetails(d) {
  const area = d.area;
  const facts = [
    ['Comuna', area?.comuna ?? 'Sin identificar'],
    ['Región', area?.region ?? '–'],
    ['Altura', d.elevation_m != null ? `${num(d.elevation_m)} m` : '–'],
    ['Zona climática', MACROZONE_NAMES[d.macrozone] ?? '–'],
    ['Coordenadas', `${num(d.location.latitude, 3)}, ${num(d.location.longitude, 3)}`],
    ['Cauces a 3 km', String(d.nearby_watercourses?.length ?? 0)],
  ];
  $('detail-stats').replaceChildren(
    ...facts.map(([label, value]) => {
      const box = el('div');
      box.append(el('span', label), el('strong', value));
      return box;
    }),
  );
  $('details-sub').textContent = [
    area ? `${area.comuna}, ${area.region}` : null,
    d.elevation_m != null ? `${num(d.elevation_m)} m de altura` : null,
  ].filter(Boolean).join(' · ') || 'Comuna, altura y qué se revisó';
  $('mon-details').replaceChildren(...(d.recommendations ?? []).slice(1).map((text) => el('li', text)));
  const missing = (d.missing_sources ?? []).map((s) => MISSING_SOURCE_LABELS[s] ?? s);
  $('detail-quality').hidden = !missing.length;
  $('detail-quality').textContent = missing.length ? `No se pudo revisar: ${missing.join(', ')}.` : '';
}

const REDUCTION_TEXT = Object.freeze({
  SUELO_SECO: 'suelo seco: absorbe parte del agua',
  ISOTERMA_BAJA: 'nieva en la cordillera: baja menos agua',
});

/** Por qué el nivel de desborde quedó bajo lo que indica la lluvia sola (o null si no se rebajó). */
function floodReduction(d) {
  const c = d.consensus;
  if (!c || severity(c.risk_level) >= severity(c.raw_risk_level)) return null;
  const why = (c.discard_reasons ?? []).map((r) => REDUCTION_TEXT[r]).filter(Boolean);
  if (c.downgraded_by_disagreement) why.push('los modelos no coinciden');
  return why.length ? `rebajado: ${why.join('; ')}` : null;
}

function renderRain(d) {
  const thresholds = d.rain_thresholds;
  const marker = $('rain-marker');
  if (!d.consensus || !thresholds) {
    $('rain-value').textContent = '–';
    marker.hidden = true;
    return;
  }
  // Misma escala que la fila «Lluvia intensa y desborde» (la de aluvión es otra y va en Inicio).
  const [amarillo, naranja, rojo] = thresholds.flood_mm ?? [thresholds.amarillo_mm, thresholds.naranja_mm, thresholds.rojo_mm];
  const t = { amarillo_mm: amarillo, naranja_mm: naranja, rojo_mm: rojo, intense_hourly_mm: thresholds.intense_hourly_mm };
  const value = d.consensus.weighted_precipitation_mm;
  const top = t.rojo_mm * 1.2;
  $('rain-value').textContent = num(value, value < 10 ? 1 : 0);
  const gauge = $('rain-gauge');
  gauge.style.gridTemplateColumns = `${t.amarillo_mm}fr ${t.naranja_mm - t.amarillo_mm}fr ${t.rojo_mm - t.naranja_mm}fr ${top - t.rojo_mm}fr`;
  gauge.setAttribute('aria-label', `${mm(value)} pronosticados en 24 horas`);
  marker.style.left = `${Math.min(value / top, 1) * 100}%`;
  marker.hidden = false;
  $('rain-scale').replaceChildren(
    ...[0, t.amarillo_mm, t.naranja_mm, t.rojo_mm].map((v) => {
      const label = el('span', num(v));
      label.style.left = `${(v / top) * 100}%`;
      return label;
    }),
  );
  const lowered = floodReduction(d);
  const caption =
    value >= t.rojo_mm ? `Sobre ${num(t.rojo_mm)} mm: lluvia muy fuerte para su zona.`
      : value >= t.naranja_mm ? `Sobre ${num(t.naranja_mm)} mm: lluvia fuerte para su zona.`
        : value >= t.amarillo_mm ? `Sobre ${num(t.amarillo_mm)} mm: lluvia que puede hacer crecer esteros y ríos.`
          : 'Lluvia baja para su zona.';
  const shown = d.consensus.outcome === 'ALERTA_DESBORDE' ? d.consensus.risk_level : 'VERDE';
  $('rain-caption').textContent = lowered
    ? `${caption} Nivel de desborde: ${(LEVELS[shown]?.word ?? shown).toLowerCase()} (${lowered}).`
    : caption;
  const c = d.conditions;
  $('rain-extra').textContent = c
    ? `Ya cayeron ${mm(c.recent_precipitation_mm)} en las últimas 24 h · máximo ${num(c.max_hourly_precipitation_mm, 1)} mm/h` +
      (c.max_hourly_precipitation_mm >= t.intense_hourly_mm ? ' (chubasco intenso)' : '')
    : '';
}

function renderHourly(d) {
  const hours = d.hourly_rain ?? [];
  $('mon-hourly').hidden = !hours.length;
  if (!hours.length) return;
  $('hourly-bars').replaceChildren(
    ...hours.map((h) => {
      const v = h.precipitation_mm;
      const bar = el('div', null, 'bar');
      const fill = el('i');
      fill.style.height = `${Math.min(v * 9, 110)}px`;
      fill.dataset.kind = v >= 8 ? 'very' : v >= 4 ? 'strong' : 'weak';
      bar.append(el('span', num(v, v < 10 ? 1 : 0)), fill);
      return bar;
    }),
  );
  $('hourly-axis').replaceChildren(...[0, 3, 6, 9].filter((i) => hours[i]).map((i) => el('span', hourFmt.format(new Date(hours[i].time)))));
  const peak = hours.reduce((a, b) => (b.precipitation_mm > a.precipitation_mm ? b : a));
  $('hourly-bars').setAttribute(
    'aria-label',
    peak.precipitation_mm > 0
      ? `Lluvia por hora: la más fuerte, ${mm(peak.precipitation_mm)}, a las ${hourFmt.format(new Date(peak.time))}`
      : 'Sin lluvia en las próximas 12 horas',
  );
}

function renderStorm(d) {
  const show = stormLikely(d);
  $('mon-storm').hidden = !show;
  if (!show) return;
  $('storm-when').textContent = d.storm_window
    ? formatWindow(d.storm_window.start, d.storm_window.end)
    : 'En las próximas 24 horas';
}

function renderIsotherm(d) {
  const c = d.conditions;
  const t = d.rain_thresholds;
  if (!c || !t) {
    $('iso-value').textContent = '–';
    $('iso-status').textContent = '';
    $('iso-text').textContent = '';
    return;
  }
  const rains = c.freezing_level_m >= t.min_freezing_level_m;
  $('iso-value').textContent = `${num(c.freezing_level_m)} m`;
  $('iso-status').textContent = rains ? 'Llueve en la cordillera' : 'Nieva en la cordillera';
  $('iso-status').dataset.rain = String(rains);
  $('iso-text').textContent = rains
    ? `Sobre ${num(t.min_freezing_level_m)} m no nieva: el agua baja directo por las quebradas. Por eso sube el riesgo de aluvión.`
    : `Bajo ${num(t.min_freezing_level_m)} m cae nieve en altura: el agua no baja de inmediato por las quebradas.`;
}

function renderFlow(d) {
  const flow = d.river_flow;
  $('mon-flow').hidden = !flow;
  if (!flow) return;
  const peak = new Date(`${flow.peak_date}T12:00:00`).toLocaleDateString('es-CL', { day: 'numeric', month: 'short' });
  $('flow-name').textContent = `${flow.name} · a ${meters(flow.distance_km)}`;
  $('flow-today').textContent = `${num(flow.today_m3s, 1)} m³/s`;
  $('flow-peak').textContent = `${num(flow.peak_m3s, 1)} m³/s (${peak})`;
  $('flow-trend').textContent = FLOW_TRENDS[flow.trend] ?? flow.trend;
}

function renderModels(d) {
  const models = d.model_precipitation ?? [];
  $('mon-models-card').hidden = !models.length;
  if (!models.length) return;
  $('mon-models').replaceChildren(
    ...models.map((m) => {
      const box = el('div');
      box.append(el('span', MODEL_NAMES[m.model] ?? m.model), el('strong', mm(m.precipitation_mm)));
      return box;
    }),
  );
  const t = d.rain_thresholds;
  if (t && d.consensus) {
    // Coinciden los modelos que quedan del mismo lado del umbral amarillo que el consenso.
    const wet = (v) => v >= t.amarillo_mm;
    const side = wet(d.consensus.weighted_precipitation_mm);
    const agree = models.filter((m) => wet(m.precipitation_mm) === side).length;
    $('models-title').textContent = `${agree} de ${models.length} modelos coinciden`;
  } else {
    $('models-title').textContent = 'Pronóstico de cada modelo';
  }
  $('models-note').textContent = d.consensus?.downgraded_by_disagreement
    ? 'Los modelos no coinciden: la app bajó un nivel para evitar falsas alarmas.'
    : 'Cuando los modelos no coinciden, la app baja un nivel para evitar falsas alarmas.';
}

// ---------------------------------------------------------------- Punto seguro

function renderSafe(d) {
  const [first, ...others] = d.safe_points ?? [];
  const threat = mainThreat(d);
  const route = $('safe-route');
  const hint = $('safe-hint');
  if (first) {
    $('safe-name').textContent = first.name;
    $('safe-meta').textContent = `${meters(first.distance_km)} · ${walkMinutes(first.distance_km)} min caminando · ${first.available_capacity} cupos`;
    route.href = directionsUrl(first.location.latitude, first.location.longitude);
    route.setAttribute('aria-label', `Abrir la ruta a ${first.name}`);
    route.hidden = false;
  } else {
    $('safe-name').textContent = 'Sin puntos seguros oficiales cerca';
    $('safe-meta').textContent = 'Busque terreno alto y firme, lejos de ríos, quebradas y laderas.';
    route.hidden = true;
  }
  hasOfficialSafePoint = Boolean(first);
  hint.textContent = ROUTE_HINTS[threat] ?? (d.debris_flow?.susceptible ? ROUTE_HINTS.ALUVION : ROUTE_HINTS.DESBORDE);
  hint.hidden = false;

  $('safe-points').replaceChildren(
    ...(others.length
      ? others.map((point) => {
          const li = el('li');
          const link = el('a', null, 'row row-link');
          link.href = directionsUrl(point.location.latitude, point.location.longitude);
          link.target = '_blank';
          link.rel = 'noopener';
          link.setAttribute('aria-label', `Cómo llegar a ${point.name}, a ${meters(point.distance_km)}`);
          const text = el('span', null, 'row-text');
          text.append(el('strong', point.name), el('span', `${walkMinutes(point.distance_km)} min caminando`));
          link.append(text, el('span', meters(point.distance_km), 'row-value'));
          li.append(link);
          return li;
        })
      : [el('li', 'No hay otros puntos registrados cerca.', 'muted')]),
  );
}

/**
 * Agrupa tramos: OpenStreetMap divide un mismo cauce en muchos tramos.
 * Con nombre: un elemento por nombre. Sin nombre: tramos del mismo tipo a menos
 * de ~50 m de diferencia de distancia se consideran el mismo cauce.
 */
export function groupNearby(nearby) {
  const seen = new Map();
  for (const item of nearby ?? []) {
    const unnamed = item.name.endsWith('sin nombre');
    const key = unnamed ? `${item.name}|${Math.round(item.distance_km * 20)}` : `${item.kind}|${item.name}`;
    const current = seen.get(key);
    if (!current || item.distance_km < current.distance_km) seen.set(key, item);
  }
  return [...seen.values()].sort((a, b) => a.distance_km - b.distance_km);
}

function renderNearby(d) {
  const groups = groupNearby(d.nearby_watercourses);
  $('nearby').replaceChildren(
    ...(groups.length
      ? groups.slice(0, 10).map((item) => {
          const kind = WATERCOURSE_KINDS[item.kind] ?? WATERCOURSE_KINDS.ESTERO;
          const li = el('li', null, 'row');
          li.style.setProperty('--kind-color', kind.color);
          const governs = isGoverning(item, d.watercourse);
          const text = el('span', null, 'row-text');
          text.append(el('strong', item.name), el('span', governs ? `${kind.label} · el que más influye en su riesgo` : kind.label));
          li.append(el('span', null, 'kind-bar'), text, el('span', meters(item.distance_km), 'row-value'));
          return li;
        })
      : [el('li', 'No hay ríos, esteros, quebradas ni canales registrados a menos de 3 km de usted.', 'muted')]),
  );

  const debris = $('debris');
  if (d.debris_flow) {
    debris.textContent = d.debris_flow.susceptible
      ? `Aluvión: ${d.debris_flow.reason}`
      : 'Aluvión: sin quebradas ni esteros de montaña a menos de 500 m. Lugar no expuesto.';
    debris.dataset.level = d.debris_flow.level;
    debris.hidden = false;
  } else {
    debris.hidden = true;
  }
}

// ---------------------------------------------------------------- otros

export function renderPhones() {
  $('phones').replaceChildren(
    ...PHONES.map(({ number, who }) => {
      const li = el('li');
      const link = el('a');
      link.href = `tel:${number}`;
      link.setAttribute('aria-label', `Llamar al ${number}, ${who}`);
      link.append(el('span', number, 'num'), el('span', who, 'who'));
      li.append(link);
      return li;
    }),
  );
}

export function setStatus(id, text, kind = '') {
  const node = $(id);
  node.textContent = text;
  node.dataset.kind = kind;
}

export function setProtectButton({ busy = false, active = false } = {}) {
  const button = $('btn-protect');
  button.disabled = busy;
  button.dataset.state = active ? 'active' : 'idle';
  // Como en el diseño: con la protección activa, el botón grande se reemplaza por «Actualizar» en la tarjeta.
  button.hidden = active && !busy;
  $('btn-refresh').hidden = !active;
  $('btn-protect-label').textContent = busy
    ? 'Un momento…'
    : active
      ? 'Actualizar mi diagnóstico'
      : 'Activar protección en mi ubicación';
}

const PLACE_KIND_WORDS = Object.freeze({
  city: 'Ciudad', town: 'Pueblo', village: 'Villa o pueblo', suburb: 'Sector', hamlet: 'Caserío', locality: 'Localidad',
});

export function renderPlaceResults(places, { onPick, query }) {
  const list = $('place-results');
  if (!query) {
    list.replaceChildren();
    return;
  }
  if (!places.length) {
    list.replaceChildren(el('li', 'No encontramos ese lugar. Revise cómo se escribe o búsquelo en el mapa.', 'muted'));
    return;
  }
  list.replaceChildren(
    ...places.map((place) => {
      const button = el('button');
      button.type = 'button';
      const isComuna = place.kind === 'comuna';
      const kind = PLACE_KIND_WORDS[place.kind] ?? 'Localidad';
      button.append(
        el('strong', place.name),
        el('span', isComuna ? `Comuna · ${place.region}` : `${kind} · comuna de ${place.comuna}, ${place.region}`),
      );
      button.addEventListener('click', () => onPick(place));
      const item = el('li');
      item.append(button);
      return item;
    }),
  );
}

export function setNetworkStatus(online) {
  // Sólo se avisa cuando falta la conexión: «En línea» no le sirve al usuario.
  $('net-status').hidden = online;
  $('net-label').textContent = online ? 'En línea' : 'Sin conexión';
  $('net-status').dataset.offline = String(!online);
}

let toastTimer = null;
export function toast(message, kind = '') {
  const node = $('toast');
  node.textContent = message;
  node.dataset.kind = kind;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, 7000);
}

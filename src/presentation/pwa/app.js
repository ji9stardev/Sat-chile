// SAT-Maule PWA: orquestación. Cada módulo de ./js tiene una sola responsabilidad.
import { ApiError, api } from './js/api.js';
import { REFRESH_INTERVAL_MS, REPORT_OPTIONS, REPORT_QUEUE_MAX_AGE_MS } from './js/config.js';
import { getPosition } from './js/geo.js';
import {
  initMap,
  loadRadar,
  mapCenter,
  cancelPick,
  pickOnMap,
  refreshSize,
  renderLayers,
  showNearby,
  showRefuges,
  showUser,
} from './js/map.js';
import { enablePush, isIOS, isStandalone, requestNotificationPermission } from './js/push.js';
import { KEYS, deviceId, load, save } from './js/storage.js';
import * as ui from './js/ui.js';

const $ = (id) => document.getElementById(id);
const SLOW_SERVER_HINT_MS = 8000;

let lastPosition = null;
let lastRefreshAt = 0;
let diagnosisSeq = 0; // sólo se muestra la respuesta del último pedido

// ---------------------------------------------------------------- Service Worker

async function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  // Cuando una versión nueva toma el control, recargar una vez para no mezclar archivos viejos y nuevos.
  const hadController = Boolean(navigator.serviceWorker.controller);
  let reloaded = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!hadController || reloaded) return;
    reloaded = true;
    window.location.reload();
  });
  try {
    await navigator.serviceWorker.register('/sw.js', { scope: '/' });
  } catch (error) {
    console.warn('No se pudo registrar el Service Worker', error);
  }
  navigator.serviceWorker.addEventListener('message', (event) => {
    if (event.data?.type !== 'SAT_ALERT') return;
    const { title, body } = event.data.payload ?? {};
    ui.toast(title ? `${title}. ${body ?? ''}` : 'Nueva alerta recibida', 'error');
    refreshDiagnosis({ silent: true });
    loadZonesAndMap();
  });
}

// ---------------------------------------------------------------- diagnóstico

function showSavedDiagnosis() {
  const saved = load(KEYS.lastDiagnosis);
  if (saved?.diagnosis) {
    ui.renderDiagnosis(saved.diagnosis, { savedAt: saved.savedAt, stale: true, manual: saved.manual ?? null });
    const region = load(KEYS.regionOverview);
    if (region?.data?.region_code === saved.diagnosis.area?.region_code) {
      ui.renderRegion(region.data, { myComuna: saved.diagnosis.area?.comuna, offline: true, savedAt: region.savedAt });
    }
  }
  return Boolean(saved?.diagnosis);
}

async function refreshDiagnosis({ position = null, silent = false } = {}) {
  const protection = load(KEYS.protection);
  // Un lugar elegido a mano se mantiene hasta que el usuario vuelva a su GPS.
  const manual = position ? null : load(KEYS.manualPlace);
  if (manual) position = { lat: manual.lat, lon: manual.lon, accuracy: 0 };
  if (!position) {
    if (!protection?.active) return;
    try {
      position = await getPosition({ highAccuracy: false, maxAgeMs: 120000 });
    } catch {
      position = { lat: protection.lat, lon: protection.lon, accuracy: 100 }; // última ubicación conocida
    }
  }
  lastPosition = position;
  if (!manual) save(KEYS.protection, { active: true, lat: position.lat, lon: position.lon, at: Date.now() });

  const seq = ++diagnosisSeq;
  if (!silent) ui.setStatus('protect-status', manual ? `Calculando el riesgo en ${manual.name}…` : 'Calculando el riesgo en su ubicación…');
  const slowHint = setTimeout(() => {
    if (!silent) ui.setStatus('protect-status', 'El servidor está despertando; puede tardar hasta un minuto…');
  }, SLOW_SERVER_HINT_MS);
  try {
    const { data } = await api.gpsCheck(position.lat, position.lon);
    if (seq !== diagnosisSeq) return; // llegó tarde: ya se pidió otro lugar
    save(KEYS.lastDiagnosis, { diagnosis: data, savedAt: new Date().toISOString(), manual });
    ui.renderDiagnosis(data, { manual });
    loadRegion(data);
    loadRefuges(position);
    showUser(position);
    showNearby(data.nearby_watercourses, position, data.watercourse);
    lastRefreshAt = Date.now();
    if (!silent) ui.setStatus('protect-status', '');
  } catch (error) {
    if (seq !== diagnosisSeq) return;
    const hadSaved = showSavedDiagnosis();
    if (!silent) {
      ui.setStatus('protect-status', `${error.message}${hadSaved ? ' Se muestra su último diagnóstico.' : ''}`, 'error');
    }
  } finally {
    clearTimeout(slowHint);
  }
}

// ---------------------------------------------------------------- botón principal

async function onProtectClick() {
  ui.setProtectButton({ busy: true });
  // El permiso de notificaciones se pide primero, dentro del gesto del usuario (requisito de Safari).
  const permissionPromise = requestNotificationPermission();
  try {
    ui.setStatus('protect-status', 'Obteniendo su ubicación…');
    const position = await getPosition();
    save(KEYS.manualPlace, null);
    await refreshDiagnosis({ position });

    ui.setStatus('protect-status', 'Activando alertas en este celular…');
    const permission = await permissionPromise;
    const result = await enablePush(position, permission).catch((error) => ({ ok: false, reason: 'error', error }));
    ui.setStatus('protect-status', pushMessage(result), result.ok ? 'ok' : 'error');
  } catch (error) {
    ui.setStatus('protect-status', error.message, 'error');
  } finally {
    ui.setProtectButton({ active: Boolean(load(KEYS.protection)?.active) });
  }
}

async function onRefreshClick() {
  const button = $('btn-refresh');
  button.disabled = true;
  try {
    if (load(KEYS.manualPlace)) {
      await refreshDiagnosis();
    } else {
      const position = await getPosition({ highAccuracy: false, maxAgeMs: 60000 });
      await refreshDiagnosis({ position });
    }
  } catch (error) {
    ui.setStatus('protect-status', error.message, 'error');
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------- cambiar ubicación

async function useManualPlace(place) {
  $('place-dialog').close();
  save(KEYS.manualPlace, place);
  location.hash = '#inicio';
  await refreshDiagnosis();
}

async function useGpsPlace() {
  $('place-dialog').close();
  ui.setStatus('protect-status', 'Obteniendo su ubicación…');
  try {
    const position = await getPosition();
    save(KEYS.manualPlace, null);
    location.hash = '#inicio';
    await refreshDiagnosis({ position });
    ui.setProtectButton({ active: Boolean(load(KEYS.protection)?.active) });
  } catch (error) {
    ui.setStatus('protect-status', error.message, 'error');
  }
}

function setupPlacePicker() {
  const dialog = $('place-dialog');
  const input = $('place-search');
  let timer = null;
  let lastQuery = '';

  $('btn-change-place').addEventListener('click', () => {
    input.value = '';
    ui.renderPlaceResults([], { query: '' });
    dialog.showModal();
    input.focus();
  });
  $('place-close').addEventListener('click', () => dialog.close());
  $('place-gps').addEventListener('click', useGpsPlace);
  $('btn-back-gps').addEventListener('click', useGpsPlace);

  input.addEventListener('input', () => {
    clearTimeout(timer);
    const query = input.value.trim();
    timer = setTimeout(async () => {
      lastQuery = query;
      if (query.length < 2) {
        ui.renderPlaceResults([], { query: '' });
        return;
      }
      try {
        const { data } = await api.places(query);
        if (query !== lastQuery) return; // llegó tarde: el usuario siguió escribiendo
        ui.renderPlaceResults(data, {
          query,
          onPick: (p) => useManualPlace({ name: p.name, kind: p.kind === 'comuna' ? 'comuna' : 'localidad', lat: p.latitude, lon: p.longitude }),
        });
      } catch (error) {
        ui.renderPlaceResults([], { query });
        ui.toast(error.message, 'error');
      }
    }, 250);
  });

  $('place-map').addEventListener('click', () => {
    dialog.close();
    location.hash = '#seguro';
    requestAnimationFrame(() => {
      refreshSize();
      const armed = pickOnMap(({ lat, lon }) => useManualPlace({ name: 'el punto elegido', kind: 'punto', lat, lon }));
      ui.toast(armed ? 'Toque en el mapa el lugar que quiere consultar.' : 'El mapa no está disponible.', armed ? 'info' : 'error');
    });
  });
}

function pushMessage(result) {
  if (result.ok) return 'Protección activa: le avisaremos aunque tenga la pantalla bloqueada.';
  switch (result.reason) {
    case 'ios-install':
      $('ios-hint').hidden = false;
      return 'En iPhone, primero agregue SAT-Chile a su pantalla de inicio (vea las instrucciones).';
    case 'denied':
      return 'Las notificaciones están bloqueadas. Actívelas en los ajustes del navegador para recibir alertas.';
    case 'dismissed':
      return 'No se activaron las notificaciones. Toque el botón otra vez para permitirlas.';
    case 'unsupported':
      return 'Este navegador no admite alertas push. Abra la app regularmente para ver su nivel.';
    default:
      return result.error instanceof ApiError && result.error.status === 503
        ? 'Diagnóstico listo. Las alertas push aún no están habilitadas en el servidor.'
        : 'Diagnóstico listo, pero no se pudieron activar las alertas. Intente más tarde.';
  }
}

// ---------------------------------------------------------------- refugios y puntos de encuentro

async function loadRefuges(position, { fromMap = false } = {}) {
  if (!position) return;
  if (fromMap) ui.setStatus('refuge-status', 'Buscando refugios en esta zona…');
  try {
    const { data } = await api.refuges(position.lat, position.lon, 10);
    showRefuges(data);
    ui.renderRefuges(data);
    if (fromMap) {
      ui.setStatus(
        'refuge-status',
        data.length
          ? `${data.length} refugios y puntos de encuentro a menos de 10 km del centro del mapa.`
          : 'No hay refugios registrados a menos de 10 km. Mueva el mapa y busque otra vez.',
        data.length ? 'ok' : '',
      );
    }
  } catch (error) {
    if (fromMap) ui.setStatus('refuge-status', error.message, 'error');
  }
}

function setupRefugeSearch() {
  $('btn-search-refuges').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await loadRefuges(mapCenter(), { fromMap: true });
    } finally {
      button.disabled = false;
    }
  });
}

// ---------------------------------------------------------------- comunas de la región

async function loadRegion(diagnosis) {
  const code = diagnosis?.area?.region_code;
  const myComuna = diagnosis?.area?.comuna ?? null;
  if (!code) {
    ui.renderRegion(null);
    return;
  }
  try {
    const { data } = await api.regionOverview(code);
    save(KEYS.regionOverview, { data, savedAt: new Date().toISOString() });
    ui.renderRegion(data, { myComuna });
  } catch {
    const saved = load(KEYS.regionOverview);
    if (saved?.data?.region_code === code) ui.renderRegion(saved.data, { myComuna, offline: true, savedAt: saved.savedAt });
  }
}

// ---------------------------------------------------------------- zonas y mapa

async function loadZonesAndMap() {
  try {
    const { data } = await api.mapLayers();
    renderLayers(data);
    save(KEYS.mapLayers, data);
  } catch {
    renderLayers(load(KEYS.mapLayers));
  }
}

// ---------------------------------------------------------------- navegación (Inicio, Monitoreo, Punto seguro, Reportar)

const VIEWS = ['inicio', 'monitoreo', 'seguro', 'reportar'];

function showView() {
  const name = location.hash.slice(1);
  const current = VIEWS.includes(name) ? name : 'inicio';
  for (const view of VIEWS) $(`view-${view}`).hidden = view !== current;
  if (current !== 'seguro') cancelPick();
  for (const link of document.querySelectorAll('.tabbar a')) {
    if (link.dataset.tab === current) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  window.scrollTo({ top: 0 });
  // Leaflet mide su contenedor: al mostrarse por primera vez hay que avisarle.
  if (current === 'seguro') requestAnimationFrame(refreshSize);
}

function setupNavigation() {
  window.addEventListener('hashchange', showView);
  showView();
  $('radar-box').addEventListener('toggle', (event) => {
    if (event.currentTarget.open) openWindy();
  });
  for (const button of document.querySelectorAll('.windy-layer')) {
    button.addEventListener('click', () => {
      for (const b of document.querySelectorAll('.windy-layer')) b.setAttribute('aria-pressed', String(b === button));
      loadRadar($('radar'), lastPosition, button.dataset.overlay);
    });
  }
}

let windyStudyKey = null;

/** Mapa de Windy y, si el servidor tiene clave de Windy, el estudio por modelos del punto. */
async function openWindy() {
  const overlay = document.querySelector('.windy-layer[aria-pressed="true"]')?.dataset.overlay ?? 'rain';
  loadRadar($('radar'), lastPosition, overlay);
  if (!lastPosition || !navigator.onLine) return;
  const key = `${lastPosition.lat.toFixed(2)},${lastPosition.lon.toFixed(2)}`;
  if (windyStudyKey === key) return;
  windyStudyKey = key;
  try {
    const { data } = await api.windyStudy(lastPosition.lat, lastPosition.lon);
    ui.renderWindyStudy(data);
  } catch (error) {
    ui.renderWindyStudy(null);
    if (!(error instanceof ApiError) || error.status !== 501) windyStudyKey = null; // reintentar después
  }
}

// ---------------------------------------------------------------- reportes ciudadanos

async function sendReport(payload) {
  const { data } = await api.reportWaterLevel(payload);
  return data;
}

function selectedReportOption() {
  return document.querySelector('input[name="observed"]:checked')?.value ?? null;
}

function showReportDone(text) {
  $('report-form').hidden = true;
  $('report-done').hidden = false;
  $('report-done-text').textContent = text;
  $('report-done-title').focus();
}

function resetReport() {
  $('report-form').reset();
  $('btn-send-report').disabled = true;
  ui.setStatus('report-status', '');
  $('report-done').hidden = true;
  $('report-form').hidden = false;
}

async function onReportSubmit(event) {
  event.preventDefault();
  const option = REPORT_OPTIONS[selectedReportOption()];
  if (!option) return;
  const button = $('btn-send-report');
  button.disabled = true;
  try {
    ui.setStatus('report-status', 'Obteniendo su ubicación…');
    const position = await getPosition();
    const text = $('report-desc').value.trim();
    const payload = {
      lat: Number(position.lat.toFixed(5)),
      lon: Number(position.lon.toFixed(5)),
      device_id: deviceId(),
      observed_level: option.observed,
      description: text.length >= 5 ? `${option.text}. ${text}` : option.text,
    };
    try {
      ui.setStatus('report-status', 'Enviando…');
      const outcome = await sendReport(payload);
      showReportDone(outcome.message);
      if (outcome.alert_id) loadZonesAndMap();
    } catch (error) {
      if (isRetryable(error)) {
        enqueueReport(payload);
        showReportDone('Sin conexión con el servidor: su reporte se enviará solo apenas se pueda (hasta 30 minutos).');
      } else {
        ui.setStatus('report-status', error.message, 'error');
      }
    }
  } catch (error) {
    ui.setStatus('report-status', error.message, 'error');
  } finally {
    button.disabled = !selectedReportOption();
  }
}

function setupReportForm() {
  const form = $('report-form');
  form.addEventListener('change', () => {
    $('btn-send-report').disabled = !selectedReportOption();
  });
  form.addEventListener('submit', onReportSubmit);
  $('btn-report-again').addEventListener('click', resetReport);
}

/** Sin señal, tiempo agotado o servidor caído/despertando (5xx): vale la pena reintentar. */
const isRetryable = (error) => error instanceof ApiError && (error.isNetwork || error.status >= 500);

function enqueueReport(payload) {
  const queue = load(KEYS.reportQueue, []);
  queue.push({ id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, payload, queuedAt: Date.now() });
  save(KEYS.reportQueue, queue);
}

let flushing = null;

/** Envía la cola una sola vez a la vez; lo que se encole mientras tanto no se pierde. */
function flushReportQueue() {
  flushing ??= (async () => {
    try {
      const queue = load(KEYS.reportQueue, []);
      if (!queue.length || !navigator.onLine) return;
      const done = new Set();
      for (const item of queue) {
        // Un reporte viejo ya no describe la situación actual: se descarta.
        if (Date.now() - item.queuedAt > REPORT_QUEUE_MAX_AGE_MS) {
          done.add(item.id ?? item.queuedAt);
          continue;
        }
        try {
          await sendReport(item.payload);
          done.add(item.id ?? item.queuedAt);
          ui.toast('Su reporte pendiente fue enviado. Gracias por avisar.');
        } catch (error) {
          if (!isRetryable(error)) done.add(item.id ?? item.queuedAt); // rechazado: no sirve reintentar
        }
      }
      // Se relee la cola: puede haber reportes nuevos encolados durante el envío.
      save(KEYS.reportQueue, load(KEYS.reportQueue, []).filter((item) => !done.has(item.id ?? item.queuedAt)));
    } finally {
      flushing = null;
    }
  })();
  return flushing;
}

// ---------------------------------------------------------------- conectividad e instalación

function setupConnectivity() {
  ui.setNetworkStatus(navigator.onLine);
  window.addEventListener('online', () => {
    ui.setNetworkStatus(true);
    flushReportQueue();
    loadZonesAndMap();
    refreshDiagnosis({ silent: true });
  });
  window.addEventListener('offline', () => ui.setNetworkStatus(false));
}

function setupInstallPrompt() {
  let deferred = null;
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferred = event;
    $('btn-install').hidden = false;
  });
  $('btn-install').addEventListener('click', async () => {
    if (!deferred) return;
    deferred.prompt();
    await deferred.userChoice;
    deferred = null;
    $('btn-install').hidden = true;
  });
  if (isIOS() && !isStandalone()) $('ios-hint').hidden = false;
}

function setupAutoRefresh() {
  const tick = () => {
    flushReportQueue();
    if (document.visibilityState === 'visible' && Date.now() - lastRefreshAt >= REFRESH_INTERVAL_MS) {
      lastRefreshAt = Date.now(); // también si no hay diagnóstico o el servidor falla
      refreshDiagnosis({ silent: true });
      loadZonesAndMap();
    }
  };
  setInterval(tick, 60 * 1000);
  document.addEventListener('visibilitychange', tick);
}

function handleUrlActions() {
  const params = new URLSearchParams(location.search);
  if (params.get('accion') === 'reportar') location.hash = '#reportar';
  if (params.has('alerta')) location.hash = '#inicio';
  if (params.has('accion') || params.has('alerta') || params.has('fuente')) {
    history.replaceState(null, '', location.pathname + location.hash);
  }
}

// ---------------------------------------------------------------- arranque

function start() {
  ui.renderPhones();
  ui.renderEmpty();
  const active = Boolean(load(KEYS.protection)?.active);
  ui.setProtectButton({ active });
  showSavedDiagnosis();

  initMap($('map'));
  renderLayers(load(KEYS.mapLayers));
  setupNavigation();
  setupReportForm();
  setupPlacePicker();
  setupRefugeSearch();
  setupConnectivity();
  setupInstallPrompt();
  setupAutoRefresh();
  handleUrlActions();

  $('btn-protect').addEventListener('click', onProtectClick);
  $('btn-refresh').addEventListener('click', onRefreshClick);

  registerServiceWorker();
  loadZonesAndMap();
  flushReportQueue();
  if (active || load(KEYS.manualPlace)) refreshDiagnosis({ silent: true });
}

start();

"""Modo demostración de SAT-Maule: la app completa sin MongoDB ni configuración.

Por defecto usa **datos meteorológicos reales** (Open-Meteo: ECMWF, DWD ICON,
NOAA GFS; caudales Copernicus GloFAS) y evalúa las zonas al iniciar y cada
30 minutos. El panel http://localhost:8000/demo muestra lo que informa cada
fuente oficial, zona por zona.

* Persistencia en memoria con las 5 zonas piloto, cauces y puntos [DEMO].
* Web Push real con claves VAPID temporales generadas al iniciar.

Uso::

    python scripts/demo.py              # datos reales
    python scripts/demo.py --simulado   # lluvia simulada desde el panel (para ensayar alertas)
    python scripts/demo.py --port 8001  # otro puerto (si el 8000 está ocupado)

Los datos se pierden al cerrar. No usar en producción.
"""

from __future__ import annotations

import httpx

import asyncio
import logging
import secrets
import sys
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, Response

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.application.use_cases import (  # noqa: E402
    DispatchAlertsUseCase,
    EvaluateGpsRiskUseCase,
    GetMapLayersUseCase,
    GetZonesStatusUseCase,
    MonitorZonesUseCase,
    ProcessCrowdsourcingUseCase,
    RegionOverviewUseCase,
    WindyStudyUseCase,
    RegisterSubscriberUseCase,
)
from src.domain.entities import GeoZone, Watercourse, WatercourseKind  # noqa: E402
from src.domain.exceptions import DomainError  # noqa: E402
from src.domain.repositories import WeatherForecastProvider  # noqa: E402
from src.domain.services import (  # noqa: E402
    ConsensusEngineService,
    CooldownPolicy,
    CrowdValidationService,
    ExposureAssessmentService,
)
from src.domain.value_objects import PushNotification, RiskLevel, WeatherModel  # noqa: E402
from src.infrastructure.config import Settings  # noqa: E402
from src.infrastructure.database.seed import build_seed, load_watercourses  # noqa: E402
from src.domain.services.regional_thresholds import RegionalRules  # noqa: E402
from src.infrastructure.external_apis import CachedWeatherProvider, OpenMeteoClient  # noqa: E402
from src.infrastructure.external_apis.senapred_client import SenapredClient
from src.infrastructure.external_apis.windy_client import windy_from_settings
from src.infrastructure.geodata import (
    InMemoryAdminAreaLocator,
    InMemoryEmergencyServiceFinder,
    InMemoryLocalityFinder,
    chile_comunas,
    chile_localities,
    load_municipal_phones,
    load_services,
)  # noqa: E402
from src.infrastructure.notifications import WebPushAdapter  # noqa: E402
from src.infrastructure.notifications.generate_vapid import generate_vapid_keys  # noqa: E402
from src.presentation.api.container import Container  # noqa: E402
from src.presentation.api.main import create_app  # noqa: E402
from tests.fakes import (  # noqa: E402
    FakeAlerts,
    FakeNotificationLog,
    FakeReports,
    FakeSafePoints,
    FakeSubscribers,
    FakeWatercourses,
    FakeWeather,
    FakeZones,
)

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000
SIMULATED = "--simulado" in sys.argv
RAIN_BY_LEVEL = {"VERDE": 2.0, "AMARILLO": 28.0, "NARANJA": 52.0, "ROJO": 95.0}
MODEL_LABELS = {
    WeatherModel.ECMWF_IFS025: "ECMWF (Centro Europeo)",
    WeatherModel.ICON_SEAMLESS: "ICON (DWD, Alemania)",
    WeatherModel.GFS_SEAMLESS: "GFS (NOAA, EE. UU.)",
}

PRIVATE_KEY, PUBLIC_KEY = generate_vapid_keys()
SIM_WEATHER = FakeWeather(precip_mm=RAIN_BY_LEVEL["VERDE"])
STATE: dict[str, Any] = {}


async def demo_container(settings: Settings) -> Container:
    zones_seed, referential_courses, points = build_seed()
    courses = load_watercourses(referential_courses)  # red real de OpenStreetMap si fue importada
    zones, alerts, subscribers = FakeZones(zones_seed), FakeAlerts(), FakeSubscribers()
    gateway = WebPushAdapter(PRIVATE_KEY, settings.vapid_subject)
    meteo = OpenMeteoClient.from_settings(settings)
    weather: WeatherForecastProvider = SIM_WEATHER if SIMULATED else CachedWeatherProvider(meteo, ttl_s=900)
    engine = ConsensusEngineService()
    dispatcher = DispatchAlertsUseCase(
        subscribers=subscribers, zones=zones, log=FakeNotificationLog(), gateway=gateway, cooldown=CooldownPolicy(),
        watercourses=FakeWatercourses(courses),
    )
    STATE.update(
        zones=zones, alerts=alerts, subscribers=subscribers, gateway=gateway, seed=zones_seed,
        courses=courses, meteo=meteo, weather=weather, engine=engine,
    )

    async def health() -> dict[str, bool]:
        return {"mongo": True, "web_push": True}

    windy = windy_from_settings(settings)
    senapred = SenapredClient(httpx.AsyncClient(timeout=httpx.Timeout(20.0)))
    return Container(
        evaluate_gps_risk=EvaluateGpsRiskUseCase(
            zones=zones, alerts=alerts, watercourses=FakeWatercourses(courses), safe_points=FakeSafePoints(points),
            weather=weather, elevation=meteo, engine=engine, exposure=ExposureAssessmentService(),
            locator=InMemoryAdminAreaLocator(chile_comunas()), regional=RegionalRules(), discharge=meteo,
            ensemble=meteo,
            services=InMemoryEmergencyServiceFinder(load_services()),
            municipal_phones=load_municipal_phones(),
            localities=InMemoryLocalityFinder(chile_localities()),
            official=senapred,
        ),
        process_crowdsourcing=ProcessCrowdsourcingUseCase(
            reports=FakeReports(), zones=zones, alerts=alerts, validator=CrowdValidationService(), dispatcher=dispatcher
        ),
        dispatch_alerts=dispatcher,
        register_subscriber=RegisterSubscriberUseCase(subscribers=subscribers, zones=zones),
        get_zones_status=GetZonesStatusUseCase(zones=zones, alerts=alerts),
        get_map_layers=GetMapLayersUseCase(
            zones=zones, alerts=alerts, watercourses=FakeWatercourses(courses), safe_points=FakeSafePoints(points)
        ),
        monitor_zones=MonitorZonesUseCase(
            zones=zones, alerts=alerts, weather=weather, engine=engine, dispatcher=dispatcher,
            ensemble=meteo,
            watercourses=FakeWatercourses(courses),
        ),
        region_overview=RegionOverviewUseCase(areas=chile_comunas(), provider=meteo, official=senapred),
        windy_study=WindyStudyUseCase(windy),
        health_check=health,
        closers=[meteo.aclose, *([windy.aclose] if windy else [])],
    )


# ---------------------------------------------------------------- datos oficiales por zona


def _river_point(zone: GeoZone, courses: list[Watercourse]) -> tuple[str, Any] | None:
    """Punto del río con nombre más cercano al centro de la zona (punto de consulta GloFAS)."""
    rivers = [c for c in courses if c.kind is WatercourseKind.RIO and not c.name.endswith("sin nombre")]
    if not rivers:
        return None
    best = min(((c, *c.geometry.nearest_point(zone.centroid)) for c in rivers), key=lambda m: m[2])
    return best[0].name, best[1]


async def zone_report(zone: GeoZone) -> dict[str, Any]:
    weather: WeatherForecastProvider = STATE["weather"]
    engine: ConsensusEngineService = STATE["engine"]
    meteo: OpenMeteoClient = STATE["meteo"]
    report: dict[str, Any] = {"zona": zone.name, "comuna": zone.commune}
    try:
        forecasts, conditions = await asyncio.gather(
            weather.get_precipitation_forecasts(zone.centroid, list(WeatherModel), 24),
            weather.get_hydrological_conditions(zone.centroid, 24),
        )
        result = engine.evaluate(forecasts, conditions)
        report.update(
            modelos=[{"modelo": MODEL_LABELS[f.model], "mm": f.accumulated_precipitation_mm} for f in forecasts],
            consenso_mm=result.weighted_precipitation_mm,
            isoterma_m=round(conditions.freezing_level_m),
            humedad_suelo=round(conditions.soil_moisture_m3m3, 3),
            cape=round(conditions.cape_j_kg),
            nivel=result.risk_level.value,
            resultado=result.outcome.value,
            descartes=[d.value for d in result.discard_reasons],
            confianza=result.confidence,
            resumen=result.summary,
        )
    except (DomainError, ValueError) as exc:
        report["error_pronostico"] = str(exc)

    river = _river_point(zone, STATE["courses"])
    if river and not SIMULATED:
        name, point = river
        try:
            flow = await meteo.get_river_discharge(point, forecast_days=7)
            report["rio"] = {
                "nombre": name,
                "hoy_m3s": round(flow.discharge_m3s[0], 1),
                "pico_7d_m3s": round(flow.peak_discharge_m3s, 1),
                "fecha_pico": flow.peak_date.isoformat(),
            }
        except DomainError as exc:
            report["rio"] = {"nombre": name, "error": str(exc)}
    return report


# ---------------------------------------------------------------- panel

demo = APIRouter(include_in_schema=False)

PANEL_HTML = """<!doctype html>
<html lang="es-CL"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SAT-Maule · Datos en vivo</title>
<link rel="stylesheet" href="/styles.css">
<link rel="stylesheet" href="/demo.css">
<script type="module" src="/demo.js"></script>
</head><body data-level="SIN_DATOS"><main>
<section class="card">
  <h1 id="title">Datos oficiales en vivo</h1>
  <p class="muted" id="intro"></p>
  <p><a class="btn-small" href="/" target="_blank" rel="noopener">Abrir la app ↗</a>
     <button id="refresh" class="btn-small" type="button">Evaluar zonas ahora</button></p>
  <p id="stamp" class="muted small"></p>
</section>
<section id="sim" class="card" hidden>
  <h2>Lluvia simulada (24 h)</h2>
  <div class="report-buttons" style="grid-template-columns:repeat(2,1fr)">
    <button class="btn-report" data-observed="NORMAL" data-nivel="VERDE">VERDE · 2 mm</button>
    <button class="btn-report" data-observed="ALTO" data-nivel="AMARILLO">AMARILLO · 28 mm</button>
    <button class="btn-report" data-observed="ALTO" data-nivel="NARANJA">NARANJA · 52 mm</button>
    <button class="btn-report" data-observed="DESBORDE" data-nivel="ROJO">ROJO · 95 mm</button>
  </div>
</section>
<div id="zones" class="zone-grid"><p class="muted">Consultando fuentes oficiales…</p></div>
<section class="card">
  <h2>Notificación push de prueba</h2>
  <p class="muted">Primero active la protección en la app y acepte las notificaciones.</p>
  <button id="push" class="btn-primary" type="button">Enviar alerta de prueba a mis dispositivos</button>
  <p id="push-out" class="status-line"></p>
</section>
<p class="muted small">Fuentes: Open-Meteo (modelos ECMWF IFS 0.25°, DWD ICON, NOAA GFS) ·
Caudales: Copernicus GloFAS. Pronósticos para las próximas 24 horas en el centro de cada zona.</p>
</main></body></html>"""

PANEL_CSS = """
.zone-grid { display: grid; gap: 16px; }
.zcard { border-left: 10px solid var(--zc, #6b7280); }
.zcard h2 { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.zlevel { font-weight: 900; color: var(--zc); }
.kv { display: grid; grid-template-columns: 1fr auto; gap: 4px 16px; margin: 8px 0; }
.kv dt { color: var(--muted); } .kv dd { margin: 0; font-weight: 700; text-align: right; }
.models { list-style: none; padding: 0; margin: 8px 0; display: grid; gap: 4px; }
.models li { display: flex; justify-content: space-between; gap: 8px; }
.summary { margin-top: 10px; padding: 10px 12px; border-radius: 10px; background: var(--surface-2); }
"""

PANEL_JS = """
const COLORS = { VERDE: '#22c55e', AMARILLO: '#facc15', NARANJA: '#fb923c', ROJO: '#ef4444' };
const OUTCOME = { ALERTA_DESBORDE: 'Alerta de desborde', AVISO_TORMENTA_ELECTRICA: 'Aviso de tormenta eléctrica',
                  REPORTE_TRANQUILIDAD: 'Reporte de tranquilidad' };
const DISCARD = { ISOTERMA_BAJA: 'isoterma bajo 1850 m (nieve en altura)', SUELO_SECO: 'suelo seco (infiltra)' };
const $ = (id) => document.getElementById(id);
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
const fmt = (v, d = 1) => Number(v).toLocaleString('es-CL', { maximumFractionDigits: d });

function kv(pairs) {
  const dl = el('dl', null, 'kv');
  for (const [k, v] of pairs) { dl.append(el('dt', k), el('dd', v)); }
  return dl;
}

function zoneCard(z) {
  const card = el('section', null, 'card zcard');
  card.style.setProperty('--zc', COLORS[z.nivel] ?? '#6b7280');
  const h = el('h2', null);
  h.append(el('span', `${z.zona} · ${z.comuna}`), el('span', z.nivel ?? 'SIN DATOS', 'zlevel'));
  card.append(h);
  if (z.error_pronostico) { card.append(el('p', 'Pronóstico no disponible: ' + z.error_pronostico, 'muted')); }
  else {
    card.append(el('h3', 'Lluvia próximas 24 h por modelo'));
    const ul = el('ul', null, 'models');
    for (const m of z.modelos) { const li = el('li'); li.append(el('span', m.modelo), el('strong', fmt(m.mm) + ' mm')); ul.append(li); }
    card.append(ul);
    card.append(kv([
      ['Consenso ponderado', fmt(z.consenso_mm) + ' mm'],
      ['Isoterma 0 °C (máx.)', fmt(z.isoterma_m, 0) + ' m'],
      ['Humedad del suelo', fmt(z.humedad_suelo, 3) + ' m³/m³'],
      ['Energía de tormenta (CAPE)', fmt(z.cape, 0) + ' J/kg'],
      ['Confianza del consenso', Math.round(z.confianza * 100) + ' %'],
      ['Resultado', OUTCOME[z.resultado] ?? z.resultado],
    ]));
    if (z.descartes.length) card.append(el('p', 'Filtro hidrológico: ' + z.descartes.map(d => DISCARD[d] ?? d).join('; '), 'muted'));
  }
  if (z.rio) {
    card.append(el('h3', 'Caudal pronosticado (GloFAS) · ' + z.rio.nombre));
    if (z.rio.error) card.append(el('p', z.rio.error, 'muted'));
    else card.append(kv([['Hoy', fmt(z.rio.hoy_m3s) + ' m³/s'],
                         ['Máximo próximos 7 días', `${fmt(z.rio.pico_7d_m3s)} m³/s (${new Date(z.rio.fecha_pico + 'T12:00').toLocaleDateString('es-CL', { weekday: 'short', day: 'numeric', month: 'short' })})`]]));
  }
  if (z.resumen) card.append(el('p', z.resumen, 'summary'));
  return card;
}

async function load(runMonitor = false) {
  $('zones').replaceChildren(el('p', runMonitor ? 'Evaluando zonas y despachando alertas…' : 'Consultando fuentes oficiales…', 'muted'));
  if (runMonitor) await fetch('/demo/monitor', { method: 'POST' });
  const r = await fetch('/demo/datos');
  const data = await r.json();
  $('zones').replaceChildren(...data.zonas.map(zoneCard));
  $('stamp').textContent = 'Consultado: ' + new Date(data.consultado).toLocaleString('es-CL');
  $('intro').textContent = data.simulado
    ? 'MODO SIMULADO: la lluvia la define usted con los botones. El resto del sistema es real.'
    : 'Pronósticos reales para el centro de cada zona piloto. El monitor evalúa las zonas cada 30 minutos y emite alertas si corresponde.';
  $('title').textContent = data.simulado ? 'Demostración (lluvia simulada)' : 'Datos oficiales en vivo';
  $('sim').hidden = !data.simulado;
}

$('refresh').addEventListener('click', () => load(true));
for (const b of document.querySelectorAll('[data-nivel]')) {
  b.addEventListener('click', async () => { await fetch('/demo/clima?nivel=' + b.dataset.nivel, { method: 'POST' }); load(); });
}
$('push').addEventListener('click', async () => {
  $('push-out').textContent = 'Enviando…';
  const r = await (await fetch('/demo/push', { method: 'POST' })).json();
  $('push-out').textContent = r.error ?? `Enviada a ${r.dispositivos} dispositivo(s): ${r.resultados.join(', ')}`;
});
load();
"""


@demo.get("/demo")
async def panel() -> HTMLResponse:
    return HTMLResponse(PANEL_HTML)


@demo.get("/demo.js")
async def panel_js() -> Response:
    return Response(PANEL_JS, media_type="text/javascript")


@demo.get("/demo.css")
async def panel_css() -> Response:
    return Response(PANEL_CSS, media_type="text/css")


@demo.get("/demo/datos")
async def official_data() -> dict[str, Any]:
    from src.domain.entities.base import utc_now

    zones: list[GeoZone] = STATE["seed"]
    reports = await asyncio.gather(*(zone_report(z) for z in zones))
    return {"consultado": utc_now().isoformat(), "simulado": SIMULATED, "zonas": reports}


@demo.post("/demo/monitor")
async def run_monitor() -> dict[str, Any]:
    container: Container = app.state.container
    summary = await container.monitor_zones.execute()
    return {z.zone_name: f"{z.risk_level} ({z.action})" for z in summary.zones}


@demo.post("/demo/clima")
async def set_weather(nivel: str = Query(pattern="^(VERDE|AMARILLO|NARANJA|ROJO)$")) -> dict[str, Any]:
    if not SIMULATED:
        return {"error": "Disponible sólo con --simulado."}
    SIM_WEATHER.precip_mm = RAIN_BY_LEVEL[nivel]
    STATE["alerts"].items.clear()
    for zone in STATE["seed"]:
        await STATE["zones"].save(zone)
    return await run_monitor()


@demo.post("/demo/push")
async def test_push() -> dict[str, Any]:
    active = [s for s in STATE["subscribers"].all() if s.active]
    if not active:
        return {"error": "No hay dispositivos suscritos. Abra la app, toque «Activar protección» y acepte las notificaciones."}
    notification = PushNotification(
        title="PRUEBA · SAT-Maule",
        body="Notificación de prueba. En una alerta ROJA real: evacúe hacia terreno alto.",
        risk_level=RiskLevel.ROJO,
        tag="demo",
        url="/?alerta=demo",
        ttl_seconds=600,
    )
    results = await STATE["gateway"].send_many([s.subscription for s in active], notification)
    return {"dispositivos": len(active), "resultados": [r.status.value for r in results]}


app = create_app(
    Settings(
        vapid_private_key=PRIVATE_KEY,  # type: ignore[arg-type]
        vapid_public_key=PUBLIC_KEY,
        reporter_hash_secret=secrets.token_urlsafe(24),  # type: ignore[arg-type]
        # Con datos reales el monitor corre solo (primera pasada a los 15 s, luego cada 30 min).
        monitor_interval_min=0 if SIMULATED else 30,
    ),
    container_factory=demo_container,
)
# Las rutas de la demo deben ir antes del montaje estático de la PWA en "/".
app.router.routes[:0] = demo.routes


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mode = "LLUVIA SIMULADA" if SIMULATED else "DATOS REALES (Open-Meteo / GloFAS)"
    print(f"\n  SAT-Maule DEMO · {mode}\n  App: http://localhost:{PORT}    Panel: http://localhost:{PORT}/demo\n")
    if "--no-browser" not in sys.argv:
        webbrowser.open(f"http://localhost:{PORT}/demo")
        webbrowser.open(f"http://localhost:{PORT}/")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")

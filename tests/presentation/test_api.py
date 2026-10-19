"""Pruebas de la API FastAPI con un contenedor de dependencias en memoria."""

from collections.abc import Iterator, Sequence

import pytest
from fastapi.testclient import TestClient

from src.application.use_cases import (
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
from src.domain.services import (
    ConsensusEngineService,
    CooldownPolicy,
    CrowdValidationService,
    ExposureAssessmentService,
)
from src.domain.value_objects.admin_area import AdminArea
from src.domain.value_objects.coordinates import Coordinates, GeoPolygon
from src.domain.value_objects.forecast import PointOutlook
from src.infrastructure.config import Settings
from src.infrastructure.database.seed import build_seed
from src.presentation.api.container import Container
from src.presentation.api.main import create_app
from tests.fakes import (
    FakeAlerts,
    FakeElevation,
    FakeGateway,
    FakeNotificationLog,
    FakeReports,
    FakeSafePoints,
    FakeSubscribers,
    FakeWatercourses,
    FakeWeather,
    FakeZones,
)

ZONES, COURSES, POINTS = build_seed()


class FakeOutlooks:
    """Talca con 45 mm (NARANJA en la macrozona Centro); el resto, seco."""

    async def get_outlooks(self, points: Sequence[Coordinates], horizon_hours: int = 24) -> Sequence[PointOutlook]:
        return [
            PointOutlook(rain_mm=45.0 if i == 0 else 0.0, freezing_level_m=3000.0, max_wind_gust_kmh=20.0)
            for i, _ in enumerate(points)
        ]


def _square(lat: float, lon: float) -> GeoPolygon:
    return GeoPolygon(
        ring=(
            Coordinates(latitude=lat, longitude=lon),
            Coordinates(latitude=lat + 0.1, longitude=lon),
            Coordinates(latitude=lat + 0.1, longitude=lon + 0.1),
            Coordinates(latitude=lat, longitude=lon + 0.1),
        )
    )


AREAS = [
    AdminArea(comuna="Talca", region="Maule", region_code="CL-ML", parts=(_square(-35.45, -71.7),)),
    AdminArea(comuna="Curicó", region="Maule", region_code="CL-ML", parts=(_square(-35.0, -71.3),)),
]


async def fake_container(settings: Settings) -> Container:
    zones, alerts, subscribers = FakeZones(ZONES), FakeAlerts(), FakeSubscribers()
    dispatcher = DispatchAlertsUseCase(
        subscribers=subscribers, zones=zones, log=FakeNotificationLog(), gateway=FakeGateway(), cooldown=CooldownPolicy()
    )

    async def health() -> dict[str, bool]:
        return {"mongo": True, "web_push": False}

    return Container(
        evaluate_gps_risk=EvaluateGpsRiskUseCase(
            zones=zones, alerts=alerts, watercourses=FakeWatercourses(COURSES), safe_points=FakeSafePoints(POINTS),
            weather=FakeWeather(precip_mm=45.0), elevation=FakeElevation(user_m=22, channel_m=20),
            engine=ConsensusEngineService(), exposure=ExposureAssessmentService(),
        ),
        process_crowdsourcing=ProcessCrowdsourcingUseCase(
            reports=FakeReports(), zones=zones, alerts=alerts, validator=CrowdValidationService(), dispatcher=dispatcher
        ),
        dispatch_alerts=dispatcher,
        register_subscriber=RegisterSubscriberUseCase(subscribers=subscribers, zones=zones),
        get_zones_status=GetZonesStatusUseCase(zones=zones, alerts=alerts),
        get_map_layers=GetMapLayersUseCase(
            zones=zones, alerts=alerts, watercourses=FakeWatercourses(COURSES), safe_points=FakeSafePoints(POINTS)
        ),
        monitor_zones=MonitorZonesUseCase(
            zones=zones, alerts=alerts, weather=FakeWeather(precip_mm=45.0),
            engine=ConsensusEngineService(), dispatcher=dispatcher,
        ),
        region_overview=RegionOverviewUseCase(areas=AREAS, provider=FakeOutlooks()),
        windy_study=WindyStudyUseCase(None),
        health_check=health,
    )


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"reporter_hash_secret": "test-secret", "monitor_interval_min": 0}
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(make_settings(), container_factory=fake_container)
    with TestClient(app) as c:
        yield c


def test_gps_check(client: TestClient) -> None:
    r = client.get("/api/gps-check", params={"lat": -34.98, "lon": -72.0})
    assert r.status_code == 200
    body = r.json()
    assert body["personal_risk_level"] == "ROJO"
    assert body["exposure"] == "CRITICA"
    assert body["consensus"]["outcome"] == "ALERTA_DESBORDE"
    assert body["zones"][0]["name"] == "Licantén"
    assert r.headers["cache-control"] == "no-store"


def test_gps_check_validates_query(client: TestClient) -> None:
    assert client.get("/api/gps-check", params={"lat": -95, "lon": -72}).status_code == 422
    assert client.get("/api/gps-check", params={"lat": -35}).status_code == 422


def test_subscribe(client: TestClient) -> None:
    payload = {
        "subscription": {
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
            "expirationTime": None,
            "keys": {"p256dh": "B" * 87, "auth": "a" * 22},
        },
        "location": {"lat": -34.98, "lon": -72.0},
    }
    r = client.post("/api/subscribe", json=payload)
    assert r.status_code == 201
    assert r.json()["created"] is True and len(r.json()["zone_ids"]) == 1
    assert client.post("/api/subscribe", json=payload).json()["created"] is False


def test_report_water_level_and_rate_limit(client: TestClient) -> None:
    payload = {
        "lat": -34.98, "lon": -72.0, "device_id": "device-0123456789abcdef",
        "observed_level": "ALTO", "description": "El Mataquito subió mucho",
    }
    first = client.post("/api/report-water-level", json=payload)
    assert first.status_code == 201
    assert first.json()["corroborated"] is False and first.json()["distinct_reporters"] == 1

    again = client.post("/api/report-water-level", json=payload)
    assert again.status_code == 429
    assert again.headers["retry-after"] == "600"
    assert again.json()["error"] == "RateLimitedError"


def test_zones(client: TestClient) -> None:
    r = client.get("/api/zones")
    assert r.status_code == 200
    assert {z["zone"]["name"] for z in r.json()} == {"Vilches", "Talca", "Cumpeo", "Achibueno", "Licantén"}
    only = client.get("/api/zones", params=[("name", "talca"), ("name", "Licantén")]).json()
    assert {z["zone"]["name"] for z in only} == {"Talca", "Licantén"}


def test_health_and_docs(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "degradado", "mongo": True, "web_push": False}
    assert client.get("/openapi.json").status_code == 200


def test_zones_returns_503_when_database_is_down() -> None:
    async def down_container(settings: Settings) -> Container:
        container = await fake_container(settings)
        container.get_zones_status._zones.down = True  # type: ignore[attr-defined]
        return container

    app = create_app(make_settings(), container_factory=down_container)
    with TestClient(app) as c:
        r = c.get("/api/zones")
    assert r.status_code == 503
    assert r.json()["error"] == "ExternalDataUnavailableError"


def test_push_public_key_requires_configuration() -> None:
    with TestClient(create_app(make_settings(), container_factory=fake_container)) as c:
        assert c.get("/api/push/public-key").status_code == 503
    configured = make_settings(vapid_private_key="priv", vapid_public_key="BPublicKey")
    with TestClient(create_app(configured, container_factory=fake_container)) as c:
        assert c.get("/api/push/public-key").json() == {"public_key": "BPublicKey"}


def test_map_layers(client: TestClient) -> None:
    body = client.get("/api/map").json()
    assert len(body["zones"]) == 5 and len(body["safe_points"]) == 5
    lat, lon = body["zones"][0]["ring"][0]
    assert -37 < lat < -34 and -73 < lon < -70  # pares [lat, lon] para Leaflet


def test_internal_monitor_requires_token() -> None:
    with TestClient(create_app(make_settings(), container_factory=fake_container)) as c:
        assert c.post("/api/internal/monitor").status_code == 404  # deshabilitado sin token
    with TestClient(create_app(make_settings(cron_token="s3cret"), container_factory=fake_container)) as c:
        assert c.post("/api/internal/monitor").status_code == 401
        assert c.post("/api/internal/monitor", headers={"X-Cron-Token": "nope"}).status_code == 401
        ok = c.post("/api/internal/monitor", headers={"X-Cron-Token": "s3cret"})
        assert ok.status_code == 200
        assert {z["action"] for z in ok.json()["zones"]} == {"alerta_creada"}


def test_pwa_is_served_with_correct_headers(client: TestClient) -> None:
    index = client.get("/")
    assert index.status_code == 200 and "SAT-Chile" in index.text
    assert index.headers["cache-control"] == "no-cache"
    csp = index.headers["content-security-policy"]
    assert "frame-src https://embed.windy.com" in csp and "script-src 'self'" in csp

    sw = client.get("/sw.js")
    assert sw.headers["content-type"].startswith("text/javascript")
    assert sw.headers["service-worker-allowed"] == "/"

    assert client.get("/manifest.json").headers["content-type"] == "application/manifest+json"
    for asset in ("/app.js", "/js/api.js", "/styles.css", "/vendor/leaflet/leaflet.js", "/icons/icon-192.png"):
        assert client.get(asset).status_code == 200, asset
    assert "content-security-policy" not in client.get("/docs").headers  # Swagger usa un CDN


def test_service_worker_precaches_existing_files(client: TestClient) -> None:
    import re

    shell = re.search(r"const APP_SHELL = \[(.*?)\];", client.get("/sw.js").text, re.S)
    assert shell is not None
    for path in re.findall(r"'([^']+)'", shell.group(1)):
        assert client.get(path).status_code == 200, f"{path} está en APP_SHELL pero no existe"


def test_cors_preflight(client: TestClient) -> None:
    r = client.options(
        "/api/zones",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_region_overview_orders_comunas_by_risk(client: TestClient) -> None:
    body = client.get("/api/region-overview", params={"region": "CL-ML"}).json()
    assert body["region"] == "Maule"
    assert body["thresholds_mm"] == [20, 40, 70]
    assert [(c["comuna"], c["level"]) for c in body["comunas"]] == [("Talca", "NARANJA"), ("Curicó", "VERDE")]


def test_region_overview_unknown_region_is_404(client: TestClient) -> None:
    assert client.get("/api/region-overview", params={"region": "CL-ZZ"}).status_code == 404


def test_windy_study_without_key_is_501(client: TestClient) -> None:
    response = client.get("/api/windy-study", params={"lat": -35.4, "lon": -71.6})
    assert response.status_code == 501
    assert "SAT_WINDY_POINT_API_KEY" in response.json()["detail"]

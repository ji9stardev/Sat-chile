"""Pruebas de los casos de uso con repositorios en memoria."""

from datetime import UTC, datetime, timedelta

import pytest

from src.application.dto import DataQuality
from src.application.use_cases import (
    DispatchAlertsUseCase,
    EvaluateGpsRiskUseCase,
    GetZonesStatusUseCase,
    ProcessCrowdsourcingUseCase,
    RegisterSubscriberUseCase,
    WaterLevelReportCommand,
)
from src.domain.entities import Alert, AlertKind, ReportStatus, Subscriber, WaterLevelObservation
from src.domain.exceptions import RateLimitedError
from src.domain.services import (
    ConsensusEngineService,
    CooldownPolicy,
    CrowdValidationService,
    ExposureAssessmentService,
    ExposureLevel,
)
from src.domain.value_objects import Coordinates, PushSubscription, RiskLevel, ThreatType
from src.infrastructure.database.seed import build_seed
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

NOW = datetime(2026, 7, 1, 12, tzinfo=UTC)
ZONES, COURSES, POINTS = build_seed()
LICANTEN = next(z for z in ZONES if z.name == "Licantén")
# Punto a ~100 m del trazado referencial del Mataquito.
NEAR_MATAQUITO = Coordinates(latitude=-34.9800, longitude=-72.0000)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def sub(name: str, **kw: object) -> Subscriber:
    return Subscriber(
        subscription=PushSubscription(endpoint=f"https://push.example/{name}", p256dh="B" * 87, auth="a" * 22),
        **kw,  # type: ignore[arg-type]
    )


def make_gps(weather: FakeWeather, elevation: FakeElevation, alerts: FakeAlerts | None = None) -> EvaluateGpsRiskUseCase:
    return EvaluateGpsRiskUseCase(
        zones=FakeZones(ZONES), alerts=alerts or FakeAlerts(), watercourses=FakeWatercourses(COURSES),
        safe_points=FakeSafePoints(POINTS), weather=weather, elevation=elevation,
        engine=ConsensusEngineService(), exposure=ExposureAssessmentService(), clock=Clock(NOW),
    )


# ---------------------------------------------------------------- GPS


async def test_gps_heavy_rain_next_to_river_is_red() -> None:
    diagnosis = await make_gps(FakeWeather(precip_mm=55.0), FakeElevation(user_m=21, channel_m=20)).execute(NEAR_MATAQUITO)
    assert diagnosis.consensus is not None and diagnosis.consensus.risk_level is RiskLevel.NARANJA
    assert diagnosis.watercourse is not None
    assert diagnosis.watercourse.name.startswith("Río Mataquito")
    assert diagnosis.watercourse.distance_km < 0.15
    assert diagnosis.watercourse.relative_height_m == 1.0
    assert diagnosis.exposure is ExposureLevel.CRITICA
    assert diagnosis.personal_risk_level is RiskLevel.ROJO
    assert [z.name for z in diagnosis.zones] == ["Licantén"]
    assert diagnosis.safe_points and diagnosis.safe_points[0].name.startswith("[DEMO]")
    assert diagnosis.data_quality is DataQuality.COMPLETA


async def test_gps_high_terrace_lowers_personal_risk() -> None:
    diagnosis = await make_gps(FakeWeather(precip_mm=55.0), FakeElevation(user_m=60, channel_m=20)).execute(NEAR_MATAQUITO)
    assert diagnosis.exposure is ExposureLevel.BAJA
    assert diagnosis.personal_risk_level is RiskLevel.VERDE  # NARANJA −2


async def test_gps_degrades_gracefully_when_sources_fail() -> None:
    alerts = FakeAlerts()
    alert = Alert(
        zone_id=LICANTEN.id, kind=AlertKind.ALERTA, risk_level=RiskLevel.AMARILLO, threat_type=ThreatType.DESBORDE_RIO,
        title="Alerta", message="Crecida", valid_from=NOW - timedelta(hours=1), valid_until=NOW + timedelta(hours=5),
    )
    alert.activate()
    await alerts.save(alert)

    diagnosis = await make_gps(FakeWeather(fail=True), FakeElevation(fail=True), alerts).execute(NEAR_MATAQUITO)
    assert diagnosis.consensus is None
    assert diagnosis.hazard_level is RiskLevel.AMARILLO  # desde la alerta activa
    assert diagnosis.exposure is ExposureLevel.CRITICA   # altura desconocida → desfavorable
    assert diagnosis.data_quality is DataQuality.PARCIAL
    assert set(diagnosis.missing_sources) == {"pronostico", "elevacion", "pendiente"}
    assert any("pronóstico" in r for r in diagnosis.recommendations)


async def test_gps_outside_coverage() -> None:
    diagnosis = await make_gps(FakeWeather(), FakeElevation()).execute(Coordinates(latitude=-33.45, longitude=-70.66))
    assert not diagnosis.within_maule
    assert diagnosis.zones == () and diagnosis.watercourse is None
    assert diagnosis.personal_risk_level is RiskLevel.VERDE


# ---------------------------------------------------------------- dispatch


def active_alert(level: RiskLevel, valid_from: datetime = NOW) -> Alert:
    alert = Alert(
        zone_id=LICANTEN.id, kind=AlertKind.ALERTA, risk_level=level, threat_type=ThreatType.DESBORDE_RIO,
        title=f"Alerta {level}", message="Crecida del Mataquito", valid_from=valid_from,
        valid_until=valid_from + timedelta(hours=6),
    )
    alert.activate()
    return alert


async def test_dispatch_cooldown_escalation_and_expired_subscriptions() -> None:
    clock = Clock(NOW)
    inside = sub("inside", location=NEAR_MATAQUITO)
    follower = sub("follower", zone_ids=frozenset({LICANTEN.id}))
    picky = sub("picky", zone_ids=frozenset({LICANTEN.id}), min_risk_level=RiskLevel.ROJO)
    gone = sub("gone", zone_ids=frozenset({LICANTEN.id}))
    subscribers = FakeSubscribers([inside, follower, picky, gone])
    gateway = FakeGateway(expired_endpoints=frozenset({"https://push.example/gone"}))
    log = FakeNotificationLog()
    uc = DispatchAlertsUseCase(
        subscribers=subscribers, zones=FakeZones(ZONES), log=log, gateway=gateway,
        cooldown=CooldownPolicy(), clock=clock,
    )

    first = await uc.execute(active_alert(RiskLevel.NARANJA))
    assert (first.candidates, first.delivered, first.expired_subscriptions) == (3, 2, 1)
    assert not (await subscribers.get_by_id(gone.id)).active  # type: ignore[union-attr]
    body_inside = next(n.body for e, n in gateway.sent if e.endswith("inside"))
    assert "DENTRO de Licantén" in body_inside

    clock.now = NOW + timedelta(minutes=5)
    repeat = await uc.execute(active_alert(RiskLevel.NARANJA, valid_from=NOW))
    assert repeat.suppressed_by_cooldown == 2 and repeat.sent == 0

    escalation = await uc.execute(active_alert(RiskLevel.ROJO, valid_from=NOW))
    assert escalation.delivered == 3  # incluye a "picky" (mínimo ROJO)
    assert all(n.title.startswith("⬆ Sube a ROJO") for e, n in gateway.sent[-2:] if not e.endswith("picky"))


async def test_debris_alert_only_reaches_subscribers_near_a_ravine() -> None:
    from src.domain.entities import Watercourse, WatercourseKind
    from src.domain.value_objects.coordinates import GeoLineString

    ravine = Watercourse(
        name="Quebrada", kind=WatercourseKind.QUEBRADA, commune="Licantén", geometry_source="test",
        geometry=GeoLineString(vertices=(
            Coordinates(latitude=-34.9800, longitude=-72.0000), Coordinates(latitude=-34.9790, longitude=-71.9990),
        )),
    )
    by_ravine = sub("by-ravine", zone_ids=frozenset({LICANTEN.id}), location=NEAR_MATAQUITO)
    far = sub("far", zone_ids=frozenset({LICANTEN.id}), location=Coordinates(latitude=-34.9600, longitude=-72.0300))
    unknown = sub("unknown", zone_ids=frozenset({LICANTEN.id}))
    gateway = FakeGateway()
    uc = DispatchAlertsUseCase(
        subscribers=FakeSubscribers([by_ravine, far, unknown]), zones=FakeZones(ZONES), log=FakeNotificationLog(),
        gateway=gateway, cooldown=CooldownPolicy(), clock=Clock(NOW), watercourses=FakeWatercourses([ravine]),
    )
    debris = active_alert(RiskLevel.AMARILLO).model_copy(update={"threat_type": ThreatType.REMOCION_EN_MASA})
    await uc.execute(debris)
    assert {e.rsplit("/", 1)[1] for e, _ in gateway.sent} == {"by-ravine", "unknown"}

    flood = await uc.execute(active_alert(RiskLevel.AMARILLO))
    assert flood.candidates == 3  # el desborde sí llega a toda la zona


# ---------------------------------------------------------------- crowdsourcing


def make_crowd(clock: Clock) -> tuple[ProcessCrowdsourcingUseCase, FakeAlerts, FakeZones, FakeGateway, FakeReports]:
    zones, alerts, reports, gateway = FakeZones(ZONES), FakeAlerts(), FakeReports(), FakeGateway()
    subscribers = FakeSubscribers([sub("vecino", zone_ids=frozenset({LICANTEN.id}))])
    dispatcher = DispatchAlertsUseCase(
        subscribers=subscribers, zones=zones, log=FakeNotificationLog(), gateway=gateway,
        cooldown=CooldownPolicy(), clock=clock,
    )
    uc = ProcessCrowdsourcingUseCase(
        reports=reports, zones=zones, alerts=alerts, validator=CrowdValidationService(),
        dispatcher=dispatcher, clock=clock,
    )
    return uc, alerts, zones, gateway, reports


def command(reporter: str, lon_offset: float = 0.0) -> WaterLevelReportCommand:
    return WaterLevelReportCommand(
        reporter_hash=reporter * 16,
        location=Coordinates(latitude=NEAR_MATAQUITO.latitude, longitude=NEAR_MATAQUITO.longitude + lon_offset),
        observed_level=WaterLevelObservation.DESBORDE,
        description="El agua pasó la ribera",
    )


async def test_third_neighbour_triggers_citizen_alert_and_push() -> None:
    clock = Clock(NOW)
    uc, alerts, zones, gateway, reports = make_crowd(clock)

    first = await uc.execute(command("a"))
    assert not first.corroborated and first.distinct_reporters == 1
    clock.now += timedelta(minutes=3)
    await uc.execute(command("b", 0.005))
    clock.now += timedelta(minutes=3)
    third = await uc.execute(command("c", 0.010))

    assert third.corroborated and third.distinct_reporters == 3
    assert third.alert_id is not None
    alert = await alerts.get_by_id(third.alert_id)
    assert alert is not None and alert.risk_level is RiskLevel.NARANJA  # mayoría DESBORDE
    assert (await zones.get_by_id(LICANTEN.id)).current_risk_level is RiskLevel.NARANJA  # type: ignore[union-attr]
    assert third.dispatch is not None and third.dispatch.delivered == 1
    assert all(r.status is ReportStatus.VERIFICADO for r in reports.all())

    # Un cuarto vecino no duplica la alerta: ya existe una NARANJA activa.
    clock.now += timedelta(minutes=3)
    fourth = await uc.execute(command("d", 0.002))
    assert fourth.corroborated and fourth.alert_id is None
    assert len(alerts.items) == 1


async def test_same_device_is_rate_limited() -> None:
    clock = Clock(NOW)
    uc, *_ = make_crowd(clock)
    await uc.execute(command("a"))
    clock.now += timedelta(minutes=2)
    with pytest.raises(RateLimitedError):
        await uc.execute(command("a"))


# ---------------------------------------------------------------- suscripción y zonas


async def test_register_subscriber_is_idempotent_and_autoresolves_zone() -> None:
    subscribers = FakeSubscribers()
    uc = RegisterSubscriberUseCase(subscribers=subscribers, zones=FakeZones(ZONES))
    s = PushSubscription(endpoint="https://push.example/x", p256dh="B" * 87, auth="a" * 22)

    first = await uc.execute(s, location=NEAR_MATAQUITO)
    again = await uc.execute(s, location=NEAR_MATAQUITO, min_risk_level=RiskLevel.NARANJA)

    assert first.created and not again.created
    assert first.subscriber_id == again.subscriber_id
    assert again.zone_ids == (LICANTEN.id,)
    assert len(subscribers.items) == 1


async def test_zones_status_orders_by_effective_level() -> None:
    alerts = FakeAlerts()
    await alerts.save(active_alert(RiskLevel.ROJO))
    statuses = await GetZonesStatusUseCase(zones=FakeZones(ZONES), alerts=alerts, clock=Clock(NOW)).execute()
    assert [s.zone.name for s in statuses][0] == "Licantén"
    assert statuses[0].effective_risk_level is RiskLevel.ROJO
    assert {s.zone.name for s in statuses} == {"Vilches", "Talca", "Cumpeo", "Achibueno", "Licantén"}

"""Pruebas del monitor periódico de zonas."""

from datetime import UTC, datetime, timedelta

from src.application.use_cases import DispatchAlertsUseCase, MonitorZonesUseCase
from src.domain.entities import Alert, AlertSource, Subscriber, Watercourse, WatercourseKind
from src.domain.services import ConsensusEngineService, CooldownPolicy
from src.domain.value_objects import (
    Coordinates,
    GeoLineString,
    HydrologicalConditions,
    PushSubscription,
    RiskLevel,
    ThreatType,
)
from src.domain.value_objects.forecast import EnsembleMember
from src.infrastructure.database.seed import build_seed
from tests.fakes import (
    FakeAlerts,
    FakeGateway,
    FakeNotificationLog,
    FakeSubscribers,
    FakeWatercourses,
    FakeWeather,
    FakeZones,
)

NOW = datetime(2026, 7, 1, 12, tzinfo=UTC)
ZONES, _, _ = build_seed()
TALCA = next(z for z in ZONES if z.name == "Talca")


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def build(weather: FakeWeather) -> tuple[MonitorZonesUseCase, FakeAlerts, FakeZones, FakeGateway, Clock]:
    clock = Clock()
    zones, alerts, gateway = FakeZones(ZONES), FakeAlerts(), FakeGateway()
    follower = Subscriber(
        subscription=PushSubscription(endpoint="https://push.example/t", p256dh="B" * 87, auth="a" * 22),
        zone_ids=frozenset({TALCA.id}),
    )
    dispatcher = DispatchAlertsUseCase(
        subscribers=FakeSubscribers([follower]), zones=zones, log=FakeNotificationLog(), gateway=gateway,
        cooldown=CooldownPolicy(), clock=clock,
    )
    monitor = MonitorZonesUseCase(
        zones=zones, alerts=alerts, weather=weather, engine=ConsensusEngineService(), dispatcher=dispatcher, clock=clock
    )
    return monitor, alerts, zones, gateway, clock


async def test_creates_then_keeps_then_escalates() -> None:
    weather = FakeWeather(precip_mm=45.0)  # NARANJA
    monitor, alerts, zones, gateway, clock = build(weather)

    first = await monitor.execute()
    assert {r.action for r in first.zones} == {"alerta_creada"}
    assert len(alerts.items) == 5
    assert all(a.source is AlertSource.MOTOR_CONSENSO and a.risk_level is RiskLevel.NARANJA for a in alerts.all())
    assert (await zones.get_by_id(TALCA.id)).current_risk_level is RiskLevel.NARANJA  # type: ignore[union-attr]
    assert len(gateway.sent) == 1  # sólo el seguidor de Talca

    clock.now += timedelta(minutes=10)
    second = await monitor.execute()
    assert {r.action for r in second.zones} == {"alerta_vigente"}
    assert len(alerts.items) == 5 and len(gateway.sent) == 1  # cooldown: sin spam

    weather.precip_mm = 90.0  # ROJO
    clock.now += timedelta(minutes=10)
    third = await monitor.execute()
    assert {r.action for r in third.zones} == {"alerta_escalada"}
    assert all(a.risk_level is RiskLevel.ROJO for a in alerts.all())
    assert gateway.sent[-1][1].title.startswith("⬆ Sube a ROJO")


async def test_dry_weather_and_failures_do_not_create_alerts() -> None:
    monitor, alerts, *_ = build(FakeWeather(precip_mm=1.0))
    calm = await monitor.execute()
    assert {r.risk_level for r in calm.zones} == {RiskLevel.VERDE}
    assert not alerts.items

    monitor, alerts, *_ = build(FakeWeather(fail=True))
    failed = await monitor.execute()
    assert {r.action for r in failed.zones} == {"sin_datos"}
    assert all(r.error for r in failed.zones)


# ---------------------------------------------------------------- aluvión

VILCHES = next(z for z in ZONES if z.name == "Vilches")
QUEBRADA = Watercourse(
    name="Quebrada de prueba", kind=WatercourseKind.QUEBRADA, commune="San Clemente",
    geometry=GeoLineString(vertices=(
        Coordinates(latitude=-35.590, longitude=-71.050), Coordinates(latitude=-35.570, longitude=-71.030),
    )),
    geometry_source="test",
)


def build_debris(weather: FakeWeather) -> tuple[MonitorZonesUseCase, FakeAlerts, FakeGateway, Clock]:
    clock = Clock()
    zones, alerts, gateway = FakeZones(ZONES), FakeAlerts(), FakeGateway()
    follower = Subscriber(
        subscription=PushSubscription(endpoint="https://push.example/v", p256dh="B" * 87, auth="a" * 22),
        zone_ids=frozenset({VILCHES.id}),
    )
    dispatcher = DispatchAlertsUseCase(
        subscribers=FakeSubscribers([follower]), zones=zones, log=FakeNotificationLog(), gateway=gateway,
        cooldown=CooldownPolicy(), clock=clock,
    )
    monitor = MonitorZonesUseCase(
        zones=zones, alerts=alerts, weather=weather, engine=ConsensusEngineService(), dispatcher=dispatcher,
        watercourses=FakeWatercourses([QUEBRADA]), clock=clock,
    )
    return monitor, alerts, gateway, clock


def debris_alerts(alerts: FakeAlerts) -> list[Alert]:
    return [a for a in alerts.all() if a.threat_type is ThreatType.REMOCION_EN_MASA]


async def test_quebrada_alone_does_not_trigger_debris_alert() -> None:
    monitor, alerts, gateway, _ = build_debris(FakeWeather(precip_mm=5.0))
    summary = await monitor.execute()
    vilches = next(r for r in summary.zones if r.zone_name == "Vilches")
    assert vilches.debris_level is RiskLevel.VERDE and vilches.debris_action == "sin_cambios"
    assert not alerts.items and not gateway.sent
    # Zonas sin quebradas no se evalúan por aluvión.
    assert all(r.debris_level is None for r in summary.zones if r.zone_name != "Vilches")


async def test_rain_triggers_and_escalates_debris_alert_only_where_there_are_quebradas() -> None:
    weather = FakeWeather(precip_mm=16.0)  # bajo el umbral de desborde, sobre el de aluvión
    monitor, alerts, gateway, clock = build_debris(weather)

    await monitor.execute()
    (alert,) = debris_alerts(alerts)
    assert alert.zone_id == VILCHES.id and alert.risk_level is RiskLevel.AMARILLO
    assert "aluvión" in alert.title and "16 mm" in alert.message
    assert len(alerts.items) == 1  # ninguna alerta de desborde
    assert len(gateway.sent) == 1 and gateway.sent[0][1].tag.endswith("REMOCION_EN_MASA")

    weather.precip_mm = 55.0
    clock.now += timedelta(minutes=30)
    await monitor.execute()
    (alert,) = debris_alerts(alerts)
    assert alert.risk_level is RiskLevel.ROJO
    assert "Manténgase lejos de quebradas" in alert.message  # al escalar cambian las instrucciones
    assert gateway.sent[-1][1].title.startswith("⬆ Sube a ROJO")


async def test_rain_already_fallen_and_snow_line_are_considered() -> None:
    falling = HydrologicalConditions(freezing_level_m=3000, soil_moisture_m3m3=0.4, recent_precipitation_mm=35.0)
    monitor, alerts, *_ = build_debris(FakeWeather(precip_mm=3.0, conditions=falling))
    await monitor.execute()
    assert [a.risk_level for a in debris_alerts(alerts)] == [RiskLevel.NARANJA]

    snowing = HydrologicalConditions(freezing_level_m=1500, soil_moisture_m3m3=0.4, recent_precipitation_mm=60.0)
    monitor, alerts, *_ = build_debris(FakeWeather(precip_mm=60.0, conditions=snowing))
    await monitor.execute()
    assert not debris_alerts(alerts)


class FakeEnsemble:
    """Corridas con CAPE: ``storms`` de 10 traen tormenta con lluvia en la misma hora."""

    def __init__(self, storms: int) -> None:
        self.storms = storms

    async def get_ensemble(self, location: Coordinates, horizon_hours: int = 24) -> list[EnsembleMember]:
        return [
            EnsembleMember(
                model="gfs025",
                precipitation=(1.0,) * horizon_hours,
                cape=((1200.0 if i < self.storms else 100.0),) * horizon_hours,
            )
            for i in range(10)
        ]


def build_storm(storms: int) -> tuple[MonitorZonesUseCase, FakeAlerts]:
    # Modelo principal: CAPE alto y algo de lluvia => AVISO_TORMENTA_ELECTRICA.
    weather = FakeWeather(
        precip_mm=3.0,
        conditions=HydrologicalConditions(freezing_level_m=3000, soil_moisture_m3m3=0.4, cape_j_kg=1500),
    )
    monitor, alerts, *_ = build(weather)
    monitor._ensemble = FakeEnsemble(storms)  # noqa: SLF001 - mismo cableado que el contenedor
    return monitor, alerts


async def test_storm_warning_needs_ensemble_probability() -> None:
    monitor, alerts = build_storm(storms=1)  # 10 %
    summary = await monitor.execute()
    assert {r.action for r in summary.zones} == {"tormenta_descartada"}
    assert not alerts.items
    assert {r.risk_level for r in summary.zones} == {RiskLevel.VERDE}

    monitor, alerts = build_storm(storms=4)  # 40 %
    summary = await monitor.execute()
    assert {r.action for r in summary.zones} == {"alerta_creada"}
    assert all(a.threat_type is ThreatType.TORMENTA_ELECTRICA for a in alerts.all())

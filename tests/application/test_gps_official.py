"""Alertas de SENAPRED en el diagnóstico personal: sólo fija el nivel la que es de su comuna y de lluvia."""

from __future__ import annotations

from datetime import UTC, datetime

from src.application.use_cases import EvaluateGpsRiskUseCase
from src.domain.services import ConsensusEngineService, ExposureAssessmentService
from src.domain.value_objects import Coordinates, RiskLevel
from src.domain.value_objects.admin_area import AdminArea
from src.domain.value_objects.coordinates import GeoPolygon
from src.domain.value_objects.official_alert import OfficialAlert, OfficialLevel
from tests.fakes import FakeAlerts, FakeElevation, FakeSafePoints, FakeWatercourses, FakeWeather, FakeZones

NOW = datetime(2026, 7, 1, 12, tzinfo=UTC)
POINT = Coordinates(latitude=-33.45, longitude=-70.66)
MAIPU = AdminArea(
    comuna="Maipú", region="Metropolitana de Santiago", region_code="CL-RM",
    parts=(GeoPolygon(ring=(
        Coordinates(latitude=-33.5, longitude=-70.7), Coordinates(latitude=-33.4, longitude=-70.7),
        Coordinates(latitude=-33.4, longitude=-70.6),
    )),),
)


class Locator:
    async def locate(self, point: Coordinates) -> AdminArea:
        return MAIPU


class Official:
    def __init__(self, *alerts: OfficialAlert) -> None:
        self.alerts = alerts

    async def active_alerts(self) -> tuple[OfficialAlert, ...]:
        return self.alerts


def alert(level: OfficialLevel, hazard: str, comunas: tuple[str, ...] = ()) -> OfficialAlert:
    return OfficialAlert(
        id=hazard, title="t", level=level, hazard=hazard, region_codes=("CL-RM",), comunas=comunas,
        scope="Región Metropolitana", published_at=NOW, url=f"https://senapred.cl/alerta/{hazard}",
    )


def make(official: Official, weather: FakeWeather | None = None) -> EvaluateGpsRiskUseCase:
    return EvaluateGpsRiskUseCase(
        zones=FakeZones([]), alerts=FakeAlerts(), watercourses=FakeWatercourses([]), safe_points=FakeSafePoints([]),
        weather=weather or FakeWeather(precip_mm=2.0), elevation=FakeElevation(), engine=ConsensusEngineService(),
        exposure=ExposureAssessmentService(), clock=lambda: NOW, locator=Locator(), official=official,
    )


async def test_alert_naming_the_comuna_sets_the_level() -> None:
    d = await make(Official(alert(OfficialLevel.ROJA, "Remoción en masa", ("Maipú",)))).execute(POINT)
    assert d.personal_risk_level is RiskLevel.ROJO
    assert d.official_alerts[0].sets_level


async def test_region_wide_alert_is_informed_without_painting_the_point() -> None:
    d = await make(Official(alert(OfficialLevel.ROJA, "Evento meteorológico"))).execute(POINT)
    assert d.personal_risk_level is RiskLevel.VERDE
    assert d.official_alerts[0].applies_to == "TODA_REGION" and not d.official_alerts[0].sets_level


async def test_region_wide_alert_rules_when_there_is_no_forecast() -> None:
    d = await make(Official(alert(OfficialLevel.ROJA, "Evento meteorológico")), FakeWeather(fail=True)).execute(POINT)
    assert d.personal_risk_level is RiskLevel.ROJO


async def test_other_hazards_do_not_raise_the_flood_level() -> None:
    d = await make(Official(alert(OfficialLevel.ROJA, "Incendio forestal", ("Maipú",)))).execute(POINT)
    assert d.personal_risk_level is RiskLevel.VERDE
    assert not d.official_alerts[0].hydromet

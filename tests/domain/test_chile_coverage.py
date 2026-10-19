"""Cobertura nacional: comunas, macrozonas, umbrales regionales, pendiente y caudal."""

from datetime import UTC, date, datetime

import pytest

from src.application.dto import FlowTrend
from src.application.use_cases import EvaluateGpsRiskUseCase
from src.domain.entities import Watercourse, WatercourseKind
from src.domain.services import ConsensusEngineService, ExposureAssessmentService
from src.domain.services.landslide import LandslideAssessmentService, Susceptibility, max_slope_deg, ring_points
from src.domain.services.regional_thresholds import RegionalRules
from src.domain.value_objects import Coordinates, GeoLineString, GeoPolygon, RiskLevel, RiverDischargeForecast
from src.domain.value_objects.admin_area import AdminArea, MacroZone, macrozone_for
from src.domain.value_objects.coordinates import simplify_vertices
from src.infrastructure.external_apis.overpass_client import stitch_rings
from src.infrastructure.geodata import InMemoryAdminAreaLocator
from tests.fakes import FakeAlerts, FakeElevation, FakeSafePoints, FakeWatercourses, FakeWeather, FakeZones

COPIAPO = Coordinates(latitude=-27.37, longitude=-70.33)


def square_area(name: str, code: str, lat: float, lon: float, half: float = 0.2) -> AdminArea:
    ring = tuple(
        Coordinates(latitude=lat + dy, longitude=lon + dx) for dx, dy in ((-half, -half), (half, -half), (half, half), (-half, half))
    )
    return AdminArea(comuna=name, region="Región", region_code=code, parts=(GeoPolygon(ring=ring),))


# ---------------------------------------------------------------- geometría


def test_stitch_rings_joins_ways_in_any_direction_and_drops_open_ones() -> None:
    ways = [[(0, 0), (1, 0)], [(1, 1), (1, 0)], [(1, 1), (0, 1), (0, 0)], [(5, 5), (6, 6)]]
    (ring,) = stitch_rings(ways)
    assert ring[0] == ring[-1] and set(ring) == {(0, 0), (1, 0), (1, 1), (0, 1)}


def test_simplify_keeps_shape_and_drops_collinear_points() -> None:
    straight = [Coordinates(latitude=-35.0, longitude=-71 + i * 0.0001) for i in range(50)]
    assert len(simplify_vertices(straight, 5)) == 2
    corner = [*straight, Coordinates(latitude=-34.99, longitude=straight[-1].longitude)]
    assert len(simplify_vertices(corner, 5)) == 3


# ---------------------------------------------------------------- regiones


def test_macrozone_by_region_or_latitude() -> None:
    assert macrozone_for("CL-AT", 0) is MacroZone.NORTE_GRANDE
    assert macrozone_for("CL-LL", 0) is MacroZone.AUSTRAL
    assert macrozone_for(None, -18.5) is MacroZone.NORTE_GRANDE
    assert macrozone_for(None, -35.4) is MacroZone.CENTRO
    assert macrozone_for(None, -53.1) is MacroZone.AUSTRAL


async def test_locator_finds_comuna() -> None:
    locator = InMemoryAdminAreaLocator([square_area("Copiapó", "CL-AT", -27.37, -70.33)])
    found = await locator.locate(COPIAPO)
    assert found is not None and found.comuna == "Copiapó" and found.macrozone is MacroZone.NORTE_GRANDE
    assert await locator.locate(Coordinates(latitude=-40, longitude=-73)) is None


def test_same_rain_alarms_the_north_but_not_the_south() -> None:
    rules = RegionalRules()
    rain = {"rain_24h_mm": 20.0, "freezing_level_m": 3500.0}
    assert rules.debris(MacroZone.NORTE_GRANDE).trigger_level(**rain) is RiskLevel.NARANJA
    assert rules.debris(MacroZone.CENTRO).trigger_level(**rain) is RiskLevel.AMARILLO
    assert rules.debris(MacroZone.SUR).trigger_level(**rain) is RiskLevel.VERDE


# ---------------------------------------------------------------- pendiente


def test_slope_from_ring() -> None:
    ring = ring_points(COPIAPO)
    assert len(ring) == 8 and all(abs(COPIAPO.distance_km(p) - 0.12) < 0.002 for p in ring)
    assert max_slope_deg(100, [100] * 8) == 0
    assert max_slope_deg(100, [100 + 120] + [100] * 7) == pytest.approx(45)


@pytest.mark.parametrize(
    ("slope", "rain", "susceptibility", "level"),
    [
        (5, 80, Susceptibility.BAJA, RiskLevel.VERDE),     # plano: nunca alerta
        (20, 60, Susceptibility.MEDIA, RiskLevel.NARANJA),  # ROJO por lluvia, −1 por pendiente media
        (35, 60, Susceptibility.ALTA, RiskLevel.ROJO),
        (35, 5, Susceptibility.ALTA, RiskLevel.VERDE),      # ladera empinada sin lluvia: sin alerta
    ],
)
def test_landslide(slope: float, rain: float, susceptibility: Susceptibility, level: RiskLevel) -> None:
    result = LandslideAssessmentService().assess(
        slope_deg=slope, trigger=RegionalRules().debris(MacroZone.CENTRO), rain_24h_mm=rain, freezing_level_m=3000
    )
    assert (result.susceptibility, result.level) == (susceptibility, level)


# ---------------------------------------------------------------- diagnóstico GPS nacional


class FakeDischarge:
    def __init__(self, today: float, peak: float) -> None:
        self.today, self.peak = today, peak

    async def get_river_discharge(self, location: Coordinates, forecast_days: int = 7) -> RiverDischargeForecast:
        return RiverDischargeForecast(
            location=location,
            dates=(date(2026, 10, 18), date(2026, 10, 19), date(2026, 10, 20)),
            discharge_m3s=(self.today, self.peak, self.today),
        )


async def test_gps_anywhere_in_chile_uses_comuna_regional_thresholds_slope_and_flow() -> None:
    river = Watercourse(
        name="Río Copiapó", kind=WatercourseKind.RIO, commune="Atacama", geometry_source="test",
        geometry=GeoLineString(vertices=(
            Coordinates(latitude=-27.36, longitude=-70.40), Coordinates(latitude=-27.38, longitude=-70.30),
        )),
    )
    use_case = EvaluateGpsRiskUseCase(
        zones=FakeZones([]), alerts=FakeAlerts(), watercourses=FakeWatercourses([river]), safe_points=FakeSafePoints([]),
        weather=FakeWeather(precip_mm=18.0), elevation=FakeElevation(user_m=400, channel_m=380, ring_m=470),
        engine=ConsensusEngineService(), exposure=ExposureAssessmentService(),
        locator=InMemoryAdminAreaLocator([square_area("Copiapó", "CL-AT", -27.37, -70.33)]),
        regional=RegionalRules(), discharge=FakeDischarge(today=4.0, peak=12.0),
        clock=lambda: datetime(2026, 10, 18, 12, tzinfo=UTC),
    )
    diagnosis = await use_case.execute(COPIAPO)

    assert diagnosis.within_chile and not diagnosis.within_maule
    assert diagnosis.area is not None and diagnosis.area.comuna == "Copiapó"
    assert diagnosis.macrozone is MacroZone.NORTE_GRANDE
    # 18 mm en el Norte Grande es NARANJA (en el Maule sería AMARILLO); ladera de ~30° → ALTA.
    assert diagnosis.landslide is not None and diagnosis.landslide.susceptibility is Susceptibility.ALTA
    assert diagnosis.landslide.level is RiskLevel.NARANJA
    assert diagnosis.personal_risk_level >= RiskLevel.NARANJA
    assert diagnosis.river_flow is not None and diagnosis.river_flow.trend is FlowTrend.SUBE_FUERTE
    assert any("Río Copiapó" in r and "m³/s" in r for r in diagnosis.recommendations)


class HourlyWeather(FakeWeather):
    """Lluvia por hora creciente y CAPE alto sólo entre la 3.ª y la 5.ª hora."""

    async def get_precipitation_forecasts(self, location, models, horizon_hours=24):  # type: ignore[no-untyped-def]
        from src.domain.value_objects import ModelPrecipitationForecast

        hourly = tuple(float(h) for h in range(horizon_hours))
        return [
            ModelPrecipitationForecast(model=m, accumulated_precipitation_mm=sum(hourly), horizon_hours=horizon_hours, hourly_mm=hourly)
            for m in models
        ]

    async def get_hydrological_conditions(self, location, horizon_hours=24):  # type: ignore[no-untyped-def]
        from src.domain.value_objects import HydrologicalConditions

        cape = tuple(1200.0 if 3 <= h <= 5 else 100.0 for h in range(horizon_hours))
        return HydrologicalConditions(freezing_level_m=3000, soil_moisture_m3m3=0.4, cape_j_kg=1200, cape_hourly=cape)


async def test_gps_returns_hourly_rain_and_storm_window() -> None:
    now = datetime(2026, 10, 18, 14, 37, tzinfo=UTC)
    use_case = EvaluateGpsRiskUseCase(
        zones=FakeZones([]), alerts=FakeAlerts(), watercourses=FakeWatercourses([]), safe_points=FakeSafePoints([]),
        weather=HourlyWeather(), elevation=FakeElevation(), engine=ConsensusEngineService(),
        exposure=ExposureAssessmentService(), clock=lambda: now,
    )
    diagnosis = await use_case.execute(COPIAPO)
    assert len(diagnosis.hourly_rain) == 12
    assert diagnosis.hourly_rain[0].time == datetime(2026, 10, 18, 14, tzinfo=UTC)
    assert [h.precipitation_mm for h in diagnosis.hourly_rain[:3]] == [0.0, 1.0, 2.0]
    assert diagnosis.storm_window is not None
    assert (diagnosis.storm_window.start.hour, diagnosis.storm_window.end.hour) == (17, 20)

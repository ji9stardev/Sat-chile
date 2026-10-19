"""Cauces de OpenStreetMap, ponderación por tipo y riesgo de aluvión."""

from datetime import UTC, datetime

import pytest

from src.application.use_cases import EvaluateGpsRiskUseCase
from src.domain.entities import Watercourse, WatercourseKind
from src.domain.services import (
    ConsensusEngineService,
    DebrisFlowAssessmentService,
    ExposureAssessmentService,
    ExposureLevel,
)
from src.domain.value_objects import Coordinates, GeoLineString, RiskLevel
from src.infrastructure.external_apis.overpass_client import classify, elements_to_watercourses
from tests.fakes import FakeAlerts, FakeElevation, FakeSafePoints, FakeWatercourses, FakeWeather, FakeZones

HOME = Coordinates(latitude=-35.4000, longitude=-71.6000)


@pytest.mark.parametrize(
    ("name", "tag", "expected"),
    [
        ("Quebrada Honda", "stream", WatercourseKind.QUEBRADA),
        ("Estero Piduco", "river", WatercourseKind.ESTERO),  # el nombre manda sobre la etiqueta
        ("Canal Maule Norte", "canal", WatercourseKind.CANAL),
        ("Río Claro", "river", WatercourseKind.RIO),
        (None, "ditch", WatercourseKind.CANAL),
        (None, "stream", WatercourseKind.ESTERO),
    ],
)
def test_osm_classification(name: str | None, tag: str, expected: WatercourseKind) -> None:
    assert classify(name, tag) is expected


def test_overpass_elements_conversion_skips_degenerate_ways() -> None:
    elements = [
        {"type": "way", "id": 1, "tags": {"waterway": "canal"},
         "geometry": [{"lat": -35.4, "lon": -71.6}, {"lat": -35.401, "lon": -71.601}]},
        {"type": "way", "id": 2, "tags": {"waterway": "stream", "name": "Quebrada X"},
         "geometry": [{"lat": -35.4, "lon": -71.6}, {"lat": -35.4000001, "lon": -71.6000001}]},  # se colapsa a 1 punto
        {"type": "node", "id": 3},
    ]
    courses = elements_to_watercourses(elements)
    assert len(courses) == 1
    assert courses[0].name == "Canal sin nombre" and courses[0].kind is WatercourseKind.CANAL
    assert courses[0].geometry_source.startswith("OpenStreetMap")


def test_canal_counts_as_farther_than_river() -> None:
    svc = ExposureAssessmentService()
    assert svc.classify(0.1, 1.0, WatercourseKind.RIO) is ExposureLevel.CRITICA
    assert svc.classify(0.1, 1.0, WatercourseKind.CANAL) is ExposureLevel.ALTA  # 100 m × 4 = 400 m efectivos


@pytest.mark.parametrize(
    ("quebrada", "stream", "elev", "rain", "iso", "susceptible", "level"),
    [
        (None, None, 100, 80, 3000, False, RiskLevel.VERDE),     # sin cauces de montaña
        (0.3, None, 100, 60, 3000, True, RiskLevel.ROJO),         # quebrada + lluvia fuerte
        (None, 0.2, 900, 35, 3000, True, RiskLevel.NARANJA),      # estero de montaña
        (None, 0.2, 150, 80, 3000, False, RiskLevel.VERDE),       # estero de valle: no aluvión
        (0.3, None, 100, 80, 1500, True, RiskLevel.VERDE),        # isoterma baja: nieve
        (0.3, None, 100, None, None, True, RiskLevel.VERDE),      # sin pronóstico
    ],
)
def test_debris_flow(quebrada, stream, elev, rain, iso, susceptible, level) -> None:  # type: ignore[no-untyped-def]
    result = DebrisFlowAssessmentService().assess(
        nearest_quebrada_km=quebrada, nearest_stream_km=stream, elevation_m=elev,
        rain_24h_mm=rain, freezing_level_m=iso,
    )
    assert (result.susceptible, result.level) == (susceptible, level)


def test_debris_flow_counts_rain_already_fallen_and_intensity() -> None:
    service = DebrisFlowAssessmentService()

    def level(rain: float, iso: float = 3000, recent: float | None = None, hourly: float | None = None) -> RiskLevel:
        return service.trigger_level(
            rain_24h_mm=rain, freezing_level_m=iso, recent_rain_24h_mm=recent, max_hourly_mm=hourly
        )

    assert level(5) is RiskLevel.VERDE
    assert level(5, recent=35) is RiskLevel.NARANJA          # temporal en curso, pronóstico restante bajo
    assert level(5, hourly=12) is RiskLevel.AMARILLO         # chubasco intenso basta para avisar
    assert level(32, hourly=12) is RiskLevel.ROJO            # la intensidad sube un nivel
    assert level(80, iso=1500, recent=60, hourly=20) is RiskLevel.VERDE  # nieve en altura

    # Caso real (Vilches, 17-10-2026): sólo GFS ve lluvia fuerte → no se alarma.
    lone_gfs = [(9.3, 0.45), (14.0, 0.30), (28.5, 0.25)]
    assert service.trigger_level(rain_24h_mm=15.5, freezing_level_m=3190, model_rain_mm=lone_gfs) is RiskLevel.VERDE
    agreeing = [(18.0, 0.45), (16.0, 0.30), (20.0, 0.25)]
    assert service.trigger_level(rain_24h_mm=17.8, freezing_level_m=3190, model_rain_mm=agreeing) is RiskLevel.AMARILLO

    result = service.assess(
        nearest_quebrada_km=0.3, nearest_stream_km=None, elevation_m=100,
        rain_24h_mm=5, freezing_level_m=3000, recent_rain_24h_mm=35,
    )
    assert result.level is RiskLevel.NARANJA
    assert "ya cayeron 35 mm" in result.reason


def line(*points: tuple[float, float]) -> GeoLineString:
    return GeoLineString(vertices=tuple(Coordinates(latitude=a, longitude=o) for a, o in points))


async def test_gps_lists_nearby_and_governs_by_weighted_distance() -> None:
    canal = Watercourse(name="Canal de riego", kind=WatercourseKind.CANAL, commune="Talca",
                        geometry=line((-35.4009, -71.61), (-35.4009, -71.59)))       # ~100 m (x4 = 400 m)
    river = Watercourse(name="Río Grande", kind=WatercourseKind.RIO, commune="Talca",
                        geometry=line((-35.4030, -71.61), (-35.4030, -71.59)))       # ~330 m
    quebrada = Watercourse(name="Quebrada Seca", kind=WatercourseKind.QUEBRADA, commune="Talca",
                           geometry=line((-35.3970, -71.61), (-35.3970, -71.59)))    # ~330 m
    far = Watercourse(name="Estero Lejano", kind=WatercourseKind.ESTERO, commune="Talca",
                      geometry=line((-35.50, -71.61), (-35.50, -71.59)))              # ~11 km
    uc = EvaluateGpsRiskUseCase(
        zones=FakeZones(), alerts=FakeAlerts(), watercourses=FakeWatercourses([canal, river, quebrada, far]),
        safe_points=FakeSafePoints(), weather=FakeWeather(precip_mm=55.0), elevation=FakeElevation(102, 100),
        engine=ConsensusEngineService(), exposure=ExposureAssessmentService(),
        clock=lambda: datetime(2026, 7, 1, tzinfo=UTC),
    )
    d = await uc.execute(HOME)
    assert [w.name for w in d.nearby_watercourses] == ["Canal de riego", "Río Grande", "Quebrada Seca"]
    assert d.watercourse is not None and d.watercourse.name in {"Río Grande", "Quebrada Seca"}  # no el canal
    assert d.debris_flow is not None and d.debris_flow.level is RiskLevel.ROJO  # quebrada a 330 m + 55 mm
    assert d.personal_risk_level is RiskLevel.ROJO
    assert d.elevation_m == 102
    assert d.recommendations[0].startswith("Manténgase lejos de cauces")

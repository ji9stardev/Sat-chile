"""Pruebas de Value Objects y entidades."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.domain.entities import Alert, AlertKind, AlertStatus, GeoZone, MauleProvince, SafePoint
from src.domain.exceptions import CapacityExceededError, InvalidStateTransitionError
from src.domain.services import ConsensusEngineService
from src.domain.value_objects import (
    Coordinates,
    GeoPolygon,
    HydrologicalConditions,
    ModelPrecipitationForecast,
    RiskLevel,
    ThreatType,
    WeatherModel,
)

TALCA = Coordinates(latitude=-35.4264, longitude=-71.6554)
CURICO = Coordinates(latitude=-34.9828, longitude=-71.2394)


def test_risk_level_ordering() -> None:
    assert RiskLevel.VERDE < RiskLevel.AMARILLO < RiskLevel.NARANJA < RiskLevel.ROJO
    assert max([RiskLevel.AMARILLO, RiskLevel.ROJO, RiskLevel.VERDE]) is RiskLevel.ROJO
    assert RiskLevel.ROJO.escalate() is RiskLevel.ROJO
    assert RiskLevel.VERDE.deescalate() is RiskLevel.VERDE
    assert RiskLevel.NARANJA.requires_public_alert


def test_coordinates_geojson_roundtrip_and_immutability() -> None:
    geo = TALCA.to_geojson()
    assert geo == {"type": "Point", "coordinates": [-71.6554, -35.4264]}
    assert Coordinates.from_geojson(geo) == TALCA
    assert TALCA.is_within_maule
    with pytest.raises(ValidationError):
        TALCA.latitude = 0  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Coordinates(latitude=-95, longitude=0)


def test_haversine_talca_curico() -> None:
    assert TALCA.distance_km(CURICO) == pytest.approx(62.1, abs=0.5)


def test_polygon_contains_and_closes_ring() -> None:
    square = GeoPolygon(
        ring=(
            Coordinates(latitude=-35.5, longitude=-71.7),
            Coordinates(latitude=-35.5, longitude=-71.6),
            Coordinates(latitude=-35.4, longitude=-71.6),
            Coordinates(latitude=-35.4, longitude=-71.7),
        )
    )
    assert square.ring[0] == square.ring[-1]
    assert square.contains(TALCA)
    assert not square.contains(CURICO)
    assert GeoPolygon.from_geojson(square.to_geojson()) == square


def test_alert_lifecycle_from_consensus() -> None:
    result = ConsensusEngineService().evaluate(
        [
            ModelPrecipitationForecast(model=m, accumulated_precipitation_mm=60)
            for m in WeatherModel
        ],
        HydrologicalConditions(freezing_level_m=3200, soil_moisture_m3m3=0.35),
    )
    now = datetime(2026, 6, 1, 12, tzinfo=UTC)
    alert = Alert.from_consensus(uuid4(), "Talca", result, now=now)
    assert alert.kind is AlertKind.ALERTA
    assert alert.risk_level is RiskLevel.NARANJA
    assert alert.status is AlertStatus.BORRADOR

    alert.activate()
    assert alert.is_active(now + timedelta(hours=1))
    alert.escalate_to(RiskLevel.ROJO)
    with pytest.raises(InvalidStateTransitionError):
        alert.escalate_to(RiskLevel.AMARILLO)
    assert alert.expire_if_due(now + timedelta(hours=25))
    with pytest.raises(InvalidStateTransitionError):
        alert.cancel("tarde")


def test_safe_point_capacity() -> None:
    sp = SafePoint(
        name="Liceo de prueba",
        commune="Talca",
        location=TALCA,
        capacity=10,
        safe_from=frozenset({ThreatType.DESBORDE_RIO}),
    )
    sp.register_arrivals(8)
    assert sp.available_capacity == 2
    with pytest.raises(CapacityExceededError):
        sp.register_arrivals(3)
    assert sp.is_safe_for(ThreatType.DESBORDE_RIO)
    assert not sp.is_safe_for(ThreatType.TSUNAMI)


def test_entity_equality_by_identity() -> None:
    poly = GeoPolygon(
        ring=(
            Coordinates(latitude=-35.5, longitude=-71.7),
            Coordinates(latitude=-35.5, longitude=-71.6),
            Coordinates(latitude=-35.4, longitude=-71.6),
        )
    )
    zone = GeoZone(name="Zona A", commune="Talca", province=MauleProvince.TALCA, boundary=poly)
    clone = zone.model_copy(update={"name": "Otro nombre"})
    assert zone == clone
    assert zone.update_risk(RiskLevel.AMARILLO)
    assert not zone.update_risk(RiskLevel.AMARILLO)

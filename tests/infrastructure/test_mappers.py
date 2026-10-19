"""Pruebas de los mappers entidad ⇄ documento BSON (puros, sin MongoDB)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import bson

from src.domain.entities import (
    Alert,
    AlertKind,
    CitizenReport,
    GeoZone,
    MauleProvince,
    SafePoint,
    Subscriber,
    Telemetry,
    TelemetrySource,
)
from src.domain.value_objects import Coordinates, GeoPolygon, PushSubscription, RiskLevel, ThreatType
from src.infrastructure.database.mappers import (
    ALERT_MAPPER,
    CITIZEN_REPORT_MAPPER,
    GEO_ZONE_MAPPER,
    SAFE_POINT_MAPPER,
    SUBSCRIBER_MAPPER,
    TELEMETRY_MAPPER,
)

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)
TALCA = Coordinates(latitude=-35.43, longitude=-71.66, altitude_m=102.0)
OPTS = bson.CodecOptions(tz_aware=True, uuid_representation=bson.binary.UuidRepresentation.STANDARD)


def roundtrip(mapper, entity):  # type: ignore[no-untyped-def]
    """Codifica a BSON real y decodifica, como lo haría el driver."""
    doc = mapper.to_document(entity)
    decoded = bson.decode(bson.encode(doc, codec_options=OPTS), codec_options=OPTS)
    return doc, mapper.from_document(decoded)


def test_telemetry_location_is_2d_geojson_with_separate_altitude() -> None:
    t = Telemetry(
        station_id="DGA-07359001", source=TelemetrySource.DGA, location=TALCA,
        recorded_at=NOW, river_level_m=2.3,
    )
    doc, back = roundtrip(TELEMETRY_MAPPER, t)
    assert doc["_id"] == t.id and "id" not in doc
    assert doc["location"] == {"type": "Point", "coordinates": [-71.66, -35.43]}
    assert doc["location_altitude_m"] == 102.0
    assert doc["source"] == "DGA"
    assert back.model_dump() == t.model_dump()


def test_alert_derived_severity_is_stripped_on_read() -> None:
    a = Alert(
        zone_id=uuid4(), kind=AlertKind.ALERTA, risk_level=RiskLevel.NARANJA,
        threat_type=ThreatType.DESBORDE_RIO, title="Alerta", message="Crecida",
        valid_from=NOW, valid_until=NOW + timedelta(hours=6),
    )
    doc, back = roundtrip(ALERT_MAPPER, a)
    assert doc["risk_severity"] == 2
    assert back.model_dump() == a.model_dump()


def test_zone_polygon_and_safe_point_sets() -> None:
    poly = GeoPolygon(
        ring=(
            Coordinates(latitude=-35.5, longitude=-71.7),
            Coordinates(latitude=-35.5, longitude=-71.6),
            Coordinates(latitude=-35.4, longitude=-71.6),
        )
    )
    zone = GeoZone(
        name="Ribera Río Claro", commune="Talca", province=MauleProvince.TALCA, boundary=poly,
        exposed_threats=frozenset({ThreatType.DESBORDE_RIO}),
    )
    doc, back = roundtrip(GEO_ZONE_MAPPER, zone)
    assert doc["boundary"]["type"] == "Polygon"
    assert back.model_dump() == zone.model_dump()

    sp = SafePoint(
        name="Liceo", commune="Talca", location=TALCA, capacity=100, current_occupancy=40,
        safe_from=frozenset({ThreatType.DESBORDE_RIO, ThreatType.TSUNAMI}),
    )
    doc, back = roundtrip(SAFE_POINT_MAPPER, sp)
    assert doc["available_capacity"] == 60
    assert sorted(doc["safe_from"]) == ["DESBORDE_RIO", "TSUNAMI"]
    assert back.model_dump() == sp.model_dump()


def test_report_urls_and_subscriber_without_location() -> None:
    report = CitizenReport(
        reporter_hash="abcdef123456", location=TALCA, threat_type=ThreatType.INUNDACION,
        description="Agua sobre la calzada", photo_urls=("https://img.example/1.jpg",), reported_at=NOW,
    )
    doc, back = roundtrip(CITIZEN_REPORT_MAPPER, report)
    assert doc["photo_urls"] == ["https://img.example/1.jpg"]
    assert back.model_dump() == report.model_dump()

    sub = Subscriber(
        subscription=PushSubscription(endpoint="https://push.example/x", p256dh="B" * 87, auth="a" * 22),
        zone_ids=frozenset({uuid4()}),
    )
    doc, back = roundtrip(SUBSCRIBER_MAPPER, sub)
    assert "location" not in doc  # omitido: el índice 2dsphere disperso lo ignora
    assert back.model_dump() == sub.model_dump()

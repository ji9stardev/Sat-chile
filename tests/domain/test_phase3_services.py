"""Pruebas de los servicios de dominio de la Fase 3."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.domain.entities import CitizenReport, ReportStatus, WaterLevelObservation
from src.domain.services import (
    CooldownPolicy,
    CrowdValidationService,
    ExposureAssessmentService,
    ExposureLevel,
    NotificationDecision,
)
from src.domain.value_objects import Coordinates, GeoLineString, NotificationRecord, RiskLevel, ThreatType

NOW = datetime(2026, 7, 1, 12, tzinfo=UTC)
TALCA = Coordinates(latitude=-35.4264, longitude=-71.6554)


# ---------------------------------------------------------------- geometría


def test_linestring_nearest_point_on_segment() -> None:
    # Eje N-S sobre la longitud -71.70; el punto está 0.01° al este (~0,9 km).
    river = GeoLineString(
        vertices=(Coordinates(latitude=-35.3, longitude=-71.70), Coordinates(latitude=-35.5, longitude=-71.70))
    )
    p = Coordinates(latitude=-35.40, longitude=-71.69)
    nearest, km = river.nearest_point(p)
    assert nearest.latitude == pytest.approx(-35.40, abs=1e-9)
    assert nearest.longitude == pytest.approx(-71.70, abs=1e-9)
    assert km == pytest.approx(0.908, abs=0.01)
    # Más allá del extremo, el más cercano es el vértice final.
    end, _ = river.nearest_point(Coordinates(latitude=-35.6, longitude=-71.70))
    assert end.latitude == pytest.approx(-35.5)


# ---------------------------------------------------------------- exposición


@pytest.mark.parametrize(
    ("distance", "height", "expected"),
    [
        (0.1, 2.0, ExposureLevel.CRITICA),
        (0.1, None, ExposureLevel.CRITICA),   # altura desconocida → desfavorable
        (0.1, 8.0, ExposureLevel.ALTA),
        (0.4, 9.0, ExposureLevel.ALTA),
        (0.4, 15.0, ExposureLevel.MODERADA),
        (1.5, 5.0, ExposureLevel.MODERADA),
        (0.05, 30.0, ExposureLevel.BAJA),     # terraza alta: segura aunque esté cerca
        (3.0, 0.0, ExposureLevel.BAJA),
        (None, None, ExposureLevel.BAJA),     # sin cauce cercano
    ],
)
def test_exposure_classification(distance: float | None, height: float | None, expected: ExposureLevel) -> None:
    assert ExposureAssessmentService().classify(distance, height) is expected


def test_personal_level_combines_hazard_and_exposure() -> None:
    svc = ExposureAssessmentService()
    critical = svc.assess(distance_km=0.1, relative_height_m=1.0, hazard_level=RiskLevel.NARANJA, watercourse_name="Río X")
    assert critical.personal_risk_level is RiskLevel.ROJO
    assert any("Río X" in r for r in critical.recommendations)

    far = svc.assess(distance_km=5.0, relative_height_m=None, hazard_level=RiskLevel.ROJO)
    assert far.personal_risk_level is RiskLevel.AMARILLO

    calm = svc.assess(distance_km=0.05, relative_height_m=0.0, hazard_level=RiskLevel.VERDE)
    assert calm.personal_risk_level is RiskLevel.VERDE


# ---------------------------------------------------------------- crowdsourcing


def report(reporter: str, km_east: float = 0.0, minutes_ago: int = 5, **kw: object) -> CitizenReport:
    return CitizenReport(
        reporter_hash=reporter * 8,
        location=Coordinates(latitude=TALCA.latitude, longitude=TALCA.longitude + km_east / 91.0),
        threat_type=kw.get("threat", ThreatType.DESBORDE_RIO),  # type: ignore[arg-type]
        observed_level=kw.get("level", WaterLevelObservation.ALTO),  # type: ignore[arg-type]
        description="El río está muy alto",
        reported_at=NOW - timedelta(minutes=minutes_ago),
        status=kw.get("status", ReportStatus.PENDIENTE),  # type: ignore[arg-type]
    )


def test_three_distinct_neighbours_within_2km_corroborate() -> None:
    svc = CrowdValidationService()
    new = report("a")
    decision = svc.evaluate(new, [report("b", 1.0), report("c", 1.8, threat=ThreatType.INUNDACION)], NOW)
    assert decision.corroborated
    assert decision.distinct_reporters == 3
    assert decision.suggested_level is RiskLevel.AMARILLO


def test_same_neighbour_repeated_counts_once_and_far_or_old_reports_ignored() -> None:
    svc = CrowdValidationService()
    nearby = [
        report("b", 0.5), report("b", 0.6, minutes_ago=20),   # mismo vecino
        report("c", 3.0),                                    # fuera de 2 km
        report("d", 0.2, minutes_ago=240),                   # fuera de la ventana de 3 h
        report("e", 0.2, status=ReportStatus.DESCARTADO),     # descartado por operador
        report("f", 0.2, level=WaterLevelObservation.NORMAL), # no indica peligro
    ]
    decision = svc.evaluate(report("a"), nearby, NOW)
    assert decision.distinct_reporters == 2
    assert not decision.corroborated


def test_majority_overflow_suggests_orange_and_normal_report_never_corroborates() -> None:
    svc = CrowdValidationService()
    d = WaterLevelObservation.DESBORDE
    decision = svc.evaluate(report("a", level=d), [report("b", 0.3, level=d), report("c", 0.4)], NOW)
    assert decision.suggested_level is RiskLevel.NARANJA

    calm = svc.evaluate(report("a", level=WaterLevelObservation.NORMAL), [report("b"), report("c"), report("d")], NOW)
    assert not calm.corroborated


def test_reporter_rate_limit() -> None:
    svc = CrowdValidationService()
    assert svc.is_rate_limited([report("a", minutes_ago=5)], NOW)
    assert not svc.is_rate_limited([report("a", minutes_ago=15)], NOW)


# ---------------------------------------------------------------- cooldown


def record(level: RiskLevel, minutes_ago: int) -> NotificationRecord:
    return NotificationRecord(
        subscriber_id=uuid4(), zone_id=uuid4(), alert_id=uuid4(), risk_level=level,
        sent_at=NOW - timedelta(minutes=minutes_ago),
    )


@pytest.mark.parametrize(
    ("last", "level", "expected"),
    [
        (None, RiskLevel.AMARILLO, NotificationDecision.PRIMERA),
        (record(RiskLevel.AMARILLO, 1), RiskLevel.ROJO, NotificationDecision.ESCALAMIENTO),
        (record(RiskLevel.ROJO, 5), RiskLevel.ROJO, NotificationDecision.SUPRIMIR),
        (record(RiskLevel.ROJO, 16), RiskLevel.ROJO, NotificationDecision.RECORDATORIO),
        (record(RiskLevel.AMARILLO, 60), RiskLevel.AMARILLO, NotificationDecision.SUPRIMIR),
        (record(RiskLevel.ROJO, 5), RiskLevel.NARANJA, NotificationDecision.SUPRIMIR),
        (record(RiskLevel.ROJO, 11), RiskLevel.NARANJA, NotificationDecision.DESESCALAMIENTO),
    ],
)
def test_cooldown_policy(last: NotificationRecord | None, level: RiskLevel, expected: NotificationDecision) -> None:
    assert CooldownPolicy().decide(last, level, NOW) is expected

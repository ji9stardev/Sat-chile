"""Entidades del dominio SAT-Maule."""

from src.domain.entities.alert import Alert, AlertKind, AlertSource, AlertStatus
from src.domain.entities.base import Entity
from src.domain.entities.citizen_report import CitizenReport, ReportStatus, WaterLevelObservation
from src.domain.entities.geo_zone import GeoZone, MauleProvince
from src.domain.entities.safe_point import SafePoint
from src.domain.entities.subscriber import Subscriber
from src.domain.entities.telemetry import Telemetry, TelemetrySource
from src.domain.entities.watercourse import Watercourse, WatercourseKind

__all__ = [
    "Alert",
    "AlertKind",
    "AlertSource",
    "AlertStatus",
    "CitizenReport",
    "Entity",
    "GeoZone",
    "MauleProvince",
    "ReportStatus",
    "SafePoint",
    "Subscriber",
    "Telemetry",
    "TelemetrySource",
    "WaterLevelObservation",
    "Watercourse",
    "WatercourseKind",
]

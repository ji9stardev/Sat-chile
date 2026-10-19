"""Puertos (interfaces abstractas) del dominio hacia infraestructura."""

from src.domain.repositories.alert_repository import AlertRepository
from src.domain.repositories.base import Repository
from src.domain.repositories.citizen_report_repository import CitizenReportRepository
from src.domain.repositories.geo_zone_repository import GeoZoneRepository
from src.domain.repositories.notification_gateway import (
    NotificationGateway,
    NotificationLogRepository,
    SubscriberRepository,
)
from src.domain.repositories.safe_point_repository import SafePointRepository
from src.domain.repositories.telemetry_repository import TelemetryRepository
from src.domain.repositories.watercourse_repository import WatercourseRepository
from src.domain.repositories.weather_provider import (
    ElevationProvider,
    RiverDischargeProvider,
    WeatherForecastProvider,
)

__all__ = [
    "AlertRepository",
    "CitizenReportRepository",
    "ElevationProvider",
    "GeoZoneRepository",
    "NotificationGateway",
    "NotificationLogRepository",
    "Repository",
    "RiverDischargeProvider",
    "SafePointRepository",
    "SubscriberRepository",
    "TelemetryRepository",
    "WatercourseRepository",
    "WeatherForecastProvider",
]

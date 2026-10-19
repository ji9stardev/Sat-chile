"""Implementaciones MongoDB (Motor) de los repositorios del dominio."""

from src.infrastructure.database.repositories.alert_repository import MongoAlertRepository
from src.infrastructure.database.repositories.base import MongoRepository
from src.infrastructure.database.repositories.citizen_report_repository import (
    MongoCitizenReportRepository,
)
from src.infrastructure.database.repositories.geo_zone_repository import MongoGeoZoneRepository
from src.infrastructure.database.repositories.notification_log_repository import (
    MongoNotificationLogRepository,
)
from src.infrastructure.database.repositories.safe_point_repository import MongoSafePointRepository
from src.infrastructure.database.repositories.subscriber_repository import MongoSubscriberRepository
from src.infrastructure.database.repositories.telemetry_repository import MongoTelemetryRepository
from src.infrastructure.database.repositories.watercourse_repository import MongoWatercourseRepository

__all__ = [
    "MongoAlertRepository",
    "MongoCitizenReportRepository",
    "MongoGeoZoneRepository",
    "MongoNotificationLogRepository",
    "MongoRepository",
    "MongoSafePointRepository",
    "MongoSubscriberRepository",
    "MongoTelemetryRepository",
    "MongoWatercourseRepository",
]

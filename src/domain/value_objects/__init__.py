"""Value Objects inmutables del dominio SAT-Maule."""

from src.domain.value_objects.coordinates import Coordinates, GeoLineString, GeoPolygon
from src.domain.value_objects.forecast import (
    ConsensusOutcome,
    ConsensusResult,
    DiscardReason,
    HydrologicalConditions,
    ModelPrecipitationForecast,
    RiverDischargeForecast,
)
from src.domain.value_objects.notification import (
    DeliveryResult,
    DeliveryStatus,
    NotificationRecord,
    PushNotification,
    PushSubscription,
)
from src.domain.value_objects.risk_level import RiskLevel
from src.domain.value_objects.threat_type import ThreatType, WeatherModel

__all__ = [
    "ConsensusOutcome",
    "ConsensusResult",
    "Coordinates",
    "DeliveryResult",
    "DeliveryStatus",
    "DiscardReason",
    "GeoLineString",
    "GeoPolygon",
    "HydrologicalConditions",
    "ModelPrecipitationForecast",
    "NotificationRecord",
    "PushNotification",
    "PushSubscription",
    "RiskLevel",
    "RiverDischargeForecast",
    "ThreatType",
    "WeatherModel",
]

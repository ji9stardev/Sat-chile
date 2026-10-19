"""Esquemas de entrada de la API (contratos públicos con el frontend).

Las respuestas reutilizan los DTO inmutables de la capa de aplicación.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from src.domain.entities.citizen_report import WaterLevelObservation
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.notification import PushSubscription
from src.domain.value_objects.risk_level import RiskLevel
from src.domain.value_objects.threat_type import ThreatType


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LatLon(_Input):
    """Coordenada WGS84."""

    lat: float = Field(ge=-90, le=90, examples=[-35.4264])
    lon: float = Field(ge=-180, le=180, examples=[-71.6554])

    def to_coordinates(self) -> Coordinates:
        return Coordinates(latitude=self.lat, longitude=self.lon)


class PushKeys(_Input):
    """Claves de ``PushSubscription.toJSON().keys``."""

    p256dh: str = Field(min_length=16)
    auth: str = Field(min_length=8)


class PushSubscriptionIn(_Input):
    """Objeto que entrega ``registration.pushManager.subscribe()`` en el navegador."""

    endpoint: HttpUrl
    keys: PushKeys
    expirationTime: int | None = None  # noqa: N815 - nombre definido por la Push API

    def to_domain(self) -> PushSubscription:
        return PushSubscription(endpoint=self.endpoint, p256dh=self.keys.p256dh, auth=self.keys.auth)


class SubscribeRequest(_Input):
    """Registro del dispositivo de un vecino."""

    subscription: PushSubscriptionIn
    location: LatLon | None = Field(default=None, description="Ubicación base (casa) del vecino, opcional.")
    zone_ids: list[UUID] = Field(default_factory=list, max_length=20)
    min_risk_level: RiskLevel = RiskLevel.AMARILLO


class WaterLevelReportRequest(_Input):
    """Reporte vecinal de crecida."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    device_id: str = Field(
        min_length=16,
        max_length=128,
        description="Identificador aleatorio generado y guardado por la PWA. Se anonimiza con HMAC en el servidor.",
    )
    observed_level: WaterLevelObservation
    threat_type: ThreatType = ThreatType.DESBORDE_RIO
    description: str = Field(min_length=5, max_length=1000)
    photo_urls: list[HttpUrl] = Field(default_factory=list, max_length=5)


class ErrorResponse(BaseModel):
    """Formato uniforme de error."""

    error: str
    detail: str


class HealthResponse(BaseModel):
    """Estado de dependencias."""

    status: str
    mongo: bool
    web_push: bool

"""Value Objects para notificaciones Web Push."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from src.domain.value_objects.risk_level import RiskLevel


class PushSubscription(BaseModel):
    """Suscripción Web Push entregada por el navegador (``PushSubscription.toJSON()``).

    Attributes:
        endpoint: URL del servicio push del navegador (FCM, Mozilla, Apple…).
        p256dh: Clave pública ECDH del cliente (base64url).
        auth: Secreto de autenticación del cliente (base64url).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: HttpUrl
    p256dh: str = Field(min_length=16)
    auth: str = Field(min_length=8)

    def to_webpush_info(self) -> dict[str, Any]:
        """Formato esperado por las librerías Web Push."""
        return {"endpoint": str(self.endpoint), "keys": {"p256dh": self.p256dh, "auth": self.auth}}


class PushNotification(BaseModel):
    """Contenido de una notificación push.

    Attributes:
        title: Título visible.
        body: Texto del mensaje.
        risk_level: Nivel de riesgo (define la urgencia de entrega).
        tag: Agrupa notificaciones: una nueva con el mismo tag reemplaza a la anterior.
        url: Ruta a abrir al tocar la notificación.
        ttl_seconds: Tiempo que el servicio push retiene el mensaje si el equipo está offline.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=2000)
    risk_level: RiskLevel
    tag: str = Field(min_length=1, max_length=64)
    url: str = "/"
    ttl_seconds: int = Field(default=6 * 3600, ge=0, le=28 * 24 * 3600)


class DeliveryStatus(StrEnum):
    """Resultado de un intento de entrega."""

    ENTREGADA = "ENTREGADA"
    SUSCRIPCION_EXPIRADA = "SUSCRIPCION_EXPIRADA"
    """El servicio push respondió 404/410: hay que eliminar la suscripción."""
    FALLIDA = "FALLIDA"


class NotificationRecord(BaseModel):
    """Última notificación entregada a un suscriptor para una zona (base del cooldown)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subscriber_id: UUID
    zone_id: UUID
    alert_id: UUID
    risk_level: RiskLevel
    sent_at: datetime


class DeliveryResult(BaseModel):
    """Resultado inmutable del envío a una suscripción."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str
    status: DeliveryStatus
    status_code: int | None = None
    detail: str | None = None

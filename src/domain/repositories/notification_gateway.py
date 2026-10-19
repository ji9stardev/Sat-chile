"""Puertos de notificación y de persistencia de suscriptores."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

from src.domain.entities.subscriber import Subscriber
from src.domain.repositories.base import Repository
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.notification import (
    DeliveryResult,
    NotificationRecord,
    PushNotification,
    PushSubscription,
)
from src.domain.value_objects.risk_level import RiskLevel


@runtime_checkable
class NotificationGateway(Protocol):
    """Puerto de salida para entregar notificaciones a dispositivos."""

    async def send(self, subscription: PushSubscription, notification: PushNotification) -> DeliveryResult:
        """Entrega una notificación. No lanza excepciones por fallos de entrega."""
        ...

    async def send_many(
        self, subscriptions: Sequence[PushSubscription], notification: PushNotification
    ) -> Sequence[DeliveryResult]:
        """Entrega masiva con concurrencia acotada; un resultado por suscripción, en orden."""
        ...


class SubscriberRepository(Repository[Subscriber]):
    """Repositorio de :class:`Subscriber`."""

    @abstractmethod
    async def find_for_zone(self, zone_id: UUID, level: RiskLevel) -> Sequence[Subscriber]:
        """Suscriptores activos que siguen la zona y aceptan ``level``."""

    @abstractmethod
    async def find_near(
        self, center: Coordinates, radius_km: float, level: RiskLevel
    ) -> Sequence[Subscriber]:
        """Suscriptores activos ubicados dentro del radio que aceptan ``level``."""

    @abstractmethod
    async def find_by_endpoint(self, endpoint: str) -> Subscriber | None:
        """Suscriptor dueño del ``endpoint`` (para re-suscripciones idempotentes)."""

    @abstractmethod
    async def deactivate_by_endpoint(self, endpoint: str) -> int:
        """Desactiva suscripciones expiradas. Devuelve cuántas se actualizaron."""


class NotificationLogRepository(ABC):
    """Registro de la última notificación por (suscriptor, zona), base del cooldown."""

    @abstractmethod
    async def get_last_many(
        self, subscriber_ids: Sequence[UUID], zone_id: UUID
    ) -> Mapping[UUID, NotificationRecord]:
        """Último envío por suscriptor para la zona (ausente si nunca se notificó)."""

    @abstractmethod
    async def record_many(self, records: Sequence[NotificationRecord]) -> None:
        """Registra envíos exitosos (reemplaza el último por suscriptor y zona)."""

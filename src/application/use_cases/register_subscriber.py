"""Caso de uso: registro idempotente de un dispositivo para alertas Web Push."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from src.application.dto import SubscriptionResult
from src.domain.entities.subscriber import Subscriber
from src.domain.repositories import GeoZoneRepository, SubscriberRepository
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.notification import PushSubscription
from src.domain.value_objects.risk_level import RiskLevel


class RegisterSubscriberUseCase:
    """Crea o actualiza un suscriptor identificado por su ``endpoint`` Web Push.

    Si el vecino entrega su ubicación base y no elige zonas, se le suscribe
    automáticamente a las zonas que contienen esa ubicación.
    """

    def __init__(self, *, subscribers: SubscriberRepository, zones: GeoZoneRepository) -> None:
        self._subscribers = subscribers
        self._zones = zones

    async def execute(
        self,
        subscription: PushSubscription,
        location: Coordinates | None = None,
        zone_ids: Sequence[UUID] = (),
        min_risk_level: RiskLevel = RiskLevel.AMARILLO,
    ) -> SubscriptionResult:
        chosen = set(zone_ids)
        if not chosen and location is not None:
            chosen = {z.id for z in await self._zones.find_containing(location)}

        existing = await self._subscribers.find_by_endpoint(str(subscription.endpoint))
        subscriber = existing or Subscriber(subscription=subscription)
        subscriber.subscription = subscription
        subscriber.location = location
        subscriber.zone_ids = frozenset(chosen)
        subscriber.min_risk_level = min_risk_level
        subscriber.active = True
        await self._subscribers.save(subscriber)

        return SubscriptionResult(
            subscriber_id=subscriber.id,
            zone_ids=tuple(sorted(subscriber.zone_ids, key=str)),
            min_risk_level=subscriber.min_risk_level,
            created=existing is None,
        )

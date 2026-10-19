"""Repositorio MongoDB de suscriptores Web Push."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorCollection

from src.domain.entities.subscriber import Subscriber
from src.domain.repositories.notification_gateway import SubscriberRepository
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.risk_level import RiskLevel
from src.infrastructure.database.mappers import SUBSCRIBER_MAPPER, point_geometry
from src.infrastructure.database.mongo_client import with_retry
from src.infrastructure.database.repositories.base import MongoRepository, near_sphere


def _accepted_levels(level: RiskLevel) -> list[str]:
    """Niveles mínimos de suscripción que aceptan una alerta de ``level``."""
    return [lvl.value for lvl in RiskLevel if lvl <= level]


class MongoSubscriberRepository(MongoRepository[Subscriber], SubscriberRepository):
    """Suscriptores en la colección ``subscribers`` (perfil REFERENCE)."""

    def __init__(self, collection: AsyncIOMotorCollection[dict[str, Any]]) -> None:
        super().__init__(collection, SUBSCRIBER_MAPPER)

    async def find_for_zone(self, zone_id: UUID, level: RiskLevel) -> Sequence[Subscriber]:
        return await self._find(
            {"zone_ids": zone_id, "active": True, "min_risk_level": {"$in": _accepted_levels(level)}}
        )

    async def find_near(
        self, center: Coordinates, radius_km: float, level: RiskLevel
    ) -> Sequence[Subscriber]:
        return await self._find(
            {
                **near_sphere("location", point_geometry(center), radius_km),
                "active": True,
                "min_risk_level": {"$in": _accepted_levels(level)},
            }
        )

    async def find_by_endpoint(self, endpoint: str) -> Subscriber | None:
        found = await self._find({"subscription.endpoint": endpoint}, limit=1)
        return found[0] if found else None

    async def deactivate_by_endpoint(self, endpoint: str) -> int:
        result = await with_retry(
            lambda: self._collection.update_many(
                {"subscription.endpoint": endpoint}, {"$set": {"active": False}}
            )
        )
        return result.modified_count

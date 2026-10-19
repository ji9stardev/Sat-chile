"""Repositorio MongoDB de alertas."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import DESCENDING

from src.domain.entities.alert import Alert, AlertStatus
from src.domain.repositories.alert_repository import AlertRepository
from src.domain.value_objects.risk_level import RiskLevel
from src.infrastructure.database.mappers import ALERT_MAPPER
from src.infrastructure.database.repositories.base import MongoRepository


class MongoAlertRepository(MongoRepository[Alert], AlertRepository):
    """Alertas en la colección ``alerts`` (perfil CRITICAL)."""

    def __init__(self, collection: AsyncIOMotorCollection[dict[str, Any]]) -> None:
        super().__init__(collection, ALERT_MAPPER)

    async def find_active_by_zone(self, zone_id: UUID, at: datetime) -> Sequence[Alert]:
        return await self._find(
            {
                "zone_id": zone_id,
                "status": AlertStatus.ACTIVA.value,
                "valid_from": {"$lte": at},
                "valid_until": {"$gt": at},
            },
            sort=[("risk_severity", DESCENDING)],
        )

    async def find_active_at_or_above(self, level: RiskLevel, at: datetime) -> Sequence[Alert]:
        return await self._find(
            {
                "status": AlertStatus.ACTIVA.value,
                "risk_severity": {"$gte": level.severity},
                "valid_from": {"$lte": at},
                "valid_until": {"$gt": at},
            },
            sort=[("risk_severity", DESCENDING), ("valid_from", DESCENDING)],
        )

    async def find_history(
        self, zone_id: UUID, since: datetime, until: datetime, limit: int = 100
    ) -> Sequence[Alert]:
        return await self._find(
            {"zone_id": zone_id, "valid_from": {"$gte": since, "$lt": until}},
            sort=[("valid_from", DESCENDING)],
            limit=limit,
        )

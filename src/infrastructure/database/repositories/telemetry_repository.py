"""Repositorio MongoDB de telemetría (con TTL sobre ``recorded_at``)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import BulkWriteError

from src.domain.entities.telemetry import Telemetry
from src.domain.repositories.telemetry_repository import TelemetryRepository
from src.domain.value_objects.coordinates import Coordinates
from src.infrastructure.database.mappers import TELEMETRY_MAPPER, point_geometry
from src.infrastructure.database.mongo_client import with_retry
from src.infrastructure.database.repositories.base import MongoRepository, near_sphere

logger = logging.getLogger(__name__)


class MongoTelemetryRepository(MongoRepository[Telemetry], TelemetryRepository):
    """Lecturas en la colección ``telemetry`` (perfil TELEMETRY)."""

    def __init__(self, collection: AsyncIOMotorCollection[dict[str, Any]]) -> None:
        super().__init__(collection, TELEMETRY_MAPPER)

    async def save_many(self, readings: Sequence[Telemetry]) -> int:
        """Inserción masiva no ordenada: los duplicados o inválidos no frenan al resto (AP)."""
        if not readings:
            return 0
        docs = [self._mapper.to_document(r) for r in readings]
        try:
            result = await with_retry(lambda: self._collection.insert_many(docs, ordered=False))
            return len(result.inserted_ids)
        except BulkWriteError as exc:
            inserted = int(exc.details.get("nInserted", 0))
            logger.warning(
                "Ingesta parcial de telemetría: %d/%d insertadas (%d errores)",
                inserted,
                len(docs),
                len(exc.details.get("writeErrors", [])),
            )
            return inserted

    async def find_by_station(
        self, station_id: str, since: datetime, until: datetime
    ) -> Sequence[Telemetry]:
        return await self._find(
            {"station_id": station_id, "recorded_at": {"$gte": since, "$lt": until}},
            sort=[("recorded_at", ASCENDING)],
        )

    async def latest_by_station(self, station_id: str) -> Telemetry | None:
        found = await self._find(
            {"station_id": station_id}, sort=[("recorded_at", DESCENDING)], limit=1
        )
        return found[0] if found else None

    async def find_near(
        self, center: Coordinates, radius_km: float, since: datetime
    ) -> Sequence[Telemetry]:
        return await self._find(
            {
                **near_sphere("location", point_geometry(center), radius_km),
                "recorded_at": {"$gte": since},
            }
        )

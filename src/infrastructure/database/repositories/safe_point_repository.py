"""Repositorio MongoDB de puntos seguros."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection

from src.domain.entities.safe_point import SafePoint
from src.domain.repositories.safe_point_repository import SafePointRepository
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.threat_type import ThreatType
from src.infrastructure.database.mappers import SAFE_POINT_MAPPER, point_geometry
from src.infrastructure.database.repositories.base import MongoRepository, near_sphere


class MongoSafePointRepository(MongoRepository[SafePoint], SafePointRepository):
    """Puntos seguros en la colección ``safe_points`` (perfil REFERENCE)."""

    def __init__(self, collection: AsyncIOMotorCollection[dict[str, Any]]) -> None:
        super().__init__(collection, SAFE_POINT_MAPPER)

    async def find_nearest(
        self,
        origin: Coordinates,
        threat: ThreatType,
        limit: int = 5,
        max_distance_km: float = 20.0,
    ) -> Sequence[SafePoint]:
        # ``available_capacity`` es un campo derivado que el mapper persiste,
        # ya que ``$nearSphere`` no admite ``$expr`` para comparar campos.
        return await self._find(
            {
                **near_sphere("location", point_geometry(origin), max_distance_km),
                "is_operational": True,
                "safe_from": threat.value,
                "available_capacity": {"$gt": 0},
            },
            limit=limit,
        )

    async def find_by_commune(self, commune: str) -> Sequence[SafePoint]:
        return await self._find({"commune": commune}, sort=[("name", 1)])

    async def list_all(self) -> Sequence[SafePoint]:
        return await self._find({}, sort=[("commune", 1), ("name", 1)])

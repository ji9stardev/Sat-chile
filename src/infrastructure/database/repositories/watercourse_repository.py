"""Repositorio MongoDB de cauces (ríos, esteros, quebradas)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection

from src.domain.entities.watercourse import Watercourse, WatercourseKind
from src.domain.repositories.watercourse_repository import WatercourseRepository
from src.domain.value_objects.coordinates import Coordinates
from src.infrastructure.database.mappers import WATERCOURSE_MAPPER, point_geometry
from src.infrastructure.database.repositories.base import MongoRepository, near_sphere


class MongoWatercourseRepository(MongoRepository[Watercourse], WatercourseRepository):
    """Cauces en la colección ``watercourses`` (perfil REFERENCE)."""

    def __init__(self, collection: AsyncIOMotorCollection[dict[str, Any]]) -> None:
        super().__init__(collection, WATERCOURSE_MAPPER)

    async def find_near(
        self, point: Coordinates, max_distance_km: float, limit: int = 5
    ) -> Sequence[Watercourse]:
        # $nearSphere sobre LineString mide la distancia al punto más cercano de la línea.
        return await self._find(near_sphere("geometry", point_geometry(point), max_distance_km), limit=limit)

    async def list_all(self) -> Sequence[Watercourse]:
        return await self._find({}, sort=[("name", 1)])

    async def list_by_kinds(self, kinds: Sequence[WatercourseKind]) -> Sequence[Watercourse]:
        return await self._find({"kind": {"$in": [k.value for k in kinds]}})

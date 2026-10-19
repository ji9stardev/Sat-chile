"""Repositorio MongoDB de zonas geográficas."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection

from src.domain.entities.geo_zone import GeoZone, MauleProvince
from src.domain.repositories.geo_zone_repository import GeoZoneRepository
from src.domain.value_objects.coordinates import Coordinates
from src.infrastructure.database.mappers import GEO_ZONE_MAPPER, point_geometry
from src.infrastructure.database.repositories.base import MongoRepository


class MongoGeoZoneRepository(MongoRepository[GeoZone], GeoZoneRepository):
    """Zonas en la colección ``geo_zones`` (perfil REFERENCE)."""

    def __init__(self, collection: AsyncIOMotorCollection[dict[str, Any]]) -> None:
        super().__init__(collection, GEO_ZONE_MAPPER)

    async def list_all(self) -> Sequence[GeoZone]:
        return await self._find({}, sort=[("name", 1)])

    async def find_by_province(self, province: MauleProvince) -> Sequence[GeoZone]:
        return await self._find({"province": province.value}, sort=[("name", 1)])

    async def find_containing(self, point: Coordinates) -> Sequence[GeoZone]:
        return await self._find(
            {"boundary": {"$geoIntersects": {"$geometry": point_geometry(point)}}}
        )

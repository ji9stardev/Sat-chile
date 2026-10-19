"""Implementación genérica de :class:`Repository` sobre Motor."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any, Generic, TypeVar
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import ReplaceOne

from src.domain.entities.base import Entity
from src.domain.repositories.base import Repository
from src.infrastructure.database.mappers import EntityMapper
from src.infrastructure.database.mongo_client import with_retry

E = TypeVar("E", bound=Entity)

METERS_PER_KM = 1000.0


class MongoRepository(Repository[E], Generic[E]):
    """CRUD + utilidades de consulta compartidas por todos los repositorios Mongo.

    Recibe la colección y el mapper por inyección de dependencias, de modo
    que puede probarse con colecciones falsas y reutilizarse sin herencia
    profunda (cada subclase sólo agrega sus consultas específicas).
    """

    def __init__(self, collection: AsyncIOMotorCollection[dict[str, Any]], mapper: EntityMapper[E]) -> None:
        self._collection = collection
        self._mapper = mapper

    async def get_by_id(self, entity_id: UUID) -> E | None:
        doc = await with_retry(lambda: self._collection.find_one({"_id": entity_id}))
        return self._mapper.from_document(doc) if doc is not None else None

    async def save(self, entity: E) -> None:
        doc = self._mapper.to_document(entity)
        await with_retry(lambda: self._collection.replace_one({"_id": entity.id}, doc, upsert=True))

    async def upsert_many(self, entities: Sequence[E], chunk: int = 1000) -> None:
        """Upsert por lotes (cargas masivas como la red hidrográfica de Chile)."""
        for start in range(0, len(entities), chunk):
            ops = [
                ReplaceOne({"_id": e.id}, self._mapper.to_document(e), upsert=True)
                for e in entities[start : start + chunk]
            ]
            await with_retry(partial(self._collection.bulk_write, ops, ordered=False))

    async def delete(self, entity_id: UUID) -> bool:
        result = await with_retry(lambda: self._collection.delete_one({"_id": entity_id}))
        return result.deleted_count == 1

    async def _find(
        self,
        query: Mapping[str, Any],
        *,
        sort: Sequence[tuple[str, int]] | None = None,
        limit: int = 0,
    ) -> list[E]:
        """Ejecuta ``find`` con reintentos y mapea los resultados."""

        async def run() -> list[dict[str, Any]]:
            cursor = self._collection.find(dict(query))
            if sort:
                cursor = cursor.sort(list(sort))
            if limit:
                cursor = cursor.limit(limit)
            return await cursor.to_list(length=None)

        return [self._mapper.from_document(d) for d in await with_retry(run)]


def near_sphere(field: str, geometry: Mapping[str, Any], max_distance_km: float) -> dict[str, Any]:
    """Filtro ``$nearSphere`` (resultados ordenados por distancia, en metros)."""
    return {
        field: {
            "$nearSphere": {
                "$geometry": dict(geometry),
                "$maxDistance": max_distance_km * METERS_PER_KM,
            }
        }
    }

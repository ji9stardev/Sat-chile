"""Registro MongoDB de la última notificación por (suscriptor, zona)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import UpdateOne

from src.domain.repositories.notification_gateway import NotificationLogRepository
from src.domain.value_objects.notification import NotificationRecord
from src.infrastructure.database.mappers import to_bson_value
from src.infrastructure.database.mongo_client import with_retry


def _key(subscriber_id: UUID, zone_id: UUID) -> str:
    return f"{subscriber_id}|{zone_id}"


class MongoNotificationLogRepository(NotificationLogRepository):
    """Un documento por par (suscriptor, zona), con TTL sobre ``sent_at``."""

    def __init__(self, collection: AsyncIOMotorCollection[dict[str, Any]]) -> None:
        self._collection = collection

    async def get_last_many(
        self, subscriber_ids: Sequence[UUID], zone_id: UUID
    ) -> Mapping[UUID, NotificationRecord]:
        if not subscriber_ids:
            return {}
        keys = [_key(s, zone_id) for s in subscriber_ids]

        async def run() -> list[dict[str, Any]]:
            return await self._collection.find({"_id": {"$in": keys}}).to_list(length=None)

        records = [NotificationRecord.model_validate({k: v for k, v in d.items() if k != "_id"}) for d in await with_retry(run)]
        return {r.subscriber_id: r for r in records}

    async def record_many(self, records: Sequence[NotificationRecord]) -> None:
        if not records:
            return
        operations = [
            UpdateOne(
                {"_id": _key(r.subscriber_id, r.zone_id)},
                {"$set": to_bson_value(r.model_dump(mode="python"))},
                upsert=True,
            )
            for r in records
        ]
        await with_retry(lambda: self._collection.bulk_write(operations, ordered=False))

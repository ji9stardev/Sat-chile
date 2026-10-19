"""Repositorio MongoDB de reportes ciudadanos."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import ASCENDING

from src.domain.entities.citizen_report import CitizenReport, ReportStatus
from src.domain.repositories.citizen_report_repository import CitizenReportRepository
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.threat_type import ThreatType
from src.infrastructure.database.mappers import CITIZEN_REPORT_MAPPER, point_geometry
from src.infrastructure.database.repositories.base import MongoRepository, near_sphere


class MongoCitizenReportRepository(MongoRepository[CitizenReport], CitizenReportRepository):
    """Reportes en la colección ``citizen_reports`` (perfil CRITICAL)."""

    def __init__(self, collection: AsyncIOMotorCollection[dict[str, Any]]) -> None:
        super().__init__(collection, CITIZEN_REPORT_MAPPER)

    async def find_by_status(self, status: ReportStatus, limit: int = 50) -> Sequence[CitizenReport]:
        return await self._find(
            {"status": status.value}, sort=[("reported_at", ASCENDING)], limit=limit
        )

    async def find_by_reporter(self, reporter_hash: str, since: datetime) -> Sequence[CitizenReport]:
        return await self._find({"reporter_hash": reporter_hash, "reported_at": {"$gte": since}})

    async def find_near(
        self,
        center: Coordinates,
        radius_km: float,
        since: datetime,
        threat_type: ThreatType | None = None,
    ) -> Sequence[CitizenReport]:
        query: dict[str, Any] = {
            **near_sphere("location", point_geometry(center), radius_km),
            "reported_at": {"$gte": since},
        }
        if threat_type is not None:
            query["threat_type"] = threat_type.value
        return await self._find(query)

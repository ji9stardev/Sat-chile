"""Contrato de persistencia de reportes ciudadanos."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from datetime import datetime

from src.domain.entities.citizen_report import CitizenReport, ReportStatus
from src.domain.repositories.base import Repository
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.threat_type import ThreatType


class CitizenReportRepository(Repository[CitizenReport]):
    """Repositorio de :class:`CitizenReport`."""

    @abstractmethod
    async def find_by_status(self, status: ReportStatus, limit: int = 50) -> Sequence[CitizenReport]:
        """Reportes por estado (p. ej. cola de PENDIENTES para operadores)."""

    @abstractmethod
    async def find_by_reporter(self, reporter_hash: str, since: datetime) -> Sequence[CitizenReport]:
        """Reportes recientes de un mismo informante (control de abuso)."""

    @abstractmethod
    async def find_near(
        self,
        center: Coordinates,
        radius_km: float,
        since: datetime,
        threat_type: ThreatType | None = None,
    ) -> Sequence[CitizenReport]:
        """Reportes recientes en un radio, opcionalmente filtrados por amenaza."""

"""Contrato de persistencia de alertas."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from src.domain.entities.alert import Alert
from src.domain.repositories.base import Repository
from src.domain.value_objects.risk_level import RiskLevel


class AlertRepository(Repository[Alert]):
    """Repositorio de :class:`Alert`."""

    @abstractmethod
    async def find_active_by_zone(self, zone_id: UUID, at: datetime) -> Sequence[Alert]:
        """Alertas ACTIVAS y vigentes en ``at`` para la zona."""

    @abstractmethod
    async def find_active_at_or_above(self, level: RiskLevel, at: datetime) -> Sequence[Alert]:
        """Alertas activas con nivel ``>= level`` en toda la región."""

    @abstractmethod
    async def find_history(
        self, zone_id: UUID, since: datetime, until: datetime, limit: int = 100
    ) -> Sequence[Alert]:
        """Historial de comunicados de la zona en un rango (para auditoría y calibración)."""

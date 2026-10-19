"""Contrato de persistencia de puntos seguros."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence

from src.domain.entities.safe_point import SafePoint
from src.domain.repositories.base import Repository
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.threat_type import ThreatType


class SafePointRepository(Repository[SafePoint]):
    """Repositorio de :class:`SafePoint`."""

    @abstractmethod
    async def find_nearest(
        self,
        origin: Coordinates,
        threat: ThreatType,
        limit: int = 5,
        max_distance_km: float = 20.0,
    ) -> Sequence[SafePoint]:
        """Puntos operativos, con cupo y seguros ante ``threat``, ordenados por distancia (``$near``)."""

    @abstractmethod
    async def find_by_commune(self, commune: str) -> Sequence[SafePoint]:
        """Puntos seguros de una comuna."""

    @abstractmethod
    async def list_all(self) -> Sequence[SafePoint]:
        """Todos los puntos seguros (capa del mapa y caché offline)."""

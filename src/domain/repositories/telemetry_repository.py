"""Contrato de persistencia de telemetría (serie temporal)."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from datetime import datetime

from src.domain.entities.telemetry import Telemetry
from src.domain.repositories.base import Repository
from src.domain.value_objects.coordinates import Coordinates


class TelemetryRepository(Repository[Telemetry]):
    """Repositorio de :class:`Telemetry` (en MongoDB: *time-series collection*)."""

    @abstractmethod
    async def save_many(self, readings: Sequence[Telemetry]) -> int:
        """Inserción masiva. Devuelve la cantidad insertada."""

    @abstractmethod
    async def find_by_station(
        self, station_id: str, since: datetime, until: datetime
    ) -> Sequence[Telemetry]:
        """Lecturas de una estación en un rango, ordenadas por ``recorded_at``."""

    @abstractmethod
    async def latest_by_station(self, station_id: str) -> Telemetry | None:
        """Última lectura disponible de la estación."""

    @abstractmethod
    async def find_near(
        self, center: Coordinates, radius_km: float, since: datetime
    ) -> Sequence[Telemetry]:
        """Lecturas recientes dentro de un radio (consulta geoespacial ``2dsphere``)."""

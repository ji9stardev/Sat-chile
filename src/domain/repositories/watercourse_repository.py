"""Contrato de persistencia de cauces."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence

from src.domain.entities.watercourse import Watercourse, WatercourseKind
from src.domain.repositories.base import Repository
from src.domain.value_objects.coordinates import Coordinates


class WatercourseRepository(Repository[Watercourse]):
    """Repositorio de :class:`Watercourse`."""

    @abstractmethod
    async def find_near(
        self, point: Coordinates, max_distance_km: float, limit: int = 5
    ) -> Sequence[Watercourse]:
        """Cauces cuyo eje pasa a menos de ``max_distance_km``, del más cercano al más lejano."""

    @abstractmethod
    async def list_all(self) -> Sequence[Watercourse]:
        """Todos los cauces (puede ser una colección grande: usar con cuidado)."""

    @abstractmethod
    async def list_by_kinds(self, kinds: Sequence[WatercourseKind]) -> Sequence[Watercourse]:
        """Cauces de los tipos indicados (p. ej. sólo ríos para el mapa general)."""

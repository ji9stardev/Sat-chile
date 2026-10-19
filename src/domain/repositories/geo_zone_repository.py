"""Contrato de persistencia de zonas geográficas."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence

from src.domain.entities.geo_zone import GeoZone, MauleProvince
from src.domain.repositories.base import Repository
from src.domain.value_objects.coordinates import Coordinates


class GeoZoneRepository(Repository[GeoZone]):
    """Repositorio de :class:`GeoZone`."""

    @abstractmethod
    async def list_all(self) -> Sequence[GeoZone]:
        """Todas las zonas monitoreadas (insumo del ciclo de evaluación)."""

    @abstractmethod
    async def find_by_province(self, province: MauleProvince) -> Sequence[GeoZone]:
        """Zonas de una provincia."""

    @abstractmethod
    async def find_containing(self, point: Coordinates) -> Sequence[GeoZone]:
        """Zonas cuyo polígono contiene el punto (``$geoIntersects``)."""

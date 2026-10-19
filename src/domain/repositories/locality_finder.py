"""Puerto para nombrar la ubicación con la localidad más cercana."""

from __future__ import annotations

from typing import Protocol

from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.locality import Locality


class LocalityFinder(Protocol):
    async def nearest(self, point: Coordinates) -> tuple[Locality, float] | None:
        """La localidad que mejor nombra el punto y su distancia en km; ``None`` si no hay cerca."""
        ...

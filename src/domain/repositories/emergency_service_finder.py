"""Puerto para encontrar los servicios de emergencia cercanos."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.emergency_service import EmergencyService


class EmergencyServiceFinder(Protocol):
    async def find_nearest(
        self, point: Coordinates, max_km: float = 40.0
    ) -> Sequence[tuple[EmergencyService, float]]:
        """El más cercano de cada tipo, con su distancia en km (orden de :class:`ServiceKind`)."""
        ...

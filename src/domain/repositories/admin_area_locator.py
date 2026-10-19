"""Puerto para identificar la comuna y región de un punto."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.value_objects.admin_area import AdminArea
from src.domain.value_objects.coordinates import Coordinates


@runtime_checkable
class AdminAreaLocator(Protocol):
    """Devuelve la comuna que contiene el punto, o ``None`` (fuera de Chile o sin datos)."""

    async def locate(self, point: Coordinates) -> AdminArea | None:
        ...

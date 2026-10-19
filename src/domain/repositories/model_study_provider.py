"""Puerto hacia el estudio por modelos de Windy."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.model_study import ModelStudy


class ModelStudyProvider(Protocol):
    async def study(self, location: Coordinates) -> Sequence[ModelStudy]:
        """Un resumen por modelo; vacío si ningún modelo responde."""
        ...

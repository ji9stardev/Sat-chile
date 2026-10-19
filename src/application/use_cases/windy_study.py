"""Estudio del punto con los modelos de Windy: segunda opinión independiente de Open-Meteo."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from src.application.dto import WindyStudy
from src.domain.repositories.model_study_provider import ModelStudyProvider
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.model_study import ModelStudy


class WindyNotConfiguredError(RuntimeError):
    """Falta la clave de la Point Forecast API de Windy."""


class WindyUnavailableError(RuntimeError):
    """Ningún modelo de Windy respondió."""


def verdict(models: list[ModelStudy]) -> str:
    """Conclusión en lenguaje simple según cuántos modelos ven tormenta o lluvia fuerte."""
    storm = sum(1 for m in models if m.storm_periods)
    heavy = sum(1 for m in models if m.max_3h_mm >= 15)
    if storm and storm == len(models):
        return "Todos los modelos de Windy ven tormenta eléctrica en las próximas 24 horas."
    if storm:
        return f"{storm} de {len(models)} modelos de Windy ven tormenta: es posible, no seguro."
    if heavy:
        return "Windy no ve tormenta, pero sí lluvia fuerte en pocas horas."
    if any(m.rain_mm >= 1 for m in models):
        return "Windy ve lluvia sin señales de tormenta."
    return "Windy no ve lluvia ni tormenta en las próximas 24 horas."


class WindyStudyUseCase:
    def __init__(self, provider: ModelStudyProvider | None, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._provider = provider
        self._clock = clock

    async def execute(self, location: Coordinates) -> WindyStudy:
        if self._provider is None:
            raise WindyNotConfiguredError
        models = list(await self._provider.study(location))
        if not models:
            raise WindyUnavailableError
        return WindyStudy(
            evaluated_at=self._clock(),
            models=models,
            storm_models=sum(1 for m in models if m.storm_periods),
            verdict=verdict(models),
        )

"""Decorador con caché para :class:`WeatherForecastProvider`.

En una emergencia miles de vecinos consultan ``/api/gps-check`` a la vez,
casi siempre desde las mismas pocas celdas de la grilla meteorológica. Sin
caché, cada consulta sería una llamada a Open-Meteo y la cuota gratuita se
agotaría justo cuando más se necesita.

* Clave: coordenadas redondeadas (2 decimales ≈ 1 km, muy por debajo de la
  resolución de los modelos globales, 9-25 km).
* *Single-flight*: si llegan N consultas simultáneas para la misma celda,
  se hace **una** llamada y las N esperan el mismo resultado.
* Los errores no se cachean.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable, Sequence
from typing import Any, TypeVar

from src.domain.repositories.weather_provider import WeatherForecastProvider
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.forecast import HydrologicalConditions, ModelPrecipitationForecast
from src.domain.value_objects.threat_type import WeatherModel

T = TypeVar("T")


class CachedWeatherProvider:
    """Implementa :class:`WeatherForecastProvider` delegando en ``inner`` con caché TTL."""

    def __init__(
        self,
        inner: WeatherForecastProvider,
        *,
        ttl_s: float = 900.0,
        precision: int = 2,
        max_entries: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._ttl = ttl_s
        self._precision = precision
        self._max_entries = max_entries
        self._clock = clock
        self._entries: dict[Hashable, tuple[float, asyncio.Task[Any]]] = {}

    async def get_precipitation_forecasts(
        self,
        location: Coordinates,
        models: Sequence[WeatherModel],
        horizon_hours: int = 24,
    ) -> Sequence[ModelPrecipitationForecast]:
        key = ("precip", *self._cell(location), tuple(models), horizon_hours)
        return await self._get(key, lambda: self._inner.get_precipitation_forecasts(location, models, horizon_hours))

    async def get_hydrological_conditions(
        self, location: Coordinates, horizon_hours: int = 24
    ) -> HydrologicalConditions:
        key = ("hydro", *self._cell(location), horizon_hours)
        return await self._get(key, lambda: self._inner.get_hydrological_conditions(location, horizon_hours))

    def _cell(self, location: Coordinates) -> tuple[float, float]:
        return round(location.latitude, self._precision), round(location.longitude, self._precision)

    async def _get(self, key: Hashable, factory: Callable[[], Awaitable[T]]) -> T:
        now = self._clock()
        entry = self._entries.get(key)
        if entry is not None and entry[0] > now:
            task: asyncio.Task[T] = entry[1]
        else:
            task = asyncio.ensure_future(factory())
            self._entries[key] = (now + self._ttl, task)
            self._evict(now)
        try:
            # shield: si un cliente HTTP se desconecta, no se cancela la llamada compartida.
            return await asyncio.shield(task)
        except Exception:
            current = self._entries.get(key)
            if current is not None and current[1] is task:
                del self._entries[key]
            raise

    def _evict(self, now: float) -> None:
        if len(self._entries) <= self._max_entries:
            return
        for k in [k for k, (exp, _) in self._entries.items() if exp <= now]:
            del self._entries[k]
        while len(self._entries) > self._max_entries:
            del self._entries[next(iter(self._entries))]

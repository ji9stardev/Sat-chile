"""Windy Point Forecast API (api.windy.com/point-forecast).

Requiere una clave propia (``SAT_WINDY_POINT_API_KEY``). Las claves de prueba
de Windy devuelven datos desordenados a propósito, así que sólo sirven para
probar la conexión, no para avisar a nadie.

Para Chile sirven los modelos globales ``gfs`` (NOAA) e ``icon`` (DWD); los
demás son regionales (Europa, EE. UU., Francia...). ECMWF se ve en el mapa
de Windy pero no está en esta API.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.model_study import ModelStudy
from src.infrastructure.config import Settings

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.windy.com/api/point-forecast/v2"
MODELS: Mapping[str, str] = {"gfs": "GFS (NOAA)", "icon": "ICON (DWD)"}
PARAMETERS = ("precip", "convPrecip", "cape", "windGust")
STORM_CAPE_J_KG = 300.0  # mismo umbral que el resto de la app (Chile central)
STORM_BLOCK_MM = 0.5
"""Chubasco mínimo en un bloque de 3 h para contar tormenta (~0,2 mm/h)."""

_TO_MM = {"m": 1000.0, "mm": 1.0}
_TO_KMH = {"m*s-1": 3.6, "m/s": 3.6, "km/h": 1.0, "km*h-1": 1.0}


def _series(payload: Mapping[str, Any], key: str, factors: Mapping[str, float]) -> list[float | None] | None:
    values = payload.get(key)
    if not isinstance(values, list):
        return None
    unit = (payload.get("units") or {}).get(key)
    factor = factors.get(unit, 1.0) if unit else 1.0
    return [None if v is None else float(v) * factor for v in values]


def summarize(model: str, payload: Mapping[str, Any], *, now: datetime, hours: int = 24) -> ModelStudy | None:
    """Resume la respuesta de Windy para las próximas ``hours`` horas (bloques de 3 h)."""
    stamps = payload.get("ts")
    if not isinstance(stamps, list):
        return None
    start, end = now.timestamp() * 1000, now.timestamp() * 1000 + hours * 3_600_000
    idx = [i for i, t in enumerate(stamps) if start < t <= end + 1]
    if not idx:
        return None
    rain = _series(payload, "past3hprecip-surface", _TO_MM) or []
    conv = _series(payload, "past3hconvprecip-surface", _TO_MM) or []
    cape = _series(payload, "cape-surface", {})
    gust = _series(payload, "gust-surface", _TO_KMH)

    def at(series: Sequence[float | None], i: int) -> float:
        return (series[i] or 0.0) if i < len(series) else 0.0

    storms = [i for i in idx if cape and at(cape, i) >= STORM_CAPE_J_KG and at(conv, i) >= STORM_BLOCK_MM]
    first = storms[0] if storms else None
    return ModelStudy(
        model=model,
        label=MODELS.get(model, model),
        hours=hours,
        rain_mm=round(sum(at(rain, i) for i in idx), 1),
        max_3h_mm=round(max(at(rain, i) for i in idx), 1),
        convective_mm=round(sum(at(conv, i) for i in idx), 1),
        max_cape_j_kg=round(max(at(cape, i) for i in idx)) if cape else None,
        max_gust_kmh=round(max(at(gust, i) for i in idx)) if gust else None,
        storm_periods=len(storms),
        # el dato es lo acumulado en las 3 h anteriores a la marca de tiempo
        first_storm_at=datetime.fromtimestamp(stamps[first] / 1000 - 3 * 3600, tz=UTC) if first is not None else None,
    )


class WindyClient:
    """Consulta los modelos globales de Windy para un punto, con caché por celda de ~1 km."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        key: str,
        *,
        models: Sequence[str] = tuple(MODELS),
        cache_s: float = 1800.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._key = key
        self._models = tuple(models)
        self._cache_s = cache_s
        self._clock = clock
        self._cache: dict[tuple[float, float], tuple[float, list[ModelStudy]]] = {}

    async def study(self, location: Coordinates) -> Sequence[ModelStudy]:
        cell = (round(location.latitude, 2), round(location.longitude, 2))
        hit = self._cache.get(cell)
        if hit and self._clock() - hit[0] < self._cache_s:
            return hit[1]
        now = datetime.now(UTC)
        payloads = await asyncio.gather(*(self._fetch(m, *cell) for m in self._models))
        studies = [
            s for m, p in zip(self._models, payloads, strict=True) if p and (s := summarize(m, p, now=now)) is not None
        ]
        if studies:
            self._cache[cell] = (self._clock(), studies)
        return studies

    async def _fetch(self, model: str, lat: float, lon: float) -> Mapping[str, Any] | None:
        body = {"lat": lat, "lon": lon, "model": model, "parameters": list(PARAMETERS), "key": self._key}
        try:
            response = await self._http.post(ENDPOINT, json=body)
            if response.status_code == 204:
                return None
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Windy %s no respondió: %s", model, exc)
            return None
        return data if isinstance(data, dict) else None

    async def aclose(self) -> None:
        await self._http.aclose()


def windy_from_settings(settings: Settings) -> WindyClient | None:
    """Cliente de Windy si hay clave configurada; si no, ``None`` (el estudio numérico queda apagado)."""
    if settings.windy_point_api_key is None:
        return None
    http = httpx.AsyncClient(timeout=httpx.Timeout(settings.http_timeout_s))
    return WindyClient(http, settings.windy_point_api_key.get_secret_value())

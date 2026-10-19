"""Cliente asíncrono de Open-Meteo (Forecast multi-modelo y Flood API / GloFAS).

Implementa los puertos :class:`WeatherForecastProvider` y
:class:`RiverDischargeProvider` del dominio.

* Forecast API: https://open-meteo.com/en/docs — al pedir varios modelos en
  ``models=``, cada variable horaria llega con sufijo (``precipitation_gfs_seamless``).
* Flood API: https://open-meteo.com/en/docs/flood-api — caudales GloFAS en
  grilla de ~5 km; el punto consultado debe caer sobre el cauce.

Open-Meteo es gratuito y sin API key para uso **no comercial** (con límites
diarios de llamadas); los datos GloFAS se publican bajo CC BY 4.0. Un uso
comercial o institucional de alto volumen requiere su plan de pago.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date
from typing import Any

import httpx

from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.forecast import (
    HydrologicalConditions,
    ModelPrecipitationForecast,
    RiverDischargeForecast,
    PointOutlook,
    EnsembleMember,
)
from src.domain.value_objects.threat_type import WeatherModel
from src.infrastructure.config import Settings
from src.infrastructure.exceptions import ExternalServiceError, WeatherDataUnavailableError

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ELEVATION_POINTS = 100
MIN_HOURLY_COVERAGE = 0.8
"""Fracción mínima de horas con dato para aceptar el pronóstico de un modelo."""
PAST_HOURS = 24
ENSEMBLE_MODELS = ("icon_seamless_eps", "gfs025")
STORM_CAPE_J_KG = 300.0  # 300 J/kg: en Chile central muchas tormentas (bajas segregadas) se forman con CAPE de 200-500.
STORM_HOUR_RAIN_MM = 0.2
"""Misma regla que las probabilidades: inestabilidad y lluvia en la misma hora."""
"""Horas pasadas que se piden para saber cuánto ha llovido ya."""

Sleep = Callable[[float], Awaitable[None]]


class OpenMeteoClient:
    """Adaptador HTTP hacia Open-Meteo.

    Args:
        http: Cliente ``httpx`` inyectado (permite *mock transports* en tests y
            compartir el pool de conexiones).
        forecast_url: Endpoint de la Forecast API.
        flood_url: Endpoint de la Flood API.
        max_retries: Reintentos ante 429/5xx y errores de red.
        backoff_s: Espera base del *backoff* exponencial.
        sleep: Función de espera (inyectable para tests).
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        forecast_url: str = "https://api.open-meteo.com/v1/forecast",
        flood_url: str = "https://flood-api.open-meteo.com/v1/flood",
        elevation_url: str = "https://api.open-meteo.com/v1/elevation",
        ensemble_url: str = "https://ensemble-api.open-meteo.com/v1/ensemble",
        max_retries: int = 3,
        backoff_s: float = 0.5,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._http = http
        self._forecast_url = forecast_url
        self._flood_url = flood_url
        self._elevation_url = elevation_url
        self._ensemble_url = ensemble_url
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenMeteoClient:
        """Crea el cliente con su propio ``httpx.AsyncClient`` (cerrar con :meth:`aclose`)."""
        http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.http_timeout_s),
            # Las cabeceras HTTP deben ser ASCII (httpx rechaza caracteres como "ó").
            headers={"User-Agent": "SAT-Maule/0.1 (+alerta temprana Region del Maule)"},
        )
        return cls(
            http,
            forecast_url=settings.open_meteo_forecast_url,
            flood_url=settings.open_meteo_flood_url,
            elevation_url=settings.open_meteo_elevation_url,
            ensemble_url=settings.open_meteo_ensemble_url,
            max_retries=settings.http_max_retries,
        )

    async def aclose(self) -> None:
        """Cierra el pool HTTP."""
        await self._http.aclose()

    # ------------------------------------------------------------ puertos

    async def get_precipitation_forecasts(
        self,
        location: Coordinates,
        models: Sequence[WeatherModel],
        horizon_hours: int = 24,
    ) -> Sequence[ModelPrecipitationForecast]:
        """Precipitación acumulada por modelo. Omite modelos sin datos suficientes."""
        if not models:
            return []
        data = await self._get_json(
            self._forecast_url,
            {
                **_location_params(location),
                "hourly": "precipitation",
                "models": ",".join(m.value for m in models),
                "forecast_hours": horizon_hours,
                "timezone": "UTC",
            },
        )
        hourly: Mapping[str, Any] = data.get("hourly") or {}
        forecasts: list[ModelPrecipitationForecast] = []
        for model in models:
            series = hourly.get(f"precipitation_{model.value}")
            if series is None and len(models) == 1:
                series = hourly.get("precipitation")
            values = _window(series, horizon_hours)
            if not _has_coverage(values, horizon_hours):
                logger.warning("Modelo %s sin cobertura suficiente en %s; se omite", model, location)
                continue
            total = math.fsum(v for v in values if v is not None)
            forecasts.append(
                ModelPrecipitationForecast(
                    model=model,
                    accumulated_precipitation_mm=round(max(total, 0.0), 2),
                    horizon_hours=horizon_hours,
                    hourly_mm=tuple(max(v or 0.0, 0.0) for v in values),
                )
            )
        return forecasts

    async def get_hydrological_conditions(
        self, location: Coordinates, horizon_hours: int = 24
    ) -> HydrologicalConditions:
        """Máximos de isoterma 0 °C, humedad de suelo (0-1 cm) y CAPE en la ventana.

        También entrega la lluvia de las últimas ``PAST_HOURS`` horas (lo que
        ya está cayendo) y la intensidad horaria máxima, pasada o futura.

        Raises:
            WeatherDataUnavailableError: Si falta la isoterma o la humedad de
                suelo. No se rellenan con supuestos para no activar (ni
                desactivar) el filtro de descarte con datos inventados.
        """
        data = await self._get_json(
            self._forecast_url,
            {
                **_location_params(location),
                "hourly": "freezing_level_height,soil_moisture_0_to_1cm,cape,precipitation,wind_gusts_10m",
                "past_hours": PAST_HOURS,
                "forecast_hours": horizon_hours,
                "timezone": "UTC",
            },
        )
        hourly: Mapping[str, Any] = data.get("hourly") or {}

        def ahead(name: str) -> list[float | None]:
            return _window(hourly.get(name), PAST_HOURS + horizon_hours)[PAST_HOURS:]

        freezing = _max_or_none(ahead("freezing_level_height"))
        soil = _max_or_none(ahead("soil_moisture_0_to_1cm"))
        cape = _max_or_none(ahead("cape"))
        gust = _max_or_none(ahead("wind_gusts_10m"))
        if freezing is None or soil is None:
            raise WeatherDataUnavailableError(
                f"Open-Meteo no entregó isoterma 0 °C y/o humedad de suelo para {location}."
            )
        rain = _window(hourly.get("precipitation"), PAST_HOURS + horizon_hours)
        recent = math.fsum(v for v in rain[:PAST_HOURS] if v is not None)
        return HydrologicalConditions(
            freezing_level_m=max(freezing, 0.0),
            soil_moisture_m3m3=min(max(soil, 0.0), 1.0),
            cape_j_kg=max(cape or 0.0, 0.0),
            recent_precipitation_mm=round(max(recent, 0.0), 2),
            max_hourly_precipitation_mm=max(_max_or_none(rain) or 0.0, 0.0),
            cape_hourly=tuple(max(v or 0.0, 0.0) for v in ahead("cape")),
            max_wind_gust_kmh=None if gust is None else max(gust, 0.0),
        )

    async def get_river_discharge(
        self, location: Coordinates, forecast_days: int = 7
    ) -> RiverDischargeForecast:
        """Caudal diario GloFAS (control + máximo del ensamble).

        Raises:
            WeatherDataUnavailableError: Si el punto no tiene celda fluvial GloFAS.
        """
        data = await self._get_json(
            self._flood_url,
            {
                **_location_params(location),
                "daily": "river_discharge,river_discharge_max",
                "forecast_days": forecast_days,
            },
        )
        daily: Mapping[str, Any] = data.get("daily") or {}
        times: list[str] = list(daily.get("time") or [])
        discharge = daily.get("river_discharge") or []
        if not times or all(v is None for v in discharge):
            raise WeatherDataUnavailableError(f"Sin datos GloFAS para {location}; ¿el punto está sobre un cauce?")

        rows = [
            (date.fromisoformat(t), q, q_max)
            for t, q, q_max in zip(
                times, discharge, daily.get("river_discharge_max") or [None] * len(times), strict=False
            )
            if q is not None
        ]
        has_max = all(r[2] is not None for r in rows)
        return RiverDischargeForecast(
            location=location,
            dates=tuple(r[0] for r in rows),
            discharge_m3s=tuple(float(r[1]) for r in rows),
            discharge_max_m3s=tuple(float(r[2]) for r in rows) if has_max else None,
        )

    async def get_elevations(self, points: Sequence[Coordinates]) -> Sequence[float]:
        """Elevación DEM (Copernicus GLO-90, ~90 m) de hasta 100 puntos por llamada.

        A esa resolución, diferencias de altura de pocos metros entre un
        punto y el cauce son orientativas, no topografía de detalle.
        """
        if not points:
            return []
        elevations: list[float] = []
        for start in range(0, len(points), MAX_ELEVATION_POINTS):
            chunk = points[start : start + MAX_ELEVATION_POINTS]
            data = await self._get_json(
                self._elevation_url,
                {
                    "latitude": ",".join(f"{p.latitude:.6f}" for p in chunk),
                    "longitude": ",".join(f"{p.longitude:.6f}" for p in chunk),
                },
            )
            values = data.get("elevation")
            if not isinstance(values, list) or len(values) != len(chunk) or any(v is None for v in values):
                raise ExternalServiceError("Open-Meteo Elevation devolvió una respuesta incompleta.")
            elevations.extend(float(v) for v in values)
        return elevations

    # ------------------------------------------------------------ HTTP

    async def get_ensemble(self, location: Coordinates, horizon_hours: int = 24) -> Sequence[EnsembleMember]:
        """Corridas de ICON-EPS (40) y GFS ensemble (31). Si uno falla, se usa el otro.

        En Chile sólo GFS entrega CAPE por corrida; ninguno entrega isoterma por corrida.
        """
        results = await asyncio.gather(
            *(self._ensemble_members(location, model, horizon_hours) for model in ENSEMBLE_MODELS),
            return_exceptions=True,
        )
        members: list[EnsembleMember] = []
        for model, result in zip(ENSEMBLE_MODELS, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("Ensemble %s no disponible: %s", model, result)
            else:
                members.extend(result)
        return members

    async def _ensemble_members(self, location: Coordinates, model: str, horizon_hours: int) -> list[EnsembleMember]:
        data = await self._get_json(
            self._ensemble_url,
            {
                **_location_params(location),
                "hourly": "precipitation,cape",
                "models": model,
                "forecast_hours": horizon_hours,
                "timezone": "UTC",
            },
        )
        hourly: Mapping[str, Any] = data.get("hourly") or {}
        members: list[EnsembleMember] = []
        for key in sorted(k for k in hourly if k == "precipitation" or k.startswith("precipitation_member")):
            rain = _window(hourly.get(key), horizon_hours)
            if not _has_coverage(rain, horizon_hours):
                continue
            cape = _window(hourly.get(key.replace("precipitation", "cape", 1)), horizon_hours)
            members.append(
                EnsembleMember(
                    model=model,
                    precipitation=tuple(max(v or 0.0, 0.0) for v in rain),
                    cape=tuple(max(v or 0.0, 0.0) for v in cape) if _has_coverage(cape, horizon_hours) else (),
                )
            )
        return members

    async def get_outlooks(self, points: Sequence[Coordinates], horizon_hours: int = 24) -> Sequence[PointOutlook]:
        """Lluvia, isoterma, CAPE y ráfagas de varios puntos en una sola llamada (modelo ``best_match``).

        Raises:
            WeatherDataUnavailableError: Si la respuesta no trae un resultado por punto.
        """
        if not points:
            return []
        payload = await self._get_payload(
            self._forecast_url,
            {
                "latitude": ",".join(f"{p.latitude:.4f}" for p in points),
                "longitude": ",".join(f"{p.longitude:.4f}" for p in points),
                "hourly": "precipitation,freezing_level_height,cape,wind_gusts_10m",
                "forecast_hours": horizon_hours,
                "timezone": "UTC",
            },
        )
        results = payload if isinstance(payload, list) else [payload]
        if len(results) != len(points):
            raise WeatherDataUnavailableError(f"Open-Meteo devolvió {len(results)} resultados para {len(points)} puntos.")
        outlooks: list[PointOutlook] = []
        for result in results:
            hourly: Mapping[str, Any] = (result or {}).get("hourly") or {}
            rain = _window(hourly.get("precipitation"), horizon_hours)
            cape = _window(hourly.get("cape"), horizon_hours)
            gust = _max_or_none(_window(hourly.get("wind_gusts_10m"), horizon_hours))
            storm_hours = sum(
                1
                for c, p in zip(cape, rain, strict=False)
                if c is not None and p is not None and c >= STORM_CAPE_J_KG and p >= STORM_HOUR_RAIN_MM
            )
            outlooks.append(
                PointOutlook(
                    rain_mm=round(max(math.fsum(v for v in rain if v is not None), 0.0), 2),
                    max_hourly_mm=max(_max_or_none(rain) or 0.0, 0.0),
                    freezing_level_m=_max_or_none(_window(hourly.get("freezing_level_height"), horizon_hours)),
                    cape_j_kg=max(_max_or_none(cape) or 0.0, 0.0),
                    max_wind_gust_kmh=None if gust is None else max(gust, 0.0),
                    storm_hours=storm_hours,
                )
            )
        return outlooks

    async def _get_json(self, url: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """GET que debe responder un objeto JSON."""
        payload = await self._get_payload(url, params)
        if not isinstance(payload, dict):
            raise ExternalServiceError("Formato inesperado de Open-Meteo.")
        return payload

    async def _get_payload(self, url: str, params: Mapping[str, Any]) -> Any:
        """GET con reintentos exponenciales ante errores transitorios."""
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http.get(url, params=dict(params))
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code not in RETRYABLE_STATUS:
                    return _parse(response)
                last_error = ExternalServiceError(f"Open-Meteo respondió {response.status_code}")
            if attempt < self._max_retries:
                delay = self._backoff_s * 2**attempt
                logger.warning("Open-Meteo transitorio (%s); reintento en %.1fs", last_error, delay)
                await self._sleep(delay)
        raise ExternalServiceError(f"Open-Meteo no disponible tras {self._max_retries + 1} intentos") from last_error


def _parse(response: httpx.Response) -> Any:
    """Valida la respuesta; Open-Meteo informa errores como ``{"error": true, "reason": ...}``."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise ExternalServiceError(f"Respuesta no JSON de Open-Meteo ({response.status_code})") from exc
    if response.is_error or (isinstance(payload, dict) and payload.get("error")):
        reason = payload.get("reason", "sin detalle") if isinstance(payload, dict) else "sin detalle"
        raise ExternalServiceError(f"Open-Meteo {response.status_code}: {reason}")
    if not isinstance(payload, dict | list):
        raise ExternalServiceError("Formato inesperado de Open-Meteo.")
    return payload


def _location_params(location: Coordinates) -> dict[str, Any]:
    return {"latitude": location.latitude, "longitude": location.longitude}


def _window(series: Any, hours: int) -> list[float | None]:
    if not isinstance(series, list):
        return []
    return [None if v is None else float(v) for v in series[:hours]]


def _has_coverage(values: Sequence[float | None], expected: int) -> bool:
    present = sum(1 for v in values if v is not None)
    return expected > 0 and present / expected >= MIN_HOURLY_COVERAGE


def _max_or_none(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None

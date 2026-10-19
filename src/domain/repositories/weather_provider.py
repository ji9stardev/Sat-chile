"""Contrato del proveedor de datos climáticos (Open-Meteo en la Fase 2)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.forecast import (
    HydrologicalConditions,
    ModelPrecipitationForecast,
    RiverDischargeForecast,
    PointOutlook,
    EnsembleMember,
)
from src.domain.value_objects.threat_type import WeatherModel


@runtime_checkable
class WeatherForecastProvider(Protocol):
    """Puerto de salida hacia servicios meteorológicos.

    Se define como ``Protocol`` (tipado estructural) para que cualquier
    adaptador —cliente HTTP real, *fake* de pruebas o caché— lo satisfaga sin
    heredar de una clase del dominio.
    """

    async def get_precipitation_forecasts(
        self,
        location: Coordinates,
        models: Sequence[WeatherModel],
        horizon_hours: int = 24,
    ) -> Sequence[ModelPrecipitationForecast]:
        """Precipitación acumulada por modelo para la ventana ``horizon_hours``.

        Los modelos que fallen se omiten del resultado (no se lanzan
        excepciones por un modelo individual); el motor de consenso decide si
        los restantes son suficientes.
        """
        ...

    async def get_hydrological_conditions(
        self, location: Coordinates, horizon_hours: int = 24
    ) -> HydrologicalConditions:
        """Condiciones de la ventana en su escenario más desfavorable.

        Se usan los **máximos** de la ventana (isoterma 0 °C, humedad de suelo
        y CAPE): una isoterma alta o un suelo húmedo en cualquier hora basta
        para que el filtro de descarte *no* se active. Así un dato puntual
        favorable nunca oculta un riesgo real.
        """
        ...


@runtime_checkable
class ElevationProvider(Protocol):
    """Puerto de salida hacia un modelo digital de elevación (DEM)."""

    async def get_elevations(self, points: Sequence[Coordinates]) -> Sequence[float]:
        """Elevación (m s.n.m.) de cada punto, en el mismo orden."""
        ...


@runtime_checkable
class RiverDischargeProvider(Protocol):
    """Puerto de salida hacia pronósticos de caudal (GloFAS vía Open-Meteo Flood API)."""

    async def get_river_discharge(
        self, location: Coordinates, forecast_days: int = 7
    ) -> RiverDischargeForecast:
        """Caudal diario pronosticado para el punto del cauce más cercano."""
        ...


@runtime_checkable
class AreaOutlookProvider(Protocol):
    """Panorama del tiempo para muchos puntos a la vez (comunas de una región)."""

    async def get_outlooks(self, points: Sequence[Coordinates], horizon_hours: int = 24) -> Sequence[PointOutlook]:
        """Un :class:`PointOutlook` por punto, en el mismo orden."""
        ...


@runtime_checkable
class EnsembleProvider(Protocol):
    """Corridas de pronósticos por conjunto para calcular probabilidades."""

    async def get_ensemble(self, location: Coordinates, horizon_hours: int = 24) -> Sequence[EnsembleMember]:
        """Todas las corridas disponibles; lista vacía si ningún ensemble respondió."""
        ...

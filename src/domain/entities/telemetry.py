"""Entidad ``Telemetry``: lectura de una estación o fuente de datos."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from src.domain.entities.base import Entity
from src.domain.value_objects.coordinates import Coordinates


class TelemetrySource(StrEnum):
    """Procedencia de la lectura."""

    ESTACION_PROPIA = "ESTACION_PROPIA"
    DGA = "DGA"
    """Dirección General de Aguas (fluviometría)."""
    DMC = "DMC"
    """Dirección Meteorológica de Chile."""
    OPEN_METEO = "OPEN_METEO"


class Telemetry(Entity):
    """Lectura puntual de variables hidrometeorológicas.

    Todas las métricas son opcionales porque cada estación mide un
    subconjunto distinto, pero se exige al menos una.

    Attributes:
        station_id: Identificador externo de la estación.
        source: Procedencia de la lectura.
        location: Ubicación de la estación.
        recorded_at: Instante de medición (UTC).
        precipitation_mm: Precipitación en el intervalo de medición.
        river_level_m: Altura limnimétrica del río.
        river_flow_m3s: Caudal del río.
        soil_moisture_m3m3: Humedad volumétrica del suelo.
        temperature_c: Temperatura del aire.
        freezing_level_m: Altura de la isoterma 0 °C.
    """

    station_id: str = Field(min_length=1, max_length=64)
    source: TelemetrySource
    location: Coordinates
    recorded_at: datetime
    precipitation_mm: float | None = Field(default=None, ge=0.0)
    river_level_m: float | None = None
    river_flow_m3s: float | None = Field(default=None, ge=0.0)
    soil_moisture_m3m3: float | None = Field(default=None, ge=0.0, le=1.0)
    temperature_c: float | None = Field(default=None, ge=-60.0, le=60.0)
    freezing_level_m: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _require_some_metric(self) -> Self:
        metrics = (
            self.precipitation_mm,
            self.river_level_m,
            self.river_flow_m3s,
            self.soil_moisture_m3m3,
            self.temperature_c,
            self.freezing_level_m,
        )
        if all(m is None for m in metrics):
            raise ValueError("Una lectura de telemetría debe contener al menos una métrica.")
        if self.recorded_at.tzinfo is None:
            raise ValueError("'recorded_at' debe incluir zona horaria.")
        return self

    def exceeds_river_threshold(self, threshold_m: float) -> bool:
        """``True`` si la altura del río supera el umbral entregado."""
        return self.river_level_m is not None and self.river_level_m >= threshold_m

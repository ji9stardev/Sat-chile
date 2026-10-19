"""Estudio del pronóstico de un punto según un modelo de Windy (GFS, ICON...)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ModelStudy(BaseModel):
    """Resumen de las próximas horas de un modelo para un punto.

    Attributes:
        model: Nombre del modelo en Windy (``gfs``, ``icon``).
        label: Nombre legible («GFS (NOAA)»).
        hours: Horizonte resumido.
        rain_mm: Lluvia total en el horizonte.
        max_3h_mm: Máximo de lluvia en un bloque de 3 horas.
        convective_mm: Parte de la lluvia que es de chubasco/tormenta.
        max_cape_j_kg: Energía convectiva máxima (combustible de tormentas).
        max_gust_kmh: Ráfaga máxima.
        storm_periods: Bloques de 3 h con CAPE alto y chubasco a la vez.
        first_storm_at: Inicio del primer bloque con tormenta, si hay.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    label: str
    hours: int = Field(gt=0)
    rain_mm: float = Field(ge=0.0)
    max_3h_mm: float = Field(ge=0.0)
    convective_mm: float = Field(ge=0.0)
    max_cape_j_kg: float | None = None
    max_gust_kmh: float | None = None
    storm_periods: int = Field(default=0, ge=0)
    first_storm_at: datetime | None = None

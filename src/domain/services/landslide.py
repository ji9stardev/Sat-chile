"""``LandslideAssessmentService``: posibilidad de deslizamiento de tierra en la ubicación.

Funciona en cualquier punto de Chile sin mapas previos: se mide la
**pendiente** del terreno alrededor del usuario con el modelo de elevación
(8 puntos en un círculo de ~120 m) y se combina con el mismo detonante de
lluvia que el aluvión (umbrales de la macrozona).

* Pendiente < 15°: susceptibilidad BAJA → nunca alerta.
* 15° a 30°: MEDIA → el nivel de lluvia se rebaja uno.
* ≥ 30°: ALTA → el nivel de lluvia se aplica completo.

.. warning::
   El DEM de ~90 m suaviza los relieves: subestima laderas cortas y
   empinadas (taludes de caminos, cortes de cerro). Es un tamizaje, no un
   estudio geotécnico. Donde existan, prevalecen los mapas de peligro de
   SERNAGEOMIN y los planes reguladores.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.domain.services.debris_flow import DebrisFlowAssessmentService
from src.domain.value_objects.coordinates import EARTH_RADIUS_KM, Coordinates
from src.domain.value_objects.risk_level import RiskLevel

RING_RADIUS_M = 120.0
RING_POINTS = 8


class Susceptibility(StrEnum):
    BAJA = "BAJA"
    MEDIA = "MEDIA"
    ALTA = "ALTA"


class LandslideAssessment(BaseModel):
    """Resultado inmutable del análisis de deslizamiento."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slope_deg: float = Field(ge=0, le=90)
    susceptibility: Susceptibility
    level: RiskLevel
    reason: str


def ring_points(center: Coordinates, radius_m: float = RING_RADIUS_M, n: int = RING_POINTS) -> list[Coordinates]:
    """``n`` puntos equiespaciados en un círculo de ``radius_m`` alrededor de ``center``."""
    d_lat = math.degrees(radius_m / (EARTH_RADIUS_KM * 1000))
    d_lon = d_lat / math.cos(math.radians(center.latitude))
    return [
        Coordinates(
            latitude=center.latitude + d_lat * math.cos(2 * math.pi * i / n),
            longitude=center.longitude + d_lon * math.sin(2 * math.pi * i / n),
        )
        for i in range(n)
    ]


def max_slope_deg(center_m: float, ring_m: Sequence[float], radius_m: float = RING_RADIUS_M) -> float:
    """Pendiente máxima (grados) entre el centro y el anillo, y entre puntos opuestos del anillo."""
    slopes = [abs(z - center_m) / radius_m for z in ring_m]
    half = len(ring_m) // 2
    if len(ring_m) % 2 == 0:
        slopes += [abs(ring_m[i] - ring_m[i + half]) / (2 * radius_m) for i in range(half)]
    return math.degrees(math.atan(max(slopes, default=0.0)))


class LandslideAssessmentService:
    """Clasifica la pendiente y aplica el detonante de lluvia. Sin estado ni I/O."""

    def __init__(self, medium_deg: float = 15.0, high_deg: float = 30.0) -> None:
        self._medium = medium_deg
        self._high = high_deg

    def classify(self, slope_deg: float) -> Susceptibility:
        if slope_deg >= self._high:
            return Susceptibility.ALTA
        if slope_deg >= self._medium:
            return Susceptibility.MEDIA
        return Susceptibility.BAJA

    def assess(
        self,
        *,
        slope_deg: float,
        trigger: DebrisFlowAssessmentService,
        rain_24h_mm: float | None,
        freezing_level_m: float | None,
        recent_rain_24h_mm: float | None = None,
        max_hourly_mm: float | None = None,
        model_rain_mm: Sequence[tuple[float, float]] | None = None,
    ) -> LandslideAssessment:
        susceptibility = self.classify(slope_deg)
        slope = round(slope_deg, 1)
        if susceptibility is Susceptibility.BAJA:
            return LandslideAssessment(
                slope_deg=slope, susceptibility=susceptibility, level=RiskLevel.VERDE,
                reason=f"Terreno de pendiente baja ({slope:.0f}°): poco expuesto a deslizamientos.",
            )
        where = f"Ladera de pendiente {'alta' if susceptibility is Susceptibility.ALTA else 'media'} ({slope:.0f}°)"
        if rain_24h_mm is None or freezing_level_m is None:
            return LandslideAssessment(
                slope_deg=slope, susceptibility=susceptibility, level=RiskLevel.VERDE,
                reason=f"{where}. Sin pronóstico disponible: con lluvia intensa, aléjese de cortes y taludes.",
            )
        level = trigger.trigger_level(
            rain_24h_mm=rain_24h_mm,
            freezing_level_m=freezing_level_m,
            recent_rain_24h_mm=recent_rain_24h_mm,
            max_hourly_mm=max_hourly_mm,
            model_rain_mm=model_rain_mm,
        )
        if susceptibility is Susceptibility.MEDIA:
            level = level.deescalate()
        if level is RiskLevel.VERDE:
            reason = f"{where}. Con la lluvia esperada no hay condiciones de deslizamiento."
        else:
            reason = (
                f"POSIBLE DESLIZAMIENTO: {where.lower()} y "
                f"{trigger.describe_trigger(rain_24h_mm, freezing_level_m, recent_rain_24h_mm, max_hourly_mm)}. "
                "Aléjese de laderas, cortes de camino y taludes; atento a grietas, ruidos o árboles inclinados."
            )
        return LandslideAssessment(slope_deg=slope, susceptibility=susceptibility, level=level, reason=reason)

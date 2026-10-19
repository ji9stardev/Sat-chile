"""Probabilidades a partir de pronósticos por conjunto (ensemble).

Un ensemble corre el mismo modelo decenas de veces con condiciones iniciales
apenas distintas. La fracción de corridas que supera un umbral es una
probabilidad: si 28 de 71 corridas dan ≥ 15 mm, la probabilidad es ~39 %.

Granizo: ningún modelo global público entrega granizo en Chile. Se estima con
los ingredientes clásicos en la misma hora de cada corrida: inestabilidad
fuerte (CAPE) y lluvia convectiva, con una isoterma 0 °C que no lo derrita
antes de llegar al suelo. El tamaño es una referencia según la CAPE, no un
pronóstico de cantidad.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.domain.value_objects.forecast import EnsembleMember


STORM_ALERT_PCT = 30
"""Desde este % de corridas con tormenta se avisa tormenta eléctrica (app y push)."""


class HailSize(StrEnum):
    PEQUENO = "PEQUENO"
    """Menor a 1 cm (tipo arveja)."""
    MEDIANO = "MEDIANO"
    """1 a 2 cm (tipo uva o bolita)."""
    GRANDE = "GRANDE"
    """Más de 2 cm: puede dañar autos, techos y cultivos."""


class ProbabilityConfig(BaseModel):
    """Umbrales de las probabilidades.

    Attributes:
        rain_min_mm: Lluvia en 24 h para contar «llueve».
        intense_hourly_mm: Lluvia en una hora para «mucha agua en poco tiempo».
        hail_cape_j_kg: CAPE mínimo de la hora para condiciones de granizo.
        hail_hour_rain_mm: Lluvia mínima en esa misma hora (hay chubasco).
        hail_max_freezing_m: Sobre esta isoterma el granizo suele derretirse en la caída.
        storm_cape_j_kg: CAPE de la hora para tormenta eléctrica (mismo umbral que el aviso).
        storm_hour_rain_mm: Lluvia mínima en esa hora (la nube ya precipita).
    """

    model_config = ConfigDict(frozen=True)

    rain_min_mm: float = 1.0
    intense_hourly_mm: float = 10.0
    hail_cape_j_kg: float = 1000.0
    hail_hour_rain_mm: float = 1.0
    hail_max_freezing_m: float = 4500.0
    storm_cape_j_kg: float = 300.0  # 300 J/kg: en Chile central muchas tormentas (bajas segregadas) se forman con CAPE de 200-500.
    storm_hour_rain_mm: float = 0.2


class ProbabilityOutlook(BaseModel):
    """Probabilidades de las próximas horas en un punto (0 a 100)."""

    model_config = ConfigDict(frozen=True)

    members: int = Field(ge=1)
    """Corridas usadas (ICON-EPS + GFS ensemble)."""
    rain_pct: int = Field(ge=0, le=100)
    intense_pct: int = Field(ge=0, le=100)
    """Probabilidad de mucha agua en poco tiempo (una hora sobre el umbral)."""
    intense_hourly_mm: float
    debris_pct: tuple[int, int, int]
    """Probabilidad de alcanzar la lluvia de aluvión AMARILLO, NARANJA, ROJO."""
    snow: bool = False
    """La isoterma está baja: en altura nieva y no hay aluvión aunque llueva."""
    rain_median_mm: float
    rain_p90_mm: float
    """Escenario lluvioso: sólo 1 de cada 10 corridas da más que esto."""
    storm_pct: int | None = Field(default=None, ge=0, le=100)
    """Tormenta eléctrica; ``None`` si ninguna corrida trajo CAPE."""
    hail_pct: int | None = Field(default=None, ge=0, le=100)
    """``None`` si ninguna corrida trajo CAPE."""
    hail_size: HailSize | None = None
    hail_members: int = 0


def _pct(part: int, total: int) -> int:
    return round(100 * part / total) if total else 0


class ProbabilityService:
    """Convierte las corridas del ensemble en probabilidades."""

    def __init__(self, config: ProbabilityConfig | None = None) -> None:
        self._cfg = config or ProbabilityConfig()

    def storm_pct(self, members: Sequence[EnsembleMember]) -> int | None:
        """Sólo la probabilidad de tormenta (para el monitor de zonas)."""
        cfg = self._cfg
        with_cape = [m for m in members if m.precipitation and m.cape]
        if not with_cape:
            return None
        storms = sum(
            any(
                c >= cfg.storm_cape_j_kg and p >= cfg.storm_hour_rain_mm
                for c, p in zip(m.cape, m.precipitation, strict=False)
            )
            for m in with_cape
        )
        return _pct(storms, len(with_cape))

    def assess(
        self,
        members: Sequence[EnsembleMember],
        *,
        debris_thresholds_mm: tuple[float, float, float],
        debris_min_freezing_m: float,
        freezing_level_m: float | None,
    ) -> ProbabilityOutlook | None:
        """``None`` si no hay corridas con lluvia."""
        cfg = self._cfg
        usable = [m for m in members if m.precipitation]
        if not usable:
            return None
        totals = sorted(math.fsum(m.precipitation) for m in usable)
        n = len(usable)
        snow = freezing_level_m is not None and freezing_level_m < debris_min_freezing_m
        debris = tuple(0 if snow else _pct(sum(t >= limit for t in totals), n) for limit in debris_thresholds_mm)

        with_cape = [m for m in usable if m.cape]
        hail_pct: int | None = None
        hail_size: HailSize | None = None
        storm_pct: int | None = None
        if with_cape:
            storms = sum(
                any(
                    c >= cfg.storm_cape_j_kg and p >= cfg.storm_hour_rain_mm
                    for c, p in zip(m.cape, m.precipitation, strict=False)
                )
                for m in with_cape
            )
            storm_pct = _pct(storms, len(with_cape))
            melts = freezing_level_m is not None and freezing_level_m > cfg.hail_max_freezing_m
            peaks = [
                max(
                    (c for c, p in zip(m.cape, m.precipitation, strict=False) if p >= cfg.hail_hour_rain_mm),
                    default=0.0,
                )
                for m in with_cape
            ]
            hits = [] if melts else [c for c in peaks if c >= cfg.hail_cape_j_kg]
            hail_pct = _pct(len(hits), len(with_cape))
            if hits:
                typical = statistics.median(hits)
                hail_size = HailSize.GRANDE if typical >= 3000 else HailSize.MEDIANO if typical >= 2000 else HailSize.PEQUENO

        return ProbabilityOutlook(
            members=n,
            rain_pct=_pct(sum(t >= cfg.rain_min_mm for t in totals), n),
            intense_pct=_pct(sum(max(m.precipitation) >= cfg.intense_hourly_mm for m in usable), n),
            intense_hourly_mm=cfg.intense_hourly_mm,
            debris_pct=(debris[0], debris[1], debris[2]),
            snow=snow,
            rain_median_mm=round(statistics.median(totals), 1),
            rain_p90_mm=round(totals[min(n - 1, math.ceil(0.9 * n) - 1)], 1),
            storm_pct=storm_pct,
            hail_pct=hail_pct,
            hail_size=hail_size,
            hail_members=len(with_cape),
        )

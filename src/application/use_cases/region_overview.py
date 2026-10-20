"""Vista regional: nivel de todas las comunas de una región.

Es un panorama para comparar comunas, no el diagnóstico personal: usa un solo
modelo meteorológico y los umbrales de lluvia (no cauces ni pendiente), y
encima las alertas vigentes de SENAPRED, que mandan sobre el cálculo propio.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from src.application.dto import ComunaOutlook, RegionOverview
from src.domain.repositories.official_alert_provider import OfficialAlertProvider
from src.domain.repositories.weather_provider import AreaOutlookProvider
from src.domain.services.regional_thresholds import THRESHOLDS, rain_level
from src.domain.value_objects.admin_area import AdminArea
from src.domain.value_objects.forecast import PointOutlook
from src.domain.value_objects.official_alert import OfficialAlert, OfficialLevel
from src.domain.value_objects.risk_level import RiskLevel

STORM_MIN_RAIN_MM = 0.5


class RegionNotFoundError(LookupError):
    """No hay comunas cargadas para ese código de región."""


def comuna_outlook(area: AdminArea, outlook: PointOutlook) -> ComunaOutlook:
    """Aplica los umbrales de la macrozona de la comuna."""
    zone = area.macrozone
    level = rain_level(zone, outlook.rain_mm)
    snow = outlook.freezing_level_m is not None and outlook.freezing_level_m < THRESHOLDS[zone].flood_freezing_m
    if snow:
        level = level.deescalate()  # en altura nieva: baja menos agua, pero en el valle sigue lloviendo
    # Un solo modelo: la tormenta se informa como «posible» pero no sube el nivel; el nivel de
    # tormenta lo decide el ensamble (diagnóstico personal) o una alerta de SENAPRED.
    storm = outlook.storm_hours > 0 and outlook.rain_mm >= STORM_MIN_RAIN_MM
    return ComunaOutlook(
        comuna=area.comuna,
        level=level,
        rain_mm=outlook.rain_mm,
        max_hourly_mm=outlook.max_hourly_mm,
        max_wind_gust_kmh=outlook.max_wind_gust_kmh,
        storm=storm,
        snow=snow and outlook.rain_mm >= THRESHOLDS[zone].flood_mm[0],
    )


_OFFICIAL_TO_RISK = {OfficialLevel.AMARILLA: RiskLevel.AMARILLO, OfficialLevel.ROJA: RiskLevel.ROJO}


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def applies_to_comuna(alert: OfficialAlert, comuna: str) -> bool:
    """La alerta nombra la comuna o rige para toda la región (no sólo para algunas provincias)."""
    if alert.comunas:
        return _fold(comuna) in {_fold(c) for c in alert.comunas}
    return _fold(alert.scope).startswith("region")


def with_official(comuna: ComunaOutlook, alerts: Sequence[OfficialAlert]) -> ComunaOutlook:
    """Anota la alerta de SENAPRED más alta que incluye a la comuna.

    Sólo sube el nivel la alerta de lluvia, crecida, aluvión o tormenta que **nombra** la comuna:
    una alerta para toda la región se anota, pero no pinta todas las comunas del mismo color.
    """
    mine = [a for a in alerts if applies_to_comuna(a, comuna.comuna)]
    if not mine:
        return comuna
    top = max(mine, key=lambda a: (a.is_hydrometeorological, a.level.severity))
    named = [a for a in mine if a.comunas and a.is_hydrometeorological]
    level = RiskLevel.most_severe(comuna.level, *(_OFFICIAL_TO_RISK.get(a.level, RiskLevel.VERDE) for a in named))
    return comuna.model_copy(update={"level": level, "official_level": top.level, "official_hazard": top.hazard})


class RegionOverviewUseCase:
    """Consulta todas las comunas de la región en una llamada y guarda el resultado un rato."""

    def __init__(
        self,
        areas: Sequence[AdminArea],
        provider: AreaOutlookProvider,
        official: OfficialAlertProvider | None = None,
        ttl_s: float = 1200.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._by_region: dict[str, list[AdminArea]] = {}
        for area in areas:
            self._by_region.setdefault(area.region_code, []).append(area)
        self._provider = provider
        self._official = official
        self._ttl_s = ttl_s
        self._clock = clock
        self._cache: dict[str, tuple[float, RegionOverview]] = {}

    async def execute(self, region_code: str) -> RegionOverview:
        """El pronóstico se guarda 20 minutos; las alertas de SENAPRED se aplican en cada consulta."""
        overview = await self._weather_overview(region_code)
        if self._official is None:
            return overview
        alerts = [a for a in await self._official.active_alerts() if region_code in a.region_codes]
        if not alerts:
            return overview
        comunas = sorted(
            (with_official(c, alerts) for c in overview.comunas),
            key=lambda c: (-c.level.severity, -(c.official_level.severity if c.official_level else 0), -c.rain_mm, c.comuna),
        )
        return overview.model_copy(update={"comunas": comunas})

    async def _weather_overview(self, region_code: str) -> RegionOverview:
        cached = self._cache.get(region_code)
        if cached and self._clock() - cached[0] < self._ttl_s:
            return cached[1]
        areas = self._by_region.get(region_code)
        if not areas:
            raise RegionNotFoundError(region_code)
        outlooks = await self._provider.get_outlooks([a.representative_point for a in areas])
        comunas = sorted(
            (comuna_outlook(a, o) for a, o in zip(areas, outlooks, strict=True)),
            key=lambda c: (-c.level.severity, -c.rain_mm, c.comuna),
        )
        overview = RegionOverview(
            region=areas[0].region,
            region_code=region_code,
            evaluated_at=datetime.now(UTC),
            thresholds_mm=THRESHOLDS[areas[0].macrozone].flood_mm,
            comunas=comunas,
        )
        self._cache[region_code] = (self._clock(), overview)
        return overview

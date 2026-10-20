"""Caso de uso: diagnóstico de riesgo personalizado para una coordenada GPS.

Combina tres fuentes de **peligro** y una medida de **exposición**:

Peligro (se toma el más severo):
  1. Consenso multi-modelo calculado en la ubicación del usuario
     (:class:`ConsensusEngineService` + filtro hidrológico).
  2. Alertas activas de las zonas que contienen el punto.
  3. Nivel vigente de esas zonas.

Exposición (:class:`ExposureAssessmentService`):
  distancia al eje del cauce/quebrada más cercano (``$nearSphere``) y altura
  relativa usuario-cauce según el modelo de elevación.

Cobertura nacional:
  * La comuna y región salen de los límites de OpenStreetMap
    (:class:`AdminAreaLocator`); la región elige los umbrales de lluvia de su
    macrozona (:class:`RegionalRules`). Sin comuna conocida, se usa la latitud.
  * Deslizamientos: pendiente del terreno medida con el DEM alrededor del
    usuario (:class:`LandslideAssessmentService`).
  * Caudal: pronóstico GloFAS del río más cercano, si hay uno a menos de 3 km.

Política de degradación (AP): si una fuente falla, el diagnóstico se
entrega igual con ``data_quality=PARCIAL`` y la lista de fuentes
faltantes, nunca con un error 500 en plena emergencia. La altura GPS del
teléfono no se usa: es elipsoidal y con error de ±10-30 m; ambos puntos se
miden con el mismo DEM para que la diferencia sea coherente.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import TypeVar

from src.application.dto import (
    AlertBrief,
    AreaSummary,
    Clock,
    DataQuality,
    FlowTrend,
    GpsRiskDiagnosis,
    HourlyRain,
    ModelRain,
    NearbySafePoint,
    NearbyWatercourse,
    RainThresholds,
    RiverFlowSummary,
    StormWindow,
    WatercourseProximity,
    ZoneSummary,
    NearbyService,
    MunicipalPhone,
    LocalitySummary,
    OfficialAlertBrief,
)
from src.domain.entities.alert import Alert
from src.domain.entities.base import utc_now
from src.domain.entities.geo_zone import GeoZone
from src.domain.entities.watercourse import Watercourse, WatercourseKind
from src.domain.exceptions import ExternalDataUnavailableError, InsufficientModelDataError
from src.domain.repositories import (
    AlertRepository,
    ElevationProvider,
    GeoZoneRepository,
    RiverDischargeProvider,
    SafePointRepository,
    WatercourseRepository,
    WeatherForecastProvider,
)
from src.domain.services.consensus_engine import ConsensusEngineService
from src.domain.services.debris_flow import DebrisFlowAssessmentService
from src.domain.services.exposure_assessment import ExposureAssessmentService, level_recommendation
from src.domain.services.landslide import (
    LandslideAssessment,
    LandslideAssessmentService,
    Susceptibility,
    max_slope_deg,
    ring_points,
)
from src.domain.repositories.emergency_service_finder import EmergencyServiceFinder
from src.domain.repositories.locality_finder import LocalityFinder
from src.domain.repositories.official_alert_provider import OfficialAlertProvider
from src.domain.services.probability import ProbabilityService
from src.domain.services.regional_thresholds import RegionalRules
from src.domain.repositories.weather_provider import EnsembleProvider
from src.domain.repositories.admin_area_locator import AdminAreaLocator
from src.domain.value_objects.admin_area import AdminArea, MacroZone, macrozone_for
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.forecast import (
    ConsensusResult,
    EnsembleMember,
    HydrologicalConditions,
    ModelPrecipitationForecast,
)
from src.domain.value_objects.official_alert import OfficialLevel
from src.domain.value_objects.risk_level import RiskLevel
from src.domain.value_objects.threat_type import ThreatType, WeatherModel

logger = logging.getLogger(__name__)

T = TypeVar("T")

_Measured = tuple[Watercourse, Coordinates, float]
"""(cauce, punto más cercano del cauce, distancia en km)."""


def _nearest_of(measured: Sequence[_Measured], kind: WatercourseKind) -> float | None:
    return next((d for c, _, d in measured if c.kind is kind), None)


HOURLY_RAIN_HOURS = 12


def _hour_start(now: datetime) -> datetime:
    """Open-Meteo entrega las series desde la hora en curso."""
    return now.replace(minute=0, second=0, microsecond=0)


def _hourly_rain(
    forecasts: Sequence[ModelPrecipitationForecast], engine: ConsensusEngineService, now: datetime
) -> tuple[HourlyRain, ...]:
    """Lluvia hora a hora de las próximas 12 h, ponderada con los mismos pesos del consenso."""
    weights = engine.config.model_weights
    series = [(f.hourly_mm, weights[f.model]) for f in forecasts if f.model in weights and f.hourly_mm]
    if not series:
        return ()
    hours = min(HOURLY_RAIN_HOURS, *(len(s) for s, _ in series))
    total = sum(w for _, w in series)
    start = _hour_start(now)
    return tuple(
        HourlyRain(
            time=start + timedelta(hours=h),
            precipitation_mm=round(sum(s[h] * w for s, w in series) / total, 1),
        )
        for h in range(hours)
    )


def _storm_window(
    conditions: HydrologicalConditions | None, engine: ConsensusEngineService, now: datetime
) -> StormWindow | None:
    """Primera y última hora con CAPE sobre el umbral de tormenta (``None`` si no hay)."""
    if conditions is None:
        return None
    threshold = engine.config.thunderstorm_cape_threshold_j_kg
    hours = [h for h, cape in enumerate(conditions.cape_hourly) if cape >= threshold]
    if not hours:
        return None
    start = _hour_start(now)
    return StormWindow(start=start + timedelta(hours=hours[0]), end=start + timedelta(hours=hours[-1] + 1))


MISSING_FORECAST_NOTICE = (
    "No fue posible obtener el pronóstico meteorológico en este momento: "
    "el nivel mostrado se basa sólo en alertas vigentes. Siga los canales oficiales de SENAPRED."
)


class EvaluateGpsRiskUseCase:
    """Orquesta repositorios, proveedores externos y servicios de dominio."""

    def __init__(
        self,
        *,
        zones: GeoZoneRepository,
        alerts: AlertRepository,
        watercourses: WatercourseRepository,
        safe_points: SafePointRepository,
        weather: WeatherForecastProvider,
        elevation: ElevationProvider,
        engine: ConsensusEngineService,
        exposure: ExposureAssessmentService,
        debris_flow: DebrisFlowAssessmentService | None = None,
        locator: AdminAreaLocator | None = None,
        regional: RegionalRules | None = None,
        landslide: LandslideAssessmentService | None = None,
        discharge: RiverDischargeProvider | None = None,
        ensemble: EnsembleProvider | None = None,
        probability: ProbabilityService | None = None,
        services: EmergencyServiceFinder | None = None,
        municipal_phones: Mapping[tuple[str, str], MunicipalPhone] | None = None,
        localities: LocalityFinder | None = None,
        official: OfficialAlertProvider | None = None,
        clock: Clock = utc_now,
        watercourse_search_km: float = 3.0,
        nearby_limit: int = 25,
        safe_point_search_km: float = 15.0,
        horizon_hours: int = 24,
    ) -> None:
        self._debris = debris_flow or DebrisFlowAssessmentService()
        self._locator = locator
        self._regional = regional
        self._landslide = landslide or LandslideAssessmentService()
        self._discharge = discharge
        self._ensemble = ensemble
        self._probability = probability or ProbabilityService()
        self._services = services
        self._municipal_phones = municipal_phones or {}
        self._localities = localities
        self._official = official
        self._nearby_limit = nearby_limit
        self._zones = zones
        self._alerts = alerts
        self._watercourses = watercourses
        self._safe_points = safe_points
        self._weather = weather
        self._elevation = elevation
        self._engine = engine
        self._exposure = exposure
        self._clock = clock
        self._watercourse_km = watercourse_search_km
        self._safe_point_km = safe_point_search_km
        self._horizon = horizon_hours

    async def execute(self, location: Coordinates) -> GpsRiskDiagnosis:
        """Calcula el diagnóstico. Nunca lanza por fallas de fuentes externas."""
        now = self._clock()
        missing: list[str] = []

        area = await self._locator.locate(location) if self._locator is not None else None
        macrozone = macrozone_for(area.region_code if area else None, location.latitude)
        engine, debris_service = self._rules(macrozone)

        zones, forecast, nearby_courses, slope, members = await asyncio.gather(
            self._guard("zonas", self._zones.find_containing(location), [], missing),
            self._forecast(location, engine, missing),
            self._guard(
                "cauces",
                self._watercourses.find_near(location, self._watercourse_km, limit=self._nearby_limit),
                [],
                missing,
            ),
            self._slope(location, missing),
            self._ensemble_members(location, missing),
        )
        consensus, conditions, model_rain, forecasts = forecast if forecast else (None, None, None, [])
        alert_lists = await asyncio.gather(
            *(self._guard("alertas", self._alerts.find_active_by_zone(z.id, now), [], missing) for z in zones)
        )
        active_alerts = sorted(
            {a.id: a for alerts in alert_lists for a in alerts}.values(), key=lambda a: a.risk_level, reverse=True
        )

        hazard = RiskLevel.most_severe(
            *(a.risk_level for a in active_alerts),
            *(z.current_risk_level for z in zones),
            *((consensus.risk_level,) if consensus else ()),
        )

        measured = sorted(
            ((c, *c.geometry.nearest_point(location)) for c in nearby_courses), key=lambda m: m[2]
        )
        proximity, elevation_m = await self._proximity(location, measured, missing)
        assessment = self._exposure.assess(
            distance_km=proximity.distance_km if proximity else None,
            relative_height_m=proximity.relative_height_m if proximity else None,
            hazard_level=hazard,
            watercourse_name=proximity.name if proximity else None,
            kind=proximity.kind if proximity else None,
        )
        debris = debris_service.assess(
            nearest_quebrada_km=_nearest_of(measured, WatercourseKind.QUEBRADA),
            nearest_stream_km=_nearest_of(measured, WatercourseKind.ESTERO),
            elevation_m=elevation_m,
            rain_24h_mm=consensus.weighted_precipitation_mm if consensus else None,
            freezing_level_m=conditions.freezing_level_m if conditions else None,
            recent_rain_24h_mm=conditions.recent_precipitation_mm if conditions else None,
            max_hourly_mm=conditions.max_hourly_precipitation_mm if conditions else None,
            model_rain_mm=model_rain,
        )
        landslide = self._assess_landslide(slope, debris_service, consensus, conditions, model_rain)
        probabilities = self._probability.assess(
            members,
            debris_thresholds_mm=(
                debris_service.config.amarillo_mm,
                debris_service.config.naranja_mm,
                debris_service.config.rojo_mm,
            ),
            debris_min_freezing_m=debris_service.config.min_freezing_level_m,
            freezing_level_m=conditions.freezing_level_m if conditions else None,
        )
        river_flow = await self._river_flow(measured, missing)
        official_alerts = await self._official_alerts(area, macro_region=area.region_code if area else None)
        if consensus is None:
            # Sin pronóstico propio no se puede afirmar que su punto esté tranquilo: rige la alerta regional.
            official_alerts = tuple(
                a.model_copy(update={"sets_level": True})
                if a.applies_to == "TODA_REGION" and a.hydromet and a.level in _OFFICIAL_TO_RISK
                else a
                for a in official_alerts
            )
        personal = RiskLevel.most_severe(
            assessment.personal_risk_level,
            debris.level,
            landslide.level if landslide else RiskLevel.VERDE,
            # La alerta oficial de lluvia/crecida/aluvión que nombra su comuna manda sobre el cálculo
            # propio. Las de toda la región o de otras amenazas (incendio, volcán…) sólo se informan.
            *(_OFFICIAL_TO_RISK[a.level] for a in official_alerts if a.sets_level),
        )

        threat = (
            ThreatType.REMOCION_EN_MASA
            if debris.level > RiskLevel.VERDE or (landslide is not None and landslide.level > RiskLevel.VERDE)
            else self._threat(active_alerts, consensus, nearby_courses)
        )
        safe_points = await self._nearest_safe_points(location, threat, missing)

        # La primera recomendación es siempre la del nivel final (contrato con la PWA).
        recommendations = [level_recommendation(personal), *assessment.recommendations[1:]]
        if debris.susceptible:
            recommendations.append(debris.reason)
        if landslide is not None and landslide.susceptibility is not Susceptibility.BAJA:
            recommendations.append(landslide.reason)
        if river_flow is not None and river_flow.trend in (FlowTrend.SUBE, FlowTrend.SUBE_FUERTE):
            recommendations.append(
                f"El caudal de {river_flow.name} subiría de {river_flow.today_m3s:.0f} a "
                f"{river_flow.peak_m3s:.0f} m³/s hacia el {river_flow.peak_date:%d-%m} (pronóstico GloFAS)."
            )
        if consensus is None:
            recommendations.append(MISSING_FORECAST_NOTICE)

        unique_missing = tuple(dict.fromkeys(missing))
        return GpsRiskDiagnosis(
            location=location,
            evaluated_at=now,
            within_maule=location.is_within_maule,
            within_chile=area is not None or location.is_within_chile,
            area=AreaSummary.from_area(area) if area else None,
            macrozone=macrozone,
            landslide=landslide,
            river_flow=river_flow,
            probabilities=probabilities,
            emergency_services=await self._emergency_services(location),
            municipal_phone=self._municipal_phones.get((area.region_code, area.comuna)) if area else None,
            locality=await self._locality(location),
            official_alerts=official_alerts,
            comuna_alerts=await self._comuna_alerts(area, zones, active_alerts, now, missing),
            conditions=conditions,
            model_precipitation=tuple(
                ModelRain(model=f.model, precipitation_mm=f.accumulated_precipitation_mm) for f in forecasts
            ),
            hourly_rain=_hourly_rain(forecasts, engine, now),
            storm_window=_storm_window(conditions, engine, now),
            rain_thresholds=RainThresholds(
                amarillo_mm=debris_service.config.amarillo_mm,
                naranja_mm=debris_service.config.naranja_mm,
                rojo_mm=debris_service.config.rojo_mm,
                min_freezing_level_m=debris_service.config.min_freezing_level_m,
                intense_hourly_mm=debris_service.config.intense_hourly_mm,
                flood_mm=(
                    engine.config.amarillo_threshold_mm,
                    engine.config.naranja_threshold_mm,
                    engine.config.rojo_threshold_mm,
                ),
            ),
            zones=tuple(ZoneSummary.from_zone(z) for z in zones),
            hazard_level=hazard,
            exposure=assessment.exposure,
            personal_risk_level=personal,
            watercourse=proximity,
            nearby_watercourses=tuple(
                NearbyWatercourse(
                    name=c.name,
                    kind=c.kind,
                    distance_km=round(d, 3),
                    path=tuple((v.latitude, v.longitude) for v in c.geometry.vertices),
                    geometry_source=c.geometry_source,
                )
                for c, _, d in measured
            ),
            elevation_m=elevation_m,
            debris_flow=debris,
            consensus=consensus,
            active_alerts=tuple(AlertBrief.from_alert(a) for a in active_alerts),
            recommendations=tuple(recommendations),
            safe_points=safe_points,
            data_quality=DataQuality.PARCIAL if unique_missing else DataQuality.COMPLETA,
            missing_sources=unique_missing,
        )

    # ------------------------------------------------------------ fuentes

    async def _official_alerts(
        self, area: AdminArea | None, macro_region: str | None
    ) -> tuple[OfficialAlertBrief, ...]:
        """Alertas de SENAPRED de la región; primero las que nombran la comuna del usuario."""
        if self._official is None or area is None or macro_region is None:
            return ()
        mine = _fold(area.comuna)
        briefs: list[OfficialAlertBrief] = []
        for alert in await self._official.active_alerts():
            if macro_region not in alert.region_codes:
                continue
            if alert.comunas:
                if mine not in {_fold(c) for c in alert.comunas}:
                    continue  # alerta para otras comunas de la región
                briefs.append(OfficialAlertBrief.from_alert(alert, "COMUNA"))
            elif _fold(alert.scope).startswith("region"):
                briefs.append(OfficialAlertBrief.from_alert(alert, "TODA_REGION"))
            else:
                briefs.append(OfficialAlertBrief.from_alert(alert, "REGION"))  # provincias: informativa
        return tuple(
            sorted(
                briefs,
                key=lambda b: (not b.hydromet, b.applies_to == "REGION", -b.level.severity, b.applies_to != "COMUNA"),
            )
        )

    async def _locality(self, location: Coordinates) -> LocalitySummary | None:
        found = await self._localities.nearest(location) if self._localities is not None else None
        if found is None:
            return None
        locality, distance = found
        return LocalitySummary(
            name=locality.name, kind=locality.kind, distance_km=round(distance, 2), near=distance > 3.0
        )

    async def _comuna_alerts(
        self,
        area: AdminArea | None,
        own_zones: Sequence[GeoZone],
        own_alerts: Sequence[Alert],
        now: datetime,
        missing: list[str],
    ) -> tuple[AlertBrief, ...]:
        """Alertas de las demás zonas monitoreadas de la comuna (no suben el nivel personal)."""
        if area is None:
            return ()
        own_ids = {z.id for z in own_zones}
        seen = {a.id for a in own_alerts}
        all_zones = await self._guard("zonas", self._zones.list_all(), [], missing)
        others = [z for z in all_zones if z.id not in own_ids and _fold(z.commune) == _fold(area.comuna)]
        briefs: list[AlertBrief] = []
        for zone in others:
            for alert in await self._guard("alertas", self._alerts.find_active_by_zone(zone.id, now), [], missing):
                if alert.id not in seen:
                    seen.add(alert.id)
                    briefs.append(AlertBrief.from_alert(alert, zone_name=zone.name))
        return tuple(sorted(briefs, key=lambda b: b.risk_level, reverse=True))

    async def _emergency_services(self, location: Coordinates) -> tuple[NearbyService, ...]:
        if self._services is None:
            return ()
        return tuple(
            NearbyService(
                kind=service.kind,
                name=service.name,
                phone=service.phone,
                distance_km=round(distance, 2),
                latitude=service.location.latitude,
                longitude=service.location.longitude,
                detail=service.detail,
            )
            for service, distance in await self._services.find_nearest(location)
        )

    async def _ensemble_members(self, location: Coordinates, missing: list[str]) -> Sequence[EnsembleMember]:
        if self._ensemble is None:
            return []
        members = await self._guard(
            "probabilidades", self._ensemble.get_ensemble(location, self._horizon), [], missing
        )
        if not members:
            missing.append("probabilidades")
        return members

    def _rules(self, macrozone: MacroZone) -> tuple[ConsensusEngineService, DebrisFlowAssessmentService]:
        """Umbrales de la macrozona; sin reglas regionales, los configurados (Maule)."""
        if self._regional is None:
            return self._engine, self._debris
        return self._regional.engine(macrozone), self._regional.debris(macrozone)

    async def _forecast(
        self, location: Coordinates, engine: ConsensusEngineService, missing: list[str]
    ) -> (
        tuple[ConsensusResult, HydrologicalConditions, list[tuple[float, float]], list[ModelPrecipitationForecast]]
        | None
    ):
        try:
            forecasts, conditions = await asyncio.gather(
                self._weather.get_precipitation_forecasts(location, list(WeatherModel), self._horizon),
                self._weather.get_hydrological_conditions(location, self._horizon),
            )
            weights = engine.config.model_weights
            model_rain = [(f.accumulated_precipitation_mm, weights[f.model]) for f in forecasts if f.model in weights]
            return engine.evaluate(forecasts, conditions), conditions, model_rain, list(forecasts)
        except (ExternalDataUnavailableError, InsufficientModelDataError) as exc:
            logger.warning("Consenso no disponible en %s: %s", location, exc)
            missing.append("pronostico")
            return None

    async def _slope(self, location: Coordinates, missing: list[str]) -> float | None:
        """Pendiente máxima (°) alrededor del usuario según el DEM."""
        try:
            center, *ring = await self._elevation.get_elevations([location, *ring_points(location)])
        except (ExternalDataUnavailableError, ValueError) as exc:
            logger.warning("Pendiente no disponible en %s: %s", location, exc)
            missing.append("pendiente")
            return None
        return max_slope_deg(center, ring)

    def _assess_landslide(
        self,
        slope: float | None,
        trigger: DebrisFlowAssessmentService,
        consensus: ConsensusResult | None,
        conditions: HydrologicalConditions | None,
        model_rain: list[tuple[float, float]] | None,
    ) -> LandslideAssessment | None:
        if slope is None:
            return None
        return self._landslide.assess(
            slope_deg=slope,
            trigger=trigger,
            rain_24h_mm=consensus.weighted_precipitation_mm if consensus else None,
            freezing_level_m=conditions.freezing_level_m if conditions else None,
            recent_rain_24h_mm=conditions.recent_precipitation_mm if conditions else None,
            max_hourly_mm=conditions.max_hourly_precipitation_mm if conditions else None,
            model_rain_mm=model_rain,
        )

    async def _river_flow(self, measured: Sequence[_Measured], missing: list[str]) -> RiverFlowSummary | None:
        """Caudal GloFAS del río más cercano (los esteros y quebradas son muy chicos para su grilla de ~5 km)."""
        river = next(((c, p, d) for c, p, d in measured if c.kind is WatercourseKind.RIO), None)
        if self._discharge is None or river is None:
            return None
        course, point, distance = river
        try:
            flow = await self._discharge.get_river_discharge(point, forecast_days=7)
        except ExternalDataUnavailableError as exc:
            logger.info("Sin caudal GloFAS para %s: %s", course.name, exc)
            missing.append("caudal")
            return None
        today = flow.discharge_m3s[0]
        peak = flow.peak_discharge_m3s
        return RiverFlowSummary(
            name=course.name,
            distance_km=round(distance, 2),
            today_m3s=round(today, 1),
            peak_m3s=round(peak, 1),
            peak_date=flow.peak_date,
            trend=FlowTrend.from_ratio(peak / today) if today > 0 else FlowTrend.ESTABLE,
        )

    async def _proximity(
        self, location: Coordinates, measured: Sequence[_Measured], missing: list[str]
    ) -> tuple[WatercourseProximity | None, float | None]:
        """Cauce que gobierna el riesgo (menor distancia ponderada por tipo) y elevación del usuario."""
        if not measured:
            try:
                (user_elev,) = await self._elevation.get_elevations([location])
                return None, round(user_elev, 1)
            except (ExternalDataUnavailableError, ValueError):
                missing.append("elevacion")
                return None, None

        cfg = self._exposure.config
        course, nearest, distance = min(measured, key=lambda m: cfg.effective_distance(m[2], m[0].kind))

        relative_height: float | None = None
        user_elevation: float | None = None
        try:
            user_elev, channel_elev = await self._elevation.get_elevations([location, nearest])
            relative_height = user_elev - channel_elev
            user_elevation = round(user_elev, 1)
        except (ExternalDataUnavailableError, ValueError) as exc:
            logger.warning("Elevación no disponible en %s: %s", location, exc)
            missing.append("elevacion")

        proximity = WatercourseProximity(
            name=course.name,
            kind=course.kind,
            distance_km=round(distance, 3),
            relative_height_m=None if relative_height is None else round(relative_height, 1),
            nearest_point=nearest,
            geometry_source=course.geometry_source,
        )
        return proximity, user_elevation

    async def _nearest_safe_points(
        self, location: Coordinates, threat: ThreatType, missing: list[str]
    ) -> tuple[NearbySafePoint, ...]:
        points = await self._guard(
            "puntos_seguros",
            self._safe_points.find_nearest(location, threat, limit=3, max_distance_km=self._safe_point_km),
            [],
            missing,
        )
        return tuple(
            NearbySafePoint(
                id=p.id,
                name=p.name,
                commune=p.commune,
                location=p.location,
                distance_km=round(p.distance_km(location), 2),
                available_capacity=p.available_capacity,
            )
            for p in points
        )

    @staticmethod
    def _threat(
        alerts: Sequence[Alert], consensus: ConsensusResult | None, courses: Sequence[Watercourse]
    ) -> ThreatType:
        if alerts:
            return alerts[0].threat_type
        if consensus is not None and consensus.flood_risk:
            return consensus.threat_type
        if courses:
            return courses[0].kind.primary_threat
        return ThreatType.DESBORDE_RIO

    @staticmethod
    async def _guard(source: str, awaitable: Awaitable[T], default: T, missing: list[str]) -> T:
        """Ejecuta una consulta; si la fuente no está disponible la registra y devuelve ``default``.

        Sólo se capturan indisponibilidades declaradas por los adaptadores:
        cualquier otro error es un defecto y debe propagarse.
        """
        try:
            return await awaitable
        except ExternalDataUnavailableError as exc:
            logger.warning("Fuente '%s' no disponible: %s", source, exc)
            missing.append(source)
            return default


def _fold(text: str) -> str:
    """Compara nombres de comuna sin tildes ni mayúsculas («Rio Claro» = «Río Claro»)."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


_OFFICIAL_TO_RISK = {OfficialLevel.AMARILLA: RiskLevel.AMARILLO, OfficialLevel.ROJA: RiskLevel.ROJO}

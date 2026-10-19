"""Objetos de transferencia (salida de los casos de uso).

Son modelos Pydantic inmutables: la capa de presentación puede devolverlos
tal cual como ``response_model`` sin exponer entidades mutables.
"""

from __future__ import annotations

from typing import Literal

from collections.abc import Callable
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.domain.entities.alert import Alert, AlertKind
from src.domain.entities.citizen_report import ReportStatus
from src.domain.entities.geo_zone import GeoZone, MauleProvince
from src.domain.entities.watercourse import WatercourseKind
from src.domain.services.debris_flow import DebrisFlowAssessment
from src.domain.services.exposure_assessment import ExposureLevel
from src.domain.services.landslide import LandslideAssessment
from src.domain.services.probability import ProbabilityOutlook
from src.domain.value_objects.admin_area import AdminArea, MacroZone
from src.domain.value_objects.emergency_service import ServiceKind
from src.domain.value_objects.locality import PlaceKind
from src.domain.value_objects.model_study import ModelStudy
from src.domain.value_objects.official_alert import OfficialAlert, OfficialLevel
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.forecast import ConsensusResult, HydrologicalConditions
from src.domain.value_objects.risk_level import RiskLevel
from src.domain.value_objects.threat_type import ThreatType, WeatherModel

Clock = Callable[[], datetime]
"""Fuente de tiempo inyectable (facilita pruebas deterministas)."""


class _DTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DataQuality(StrEnum):
    """Completitud de las fuentes usadas en un diagnóstico."""

    COMPLETA = "COMPLETA"
    PARCIAL = "PARCIAL"


class ZoneSummary(_DTO):
    """Resumen de una zona."""

    id: UUID
    name: str
    commune: str
    province: MauleProvince
    risk_level: RiskLevel

    @classmethod
    def from_zone(cls, zone: GeoZone) -> ZoneSummary:
        return cls(
            id=zone.id,
            name=zone.name,
            commune=zone.commune,
            province=zone.province,
            risk_level=zone.current_risk_level,
        )


class AlertBrief(_DTO):
    """Resumen de una alerta activa."""

    id: UUID
    kind: AlertKind
    risk_level: RiskLevel
    threat_type: ThreatType
    title: str
    message: str
    valid_until: datetime
    zone_name: str | None = None
    """Sector de la alerta (zona monitoreada) cuando es de otra zona de la comuna."""

    @classmethod
    def from_alert(cls, alert: Alert, zone_name: str | None = None) -> AlertBrief:
        return cls(
            id=alert.id,
            kind=alert.kind,
            risk_level=alert.risk_level,
            threat_type=alert.threat_type,
            title=alert.title,
            message=alert.message,
            valid_until=alert.valid_until,
            zone_name=zone_name,
        )


class NearbySafePoint(_DTO):
    """Punto seguro sugerido."""

    id: UUID
    name: str
    commune: str
    location: Coordinates
    distance_km: float
    available_capacity: int


class WatercourseProximity(_DTO):
    """Relación entre el usuario y el cauce más cercano."""

    name: str
    kind: WatercourseKind
    distance_km: float
    relative_height_m: float | None
    nearest_point: Coordinates
    geometry_source: str


class NearbyWatercourse(_DTO):
    """Río, estero, quebrada o canal cercano al usuario (con su trazado para el mapa)."""

    name: str
    kind: WatercourseKind
    distance_km: float
    path: tuple[tuple[float, float], ...]
    geometry_source: str


class OfficialAlertBrief(_DTO):
    """Alerta oficial de SENAPRED que aplica a la región del usuario."""

    title: str
    level: OfficialLevel
    hazard: str
    scope: str
    applies_to: Literal["COMUNA", "TODA_REGION", "REGION"]
    """``COMUNA`` si nombra la comuna del usuario; ``TODA_REGION`` si rige en toda la región (también
    la incluye); ``REGION`` si es para provincias o zonas de la región que no se pueden confirmar."""
    summary: str
    published_at: datetime
    url: str

    @classmethod
    def from_alert(cls, alert: OfficialAlert, applies_to: Literal["COMUNA", "TODA_REGION", "REGION"]) -> OfficialAlertBrief:
        return cls(
            title=alert.title,
            level=alert.level,
            hazard=alert.hazard,
            scope=alert.scope,
            applies_to=applies_to,
            summary=alert.summary,
            published_at=alert.published_at,
            url=alert.url,
        )


class LocalitySummary(_DTO):
    """Nombre del lugar: localidad más cercana (pueblo, villa, sector o caserío)."""

    name: str
    kind: PlaceKind
    distance_km: float
    near: bool
    """``True`` si está a más de 3 km: se muestra «Cerca de …»."""


class MunicipalPhone(_DTO):
    """Línea de seguridad municipal de la comuna del usuario."""

    comuna: str
    phone: str
    verified: bool = False
    """``False`` mientras no se confirme con la municipalidad."""
    note: str | None = None


class NearbyService(_DTO):
    """Servicio de emergencia cercano al usuario."""

    kind: ServiceKind
    name: str
    phone: str | None
    distance_km: float
    latitude: float
    longitude: float
    detail: str | None = None


class ComunaOutlook(_DTO):
    """Panorama de una comuna dentro de la vista regional."""

    comuna: str
    level: RiskLevel
    rain_mm: float
    max_hourly_mm: float
    max_wind_gust_kmh: float | None = None
    storm: bool = False
    snow: bool = False
    """La isoterma está baja: en altura cae nieve y no corre agua."""
    official_level: OfficialLevel | None = None
    """Alerta de SENAPRED más alta que rige en la comuna (por nombre o para toda la región)."""
    official_hazard: str | None = None


class RegionOverview(_DTO):
    """Comunas de una región ordenadas de mayor a menor riesgo por lluvia."""

    region: str
    region_code: str
    evaluated_at: datetime
    thresholds_mm: tuple[float, float, float]
    """Umbrales AMARILLO, NARANJA, ROJO de lluvia en 24 h de esta macrozona."""
    comunas: list[ComunaOutlook]


class WindyStudy(_DTO):
    """Estudio del punto con los modelos de Windy (próximas 24 h)."""

    evaluated_at: datetime
    models: list[ModelStudy]
    storm_models: int
    """Cuántos modelos ven al menos un bloque de 3 h con tormenta."""
    verdict: str
    source: str = "Windy.com Point Forecast API"


class PlaceOption(_DTO):
    """Comuna o localidad elegible como ubicación, con un punto para consultar su riesgo."""

    name: str
    kind: str = "comuna"
    """``comuna`` o tipo de localidad (``city``, ``town``, ``village``, ``hamlet``...)."""
    comuna: str
    region: str
    latitude: float
    longitude: float

    @classmethod
    def from_area(cls, area: AdminArea) -> PlaceOption:
        point = area.representative_point
        return cls(
            name=area.comuna, comuna=area.comuna, region=area.region, latitude=point.latitude, longitude=point.longitude
        )


class AreaSummary(_DTO):
    """Comuna y región donde está el usuario."""

    comuna: str
    region: str
    region_code: str

    @classmethod
    def from_area(cls, area: AdminArea) -> AreaSummary:
        return cls(comuna=area.comuna, region=area.region, region_code=area.region_code)


class FlowTrend(StrEnum):
    """Tendencia del caudal: pico de los próximos días frente al de hoy."""

    SUBE_FUERTE = "SUBE_FUERTE"
    SUBE = "SUBE"
    ESTABLE = "ESTABLE"
    BAJA = "BAJA"

    @classmethod
    def from_ratio(cls, ratio: float) -> FlowTrend:
        if ratio >= 2.0:
            return cls.SUBE_FUERTE
        if ratio >= 1.3:
            return cls.SUBE
        if ratio <= 0.8:
            return cls.BAJA
        return cls.ESTABLE


class RiverFlowSummary(_DTO):
    """Caudal pronosticado (GloFAS) del río más cercano."""

    name: str
    distance_km: float
    today_m3s: float
    peak_m3s: float
    peak_date: date
    trend: FlowTrend


class ModelRain(_DTO):
    """Lluvia de 24 h que pronostica un modelo global."""

    model: WeatherModel
    precipitation_mm: float


class HourlyRain(_DTO):
    """Lluvia de consenso (media ponderada de los modelos) en una hora."""

    time: datetime
    precipitation_mm: float


class StormWindow(_DTO):
    """Horas en que la convección (CAPE) alcanza el umbral de tormenta eléctrica."""

    start: datetime
    end: datetime


class RainThresholds(_DTO):
    """Umbrales de aluvión/deslizamiento de la macrozona del usuario (para la interfaz)."""

    amarillo_mm: float
    naranja_mm: float
    rojo_mm: float
    min_freezing_level_m: float
    intense_hourly_mm: float


class GpsRiskDiagnosis(_DTO):
    """Diagnóstico personalizado para una coordenada GPS."""

    location: Coordinates
    evaluated_at: datetime
    within_maule: bool
    within_chile: bool = True
    area: AreaSummary | None = None
    macrozone: MacroZone | None = None
    landslide: LandslideAssessment | None = None
    river_flow: RiverFlowSummary | None = None
    official_alerts: tuple[OfficialAlertBrief, ...] = ()
    """Alertas de SENAPRED para su comuna o región: mandan sobre el cálculo propio."""
    locality: LocalitySummary | None = None
    comuna_alerts: tuple[AlertBrief, ...] = ()
    """Alertas vigentes de otros sectores de la misma comuna (informativas)."""
    municipal_phone: MunicipalPhone | None = None
    emergency_services: tuple[NearbyService, ...] = ()
    probabilities: ProbabilityOutlook | None = None
    conditions: HydrologicalConditions | None = None
    model_precipitation: tuple[ModelRain, ...] = ()
    rain_thresholds: RainThresholds | None = None
    hourly_rain: tuple[HourlyRain, ...] = ()
    storm_window: StormWindow | None = None
    zones: tuple[ZoneSummary, ...]
    hazard_level: RiskLevel
    exposure: ExposureLevel
    personal_risk_level: RiskLevel
    watercourse: WatercourseProximity | None
    nearby_watercourses: tuple[NearbyWatercourse, ...] = ()
    elevation_m: float | None = None
    debris_flow: DebrisFlowAssessment | None = None
    consensus: ConsensusResult | None
    active_alerts: tuple[AlertBrief, ...]
    recommendations: tuple[str, ...]
    safe_points: tuple[NearbySafePoint, ...]
    data_quality: DataQuality
    missing_sources: tuple[str, ...]


class DispatchSummary(_DTO):
    """Resultado de un despacho de alerta."""

    alert_id: UUID
    candidates: int
    suppressed_by_cooldown: int
    sent: int
    delivered: int
    failed: int
    expired_subscriptions: int


class CrowdReportOutcome(_DTO):
    """Resultado de procesar un reporte ciudadano."""

    report_id: UUID
    status: ReportStatus
    corroborated: bool
    distinct_reporters: int
    required_reporters: int
    alert_id: UUID | None
    dispatch: DispatchSummary | None
    message: str


class ZoneStatus(_DTO):
    """Estado vigente de una zona para el tablero público."""

    zone: ZoneSummary
    centroid: Coordinates
    effective_risk_level: RiskLevel
    active_alerts: tuple[AlertBrief, ...]


class MapZone(_DTO):
    """Zona para el mapa: polígono como anillo de pares ``[lat, lon]`` (formato Leaflet)."""

    id: UUID
    name: str
    commune: str
    risk_level: RiskLevel
    ring: tuple[tuple[float, float], ...]


class MapWatercourse(_DTO):
    """Cauce para el mapa (pares ``[lat, lon]``)."""

    name: str
    kind: WatercourseKind
    path: tuple[tuple[float, float], ...]
    geometry_source: str


class MapSafePoint(_DTO):
    """Punto seguro para el mapa."""

    id: UUID
    name: str
    commune: str
    lat: float
    lon: float
    capacity: int
    available_capacity: int
    is_operational: bool


class MapLayers(_DTO):
    """Capas del mapa público; se cachean offline en la PWA."""

    generated_at: datetime
    zones: tuple[MapZone, ...]
    watercourses: tuple[MapWatercourse, ...]
    safe_points: tuple[MapSafePoint, ...]


class ZoneMonitorResult(_DTO):
    """Resultado de evaluar una zona en el monitor periódico."""

    zone_id: UUID
    zone_name: str
    risk_level: RiskLevel | None
    action: str
    dispatch: DispatchSummary | None = None
    error: str | None = None
    debris_level: RiskLevel | None = None
    """Nivel del detonante de aluvión; ``None`` si la zona no tiene quebradas."""
    debris_action: str | None = None
    debris_dispatch: DispatchSummary | None = None


class MonitorSummary(_DTO):
    """Resultado de una pasada completa del monitor."""

    started_at: datetime
    zones: tuple[ZoneMonitorResult, ...]


class SubscriptionResult(_DTO):
    """Resultado del registro de un suscriptor."""

    subscriber_id: UUID
    zone_ids: tuple[UUID, ...]
    min_risk_level: RiskLevel
    created: bool

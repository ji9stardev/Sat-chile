"""Composición de dependencias (*composition root*).

Es el único lugar que conoce a la vez infraestructura y aplicación: crea
adaptadores concretos y los inyecta en los casos de uso. Los routers sólo
reciben casos de uso, nunca repositorios ni clientes HTTP.
"""

from __future__ import annotations

import httpx

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from src.application.use_cases import (
    DispatchAlertsUseCase,
    EvaluateGpsRiskUseCase,
    GetMapLayersUseCase,
    GetZonesStatusUseCase,
    MonitorZonesUseCase,
    ProcessCrowdsourcingUseCase,
    RegionOverviewUseCase,
    WindyStudyUseCase,
    RegisterSubscriberUseCase,
)
from src.domain.repositories import NotificationGateway
from src.domain.services import (
    ConsensusEngineService,
    CooldownPolicy,
    CrowdValidationService,
    ExposureAssessmentService,
)
from src.domain.services.regional_thresholds import RegionalRules
from src.infrastructure.config import Settings
from src.infrastructure.database.mongo_client import Collections, MongoConnection
from src.infrastructure.database.repositories import (
    MongoAlertRepository,
    MongoCitizenReportRepository,
    MongoGeoZoneRepository,
    MongoNotificationLogRepository,
    MongoSafePointRepository,
    MongoSubscriberRepository,
    MongoWatercourseRepository,
)
from src.infrastructure.external_apis import CachedWeatherProvider, OpenMeteoClient
from src.infrastructure.external_apis.senapred_client import SenapredClient
from src.infrastructure.external_apis.windy_client import windy_from_settings
from src.infrastructure.geodata import (
    InMemoryAdminAreaLocator,
    InMemoryEmergencyServiceFinder,
    InMemoryLocalityFinder,
    chile_comunas,
    load_localities,
    load_municipal_phones,
    load_services,
)
from src.infrastructure.notifications import DisabledNotificationGateway, WebPushAdapter

logger = logging.getLogger(__name__)


@dataclass
class Container:
    """Casos de uso listos para los routers y recursos a liberar al apagar."""

    evaluate_gps_risk: EvaluateGpsRiskUseCase
    process_crowdsourcing: ProcessCrowdsourcingUseCase
    dispatch_alerts: DispatchAlertsUseCase
    register_subscriber: RegisterSubscriberUseCase
    get_zones_status: GetZonesStatusUseCase
    get_map_layers: GetMapLayersUseCase
    monitor_zones: MonitorZonesUseCase
    region_overview: RegionOverviewUseCase
    windy_study: WindyStudyUseCase
    health_check: Callable[[], Awaitable[dict[str, bool]]]
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    async def close(self) -> None:
        """Libera conexiones (Mongo, HTTP)."""
        for closer in reversed(self.closers):
            try:
                await closer()
            except Exception:  # noqa: BLE001 - el apagado debe continuar
                logger.exception("Error liberando recursos")


async def build_container(settings: Settings) -> Container:
    """Crea todas las dependencias reales.

    Si MongoDB no responde al iniciar, la API arranca igual (perfil AP): los
    endpoints que dependen de la base se degradan y ``/api/health`` lo informa.
    """
    if settings.reporter_hash_secret.get_secret_value() == "cambiar-en-produccion":
        logger.warning("SAT_REPORTER_HASH_SECRET usa el valor por defecto: defínalo antes de producción")
    mongo = MongoConnection.from_settings(settings)
    if await mongo.ping():
        try:
            await mongo.ensure_indexes()
        except Exception:  # noqa: BLE001 - índices no deben impedir el arranque
            logger.exception("No se pudieron asegurar los índices de MongoDB")
    else:
        logger.warning("MongoDB no disponible al iniciar; la API arranca en modo degradado")

    zones = MongoGeoZoneRepository(mongo.collection(Collections.GEO_ZONES))
    alerts = MongoAlertRepository(mongo.collection(Collections.ALERTS))
    reports = MongoCitizenReportRepository(mongo.collection(Collections.CITIZEN_REPORTS))
    safe_points = MongoSafePointRepository(mongo.collection(Collections.SAFE_POINTS))
    subscribers = MongoSubscriberRepository(mongo.collection(Collections.SUBSCRIBERS))
    watercourses = MongoWatercourseRepository(mongo.collection(Collections.WATERCOURSES))
    notification_log = MongoNotificationLogRepository(mongo.collection(Collections.NOTIFICATION_LOG))

    meteo = OpenMeteoClient.from_settings(settings)
    weather = CachedWeatherProvider(meteo, ttl_s=settings.weather_cache_ttl_s)

    gateway: NotificationGateway
    if settings.vapid_private_key is not None and settings.vapid_public_key:
        gateway = WebPushAdapter.from_settings(settings)
    else:
        logger.warning("Claves VAPID no configuradas: las notificaciones push están deshabilitadas")
        gateway = DisabledNotificationGateway()
    push_enabled = not isinstance(gateway, DisabledNotificationGateway)

    engine = ConsensusEngineService()
    dispatcher = DispatchAlertsUseCase(
        subscribers=subscribers, zones=zones, log=notification_log, gateway=gateway, cooldown=CooldownPolicy()
    )

    async def health() -> dict[str, bool]:
        return {"mongo": await mongo.ping(), "web_push": push_enabled}

    async def close_mongo() -> None:
        mongo.close()

    windy = windy_from_settings(settings)
    senapred = SenapredClient(httpx.AsyncClient(timeout=httpx.Timeout(20.0)))
    return Container(
        evaluate_gps_risk=EvaluateGpsRiskUseCase(
            zones=zones,
            alerts=alerts,
            watercourses=watercourses,
            safe_points=safe_points,
            weather=weather,
            elevation=meteo,
            engine=engine,
            exposure=ExposureAssessmentService(),
            locator=InMemoryAdminAreaLocator(chile_comunas()),
            regional=RegionalRules(),
            discharge=meteo,
            ensemble=meteo,
            services=InMemoryEmergencyServiceFinder(load_services()),
            municipal_phones=load_municipal_phones(),
            localities=InMemoryLocalityFinder(load_localities()),
            official=senapred,
        ),
        process_crowdsourcing=ProcessCrowdsourcingUseCase(
            reports=reports, zones=zones, alerts=alerts, validator=CrowdValidationService(), dispatcher=dispatcher
        ),
        dispatch_alerts=dispatcher,
        register_subscriber=RegisterSubscriberUseCase(subscribers=subscribers, zones=zones),
        get_zones_status=GetZonesStatusUseCase(zones=zones, alerts=alerts),
        get_map_layers=GetMapLayersUseCase(
            zones=zones, alerts=alerts, watercourses=watercourses, safe_points=safe_points
        ),
        monitor_zones=MonitorZonesUseCase(
            zones=zones, alerts=alerts, weather=weather, engine=engine, dispatcher=dispatcher,
            ensemble=meteo,
            watercourses=watercourses,
        ),
        region_overview=RegionOverviewUseCase(areas=chile_comunas(), provider=meteo),
        windy_study=WindyStudyUseCase(windy),
        health_check=health,
        closers=[close_mongo, meteo.aclose, *([windy.aclose] if windy else [])],
    )

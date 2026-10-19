"""Implementaciones en memoria de los puertos del dominio, para pruebas."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from src.domain.services.landslide import RING_POINTS
from src.domain.entities import (
    Alert,
    AlertStatus,
    CitizenReport,
    Entity,
    GeoZone,
    MauleProvince,
    ReportStatus,
    SafePoint,
    Subscriber,
    Watercourse,
    WatercourseKind,
)
from src.domain.exceptions import ExternalDataUnavailableError
from src.domain.repositories import (
    AlertRepository,
    CitizenReportRepository,
    GeoZoneRepository,
    NotificationLogRepository,
    SafePointRepository,
    SubscriberRepository,
    WatercourseRepository,
)
from src.domain.value_objects import (
    Coordinates,
    DeliveryResult,
    DeliveryStatus,
    HydrologicalConditions,
    ModelPrecipitationForecast,
    NotificationRecord,
    PushNotification,
    PushSubscription,
    RiskLevel,
    ThreatType,
    WeatherModel,
)

E = TypeVar("E", bound=Entity)


class InMemoryRepo(Generic[E]):
    def __init__(self, items: Sequence[E] = ()) -> None:
        self.items: dict[UUID, E] = {i.id: i.model_copy(deep=True) for i in items}

    async def get_by_id(self, entity_id: UUID) -> E | None:
        item = self.items.get(entity_id)
        return item.model_copy(deep=True) if item else None

    async def save(self, entity: E) -> None:
        self.items[entity.id] = entity.model_copy(deep=True)

    async def delete(self, entity_id: UUID) -> bool:
        return self.items.pop(entity_id, None) is not None

    def all(self) -> list[E]:
        return [i.model_copy(deep=True) for i in self.items.values()]


class FakeZones(InMemoryRepo[GeoZone], GeoZoneRepository):
    down: bool = False
    """``True`` simula MongoDB inalcanzable."""

    async def list_all(self) -> Sequence[GeoZone]:
        if self.down:
            raise ExternalDataUnavailableError("MongoDB no está disponible.")
        return sorted(self.all(), key=lambda z: z.name)

    async def find_by_province(self, province: MauleProvince) -> Sequence[GeoZone]:
        return [z for z in self.all() if z.province is province]

    async def find_containing(self, point: Coordinates) -> Sequence[GeoZone]:
        return [z for z in self.all() if z.contains(point)]


class FakeAlerts(InMemoryRepo[Alert], AlertRepository):
    async def find_active_by_zone(self, zone_id: UUID, at: datetime) -> Sequence[Alert]:
        return [a for a in self.all() if a.zone_id == zone_id and a.is_active(at)]

    async def find_active_at_or_above(self, level: RiskLevel, at: datetime) -> Sequence[Alert]:
        return [a for a in self.all() if a.risk_level >= level and a.is_active(at)]

    async def find_history(self, zone_id: UUID, since: datetime, until: datetime, limit: int = 100) -> Sequence[Alert]:
        return [a for a in self.all() if a.zone_id == zone_id][:limit]


class FakeReports(InMemoryRepo[CitizenReport], CitizenReportRepository):
    async def find_by_status(self, status: ReportStatus, limit: int = 50) -> Sequence[CitizenReport]:
        return [r for r in self.all() if r.status is status][:limit]

    async def find_by_reporter(self, reporter_hash: str, since: datetime) -> Sequence[CitizenReport]:
        return [r for r in self.all() if r.reporter_hash == reporter_hash and r.reported_at >= since]

    async def find_near(
        self, center: Coordinates, radius_km: float, since: datetime, threat_type: ThreatType | None = None
    ) -> Sequence[CitizenReport]:
        return [
            r for r in self.all()
            if r.location.distance_km(center) <= radius_km and r.reported_at >= since
            and (threat_type is None or r.threat_type is threat_type)
        ]


class FakeSafePoints(InMemoryRepo[SafePoint], SafePointRepository):
    async def find_nearest(
        self, origin: Coordinates, threat: ThreatType, limit: int = 5, max_distance_km: float = 20.0
    ) -> Sequence[SafePoint]:
        ok = [p for p in self.all() if p.is_safe_for(threat) and p.distance_km(origin) <= max_distance_km]
        return sorted(ok, key=lambda p: p.distance_km(origin))[:limit]

    async def find_by_commune(self, commune: str) -> Sequence[SafePoint]:
        return [p for p in self.all() if p.commune == commune]

    async def list_all(self) -> Sequence[SafePoint]:
        return self.all()


class FakeWatercourses(InMemoryRepo[Watercourse], WatercourseRepository):
    """En memoria, con prefiltro por caja envolvente (miles de cauces en la demo)."""

    def __init__(self, items: Sequence[Watercourse] = ()) -> None:
        # Sin copia profunda: los cauces no se modifican y pueden ser miles.
        self.items = {i.id: i for i in items}
        self._boxes = {
            i.id: (
                min(v.latitude for v in i.geometry.vertices), max(v.latitude for v in i.geometry.vertices),
                min(v.longitude for v in i.geometry.vertices), max(v.longitude for v in i.geometry.vertices),
            )
            for i in items
        }

    async def find_near(self, point: Coordinates, max_distance_km: float, limit: int = 5) -> Sequence[Watercourse]:
        dlat = max_distance_km / 110.0
        dlon = max_distance_km / 80.0  # a ~-35° de latitud 1° de longitud ≈ 91 km; margen holgado
        candidates = [
            w for w in self.items.values()
            if self._boxes[w.id][0] - dlat <= point.latitude <= self._boxes[w.id][1] + dlat
            and self._boxes[w.id][2] - dlon <= point.longitude <= self._boxes[w.id][3] + dlon
        ]
        measured = [(w, w.geometry.nearest_point(point)[1]) for w in candidates]
        return [w for w, d in sorted(measured, key=lambda m: m[1]) if d <= max_distance_km][:limit]

    async def list_all(self) -> Sequence[Watercourse]:
        return list(self.items.values())

    async def list_by_kinds(self, kinds: Sequence[WatercourseKind]) -> Sequence[Watercourse]:
        return [w for w in self.items.values() if w.kind in kinds]


class FakeSubscribers(InMemoryRepo[Subscriber], SubscriberRepository):
    async def find_for_zone(self, zone_id: UUID, level: RiskLevel) -> Sequence[Subscriber]:
        return [s for s in self.all() if zone_id in s.zone_ids and s.wants(level)]

    async def find_near(self, center: Coordinates, radius_km: float, level: RiskLevel) -> Sequence[Subscriber]:
        return [
            s for s in self.all()
            if s.location is not None and s.location.distance_km(center) <= radius_km and s.wants(level)
        ]

    async def find_by_endpoint(self, endpoint: str) -> Subscriber | None:
        return next((s for s in self.all() if str(s.subscription.endpoint) == endpoint), None)

    async def deactivate_by_endpoint(self, endpoint: str) -> int:
        n = 0
        for s in self.items.values():
            if str(s.subscription.endpoint) == endpoint:
                s.active = False
                n += 1
        return n


class FakeNotificationLog(NotificationLogRepository):
    def __init__(self) -> None:
        self.records: dict[tuple[UUID, UUID], NotificationRecord] = {}

    async def get_last_many(self, subscriber_ids: Sequence[UUID], zone_id: UUID) -> Mapping[UUID, NotificationRecord]:
        return {s: self.records[(s, zone_id)] for s in subscriber_ids if (s, zone_id) in self.records}

    async def record_many(self, records: Sequence[NotificationRecord]) -> None:
        for r in records:
            self.records[(r.subscriber_id, r.zone_id)] = r


class FakeGateway:
    def __init__(self, expired_endpoints: frozenset[str] = frozenset()) -> None:
        self.sent: list[tuple[str, PushNotification]] = []
        self._expired = expired_endpoints

    async def send(self, subscription: PushSubscription, notification: PushNotification) -> DeliveryResult:
        endpoint = str(subscription.endpoint)
        self.sent.append((endpoint, notification))
        status = DeliveryStatus.SUSCRIPCION_EXPIRADA if endpoint in self._expired else DeliveryStatus.ENTREGADA
        return DeliveryResult(endpoint=endpoint, status=status, status_code=410 if endpoint in self._expired else 201)

    async def send_many(
        self, subscriptions: Sequence[PushSubscription], notification: PushNotification
    ) -> Sequence[DeliveryResult]:
        return [await self.send(s, notification) for s in subscriptions]


class FakeWeather:
    """Clima configurable; ``fail=True`` simula Open-Meteo caído."""

    def __init__(self, precip_mm: float = 2.0, conditions: HydrologicalConditions | None = None, fail: bool = False):
        self.precip_mm = precip_mm
        self.conditions = conditions or HydrologicalConditions(freezing_level_m=3000, soil_moisture_m3m3=0.4)
        self.fail = fail
        self.calls = 0

    async def get_precipitation_forecasts(
        self, location: Coordinates, models: Sequence[WeatherModel], horizon_hours: int = 24
    ) -> Sequence[ModelPrecipitationForecast]:
        self.calls += 1
        if self.fail:
            raise ExternalDataUnavailableError("Open-Meteo caído")
        return [
            ModelPrecipitationForecast(model=m, accumulated_precipitation_mm=self.precip_mm, horizon_hours=horizon_hours)
            for m in models
        ]

    async def get_hydrological_conditions(self, location: Coordinates, horizon_hours: int = 24) -> HydrologicalConditions:
        if self.fail:
            raise ExternalDataUnavailableError("Open-Meteo caído")
        return self.conditions


class FakeElevation:
    """Devuelve ``user_m`` para el primer punto y ``channel_m`` para el resto.

    La consulta de pendiente (usuario + anillo de 8 puntos) responde
    ``ring_m`` en el anillo; por defecto, terreno plano.
    """

    def __init__(
        self, user_m: float = 102.0, channel_m: float = 100.0, fail: bool = False, ring_m: float | None = None
    ) -> None:
        self.user_m, self.channel_m, self.fail, self.ring_m = user_m, channel_m, fail, ring_m

    async def get_elevations(self, points: Sequence[Coordinates]) -> Sequence[float]:
        if self.fail:
            raise ExternalDataUnavailableError("DEM caído")
        if len(points) == 1 + RING_POINTS:
            return [self.user_m] + [self.user_m if self.ring_m is None else self.ring_m] * RING_POINTS
        return [self.user_m] + [self.channel_m] * (len(points) - 1)

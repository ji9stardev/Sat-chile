"""Caso de uso: capas del mapa público (zonas, cauces, puntos seguros)."""

from __future__ import annotations

import asyncio

from src.application.dto import Clock, MapLayers, MapSafePoint, MapWatercourse, MapZone
from src.domain.entities.base import utc_now
from src.domain.entities.watercourse import Watercourse
from src.domain.repositories import AlertRepository, GeoZoneRepository, SafePointRepository, WatercourseRepository
from src.domain.value_objects.risk_level import RiskLevel


class GetMapLayersUseCase:
    """Entrega geometrías listas para Leaflet (pares ``[lat, lon]``)."""

    def __init__(
        self,
        *,
        zones: GeoZoneRepository,
        alerts: AlertRepository,
        watercourses: WatercourseRepository,
        safe_points: SafePointRepository,
        clock: Clock = utc_now,
    ) -> None:
        self._zones = zones
        self._alerts = alerts
        self._watercourses = watercourses
        self._safe_points = safe_points
        self._clock = clock

    async def execute(self) -> MapLayers:
        now = self._clock()
        # Los cauces NO van en el mapa general: con la red de todo Chile (miles de ríos y
        # decenas de miles de esteros) no cabe en la memoria de un servidor pequeño. Cada
        # usuario recibe los cauces cercanos a su punto en el diagnóstico GPS.
        zones, points = await asyncio.gather(self._zones.list_all(), self._safe_points.list_all())
        courses: tuple[Watercourse, ...] = ()
        alert_lists = await asyncio.gather(*(self._alerts.find_active_by_zone(z.id, now) for z in zones))
        return MapLayers(
            generated_at=now,
            zones=tuple(
                MapZone(
                    id=z.id,
                    name=z.name,
                    commune=z.commune,
                    risk_level=RiskLevel.most_severe(z.current_risk_level, *(a.risk_level for a in alerts)),
                    ring=tuple((c.latitude, c.longitude) for c in z.boundary.ring),
                )
                for z, alerts in zip(zones, alert_lists, strict=True)
            ),
            watercourses=tuple(
                MapWatercourse(
                    name=w.name,
                    kind=w.kind,
                    path=tuple((c.latitude, c.longitude) for c in w.geometry.vertices),
                    geometry_source=w.geometry_source,
                )
                for w in courses
            ),
            safe_points=tuple(
                MapSafePoint(
                    id=p.id,
                    name=p.name,
                    commune=p.commune,
                    lat=p.location.latitude,
                    lon=p.location.longitude,
                    capacity=p.capacity,
                    available_capacity=p.available_capacity,
                    is_operational=p.is_operational,
                )
                for p in points
            ),
        )

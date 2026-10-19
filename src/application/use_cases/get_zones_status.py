"""Caso de uso: estado vigente de las zonas monitoreadas."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from src.application.dto import AlertBrief, Clock, ZoneStatus, ZoneSummary
from src.domain.entities.alert import Alert
from src.domain.entities.base import utc_now
from src.domain.entities.geo_zone import GeoZone
from src.domain.repositories import AlertRepository, GeoZoneRepository
from src.domain.value_objects.risk_level import RiskLevel


class GetZonesStatusUseCase:
    """Combina el nivel de cada zona con sus alertas activas (se informa el más severo)."""

    def __init__(self, *, zones: GeoZoneRepository, alerts: AlertRepository, clock: Clock = utc_now) -> None:
        self._zones = zones
        self._alerts = alerts
        self._clock = clock

    async def execute(self, names: Sequence[str] = ()) -> list[ZoneStatus]:
        """Zonas ordenadas de mayor a menor riesgo; ``names`` filtra sin distinguir mayúsculas."""
        now = self._clock()
        zones = list(await self._zones.list_all())
        if names:
            wanted = {n.strip().casefold() for n in names}
            zones = [z for z in zones if z.name.casefold() in wanted or z.commune.casefold() in wanted]
        alert_lists = await asyncio.gather(*(self._alerts.find_active_by_zone(z.id, now) for z in zones))
        statuses = [self._status(z, a) for z, a in zip(zones, alert_lists, strict=True)]
        return sorted(statuses, key=lambda s: (-s.effective_risk_level.severity, s.zone.name))

    @staticmethod
    def _status(zone: GeoZone, alerts: Sequence[Alert]) -> ZoneStatus:
        briefs = tuple(AlertBrief.from_alert(a) for a in sorted(alerts, key=lambda a: a.risk_level, reverse=True))
        return ZoneStatus(
            zone=ZoneSummary.from_zone(zone),
            centroid=zone.centroid,
            effective_risk_level=RiskLevel.most_severe(zone.current_risk_level, *(b.risk_level for b in briefs)),
            active_alerts=briefs,
        )

"""Caso de uso: despacho de una alerta por Web Push con anti-spam y mensaje adaptativo.

1. Destinatarios: suscriptores que siguen la zona **y** los que declararon
   una ubicación a menos de ``nearby_radius_km`` del centro de la zona,
   siempre que acepten el nivel (``min_risk_level``).
   Una alerta de aluvión sólo llega a quien declaró una ubicación a menos de
   ``debris_radius_km`` de una quebrada (o no declaró ubicación): avisar de
   aluvión a todo el valle es ruido.
2. Anti-spam: :class:`CooldownPolicy` contra el último envío por
   (suscriptor, zona). Los escalamientos pasan siempre.
3. Mensaje adaptativo por suscriptor:
   * escalamiento/desescalamiento explícito en el título ("Sube a ROJO");
   * distancia del suscriptor a la zona, si declaró ubicación;
   * TTL = vigencia restante de la alerta (un aviso vencido no se entrega);
   * urgencia por nivel (la aplica el adaptador Web Push).
4. Suscripciones expiradas (404/410) se desactivan; sólo los envíos
   entregados se registran en el log del cooldown.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime

from src.application.dto import Clock, DispatchSummary
from src.domain.entities.alert import Alert
from src.domain.entities.base import utc_now
from src.domain.entities.geo_zone import GeoZone
from src.domain.entities.subscriber import Subscriber
from src.domain.entities.watercourse import WatercourseKind
from src.domain.exceptions import ExternalDataUnavailableError
from src.domain.repositories import (
    GeoZoneRepository,
    NotificationGateway,
    NotificationLogRepository,
    SubscriberRepository,
    WatercourseRepository,
)
from src.domain.services.cooldown_policy import CooldownPolicy, NotificationDecision
from src.domain.value_objects.threat_type import ThreatType
from src.domain.value_objects.notification import (
    DeliveryResult,
    DeliveryStatus,
    NotificationRecord,
    PushNotification,
)

logger = logging.getLogger(__name__)

_TITLE_PREFIX: dict[NotificationDecision, str] = {
    NotificationDecision.ESCALAMIENTO: "⬆ Sube a {level}: ",
    NotificationDecision.DESESCALAMIENTO: "⬇ Baja a {level}: ",
    NotificationDecision.RECORDATORIO: "Recordatorio: ",
}


class DispatchAlertsUseCase:
    """Envía una alerta activa a los suscriptores que corresponda."""

    def __init__(
        self,
        *,
        subscribers: SubscriberRepository,
        zones: GeoZoneRepository,
        log: NotificationLogRepository,
        gateway: NotificationGateway,
        cooldown: CooldownPolicy,
        clock: Clock = utc_now,
        nearby_radius_km: float = 5.0,
        watercourses: WatercourseRepository | None = None,
        debris_radius_km: float = 1.0,
    ) -> None:
        self._subscribers = subscribers
        self._zones = zones
        self._log = log
        self._gateway = gateway
        self._cooldown = cooldown
        self._clock = clock
        self._nearby_km = nearby_radius_km
        self._watercourses = watercourses
        self._debris_km = debris_radius_km

    async def execute(self, alert: Alert) -> DispatchSummary:
        """Despacha ``alert``. Si no está vigente, no envía nada."""
        now = self._clock()
        if not alert.is_active(now):
            logger.info("Alerta %s no vigente (%s); no se despacha", alert.id, alert.status)
            return DispatchSummary(
                alert_id=alert.id, candidates=0, suppressed_by_cooldown=0, sent=0,
                delivered=0, failed=0, expired_subscriptions=0,
            )

        zone = await self._zones.get_by_id(alert.zone_id)
        candidates = await self._candidates(alert, zone)
        last = await self._log.get_last_many([s.id for s in candidates], alert.zone_id)

        planned: list[tuple[Subscriber, PushNotification]] = []
        for sub in candidates:
            decision = self._cooldown.decide(last.get(sub.id), alert.risk_level, now)
            if decision.should_send:
                planned.append((sub, self._personalize(alert, zone, sub, decision, now)))
        suppressed = len(candidates) - len(planned)

        results: Sequence[DeliveryResult] = await asyncio.gather(
            *(self._gateway.send(sub.subscription, notif) for sub, notif in planned)
        )

        delivered: list[NotificationRecord] = []
        expired = failed = 0
        for (sub, _), result in zip(planned, results, strict=True):
            if result.status is DeliveryStatus.ENTREGADA:
                delivered.append(
                    NotificationRecord(
                        subscriber_id=sub.id, zone_id=alert.zone_id, alert_id=alert.id,
                        risk_level=alert.risk_level, sent_at=now,
                    )
                )
            elif result.status is DeliveryStatus.SUSCRIPCION_EXPIRADA:
                expired += 1
                await self._subscribers.deactivate_by_endpoint(result.endpoint)
            else:
                failed += 1
        await self._log.record_many(delivered)

        summary = DispatchSummary(
            alert_id=alert.id,
            candidates=len(candidates),
            suppressed_by_cooldown=suppressed,
            sent=len(planned),
            delivered=len(delivered),
            failed=failed,
            expired_subscriptions=expired,
        )
        logger.info("Despacho alerta %s: %s", alert.id, summary.model_dump())
        return summary

    async def _candidates(self, alert: Alert, zone: GeoZone | None) -> list[Subscriber]:
        lookups = [self._subscribers.find_for_zone(alert.zone_id, alert.risk_level)]
        if zone is not None:
            lookups.append(self._subscribers.find_near(zone.centroid, self._nearby_km, alert.risk_level))
        found = await asyncio.gather(*lookups)
        unique = {s.id: s for group in found for s in group if s.wants(alert.risk_level)}
        if alert.threat_type is ThreatType.REMOCION_EN_MASA:
            keep = await asyncio.gather(*(self._near_quebrada(s) for s in unique.values()))
            return [s for s, ok in zip(unique.values(), keep, strict=True) if ok]
        return list(unique.values())

    async def _near_quebrada(self, sub: Subscriber) -> bool:
        """Sin ubicación declarada o sin datos de cauces se envía igual (ante la duda, avisar)."""
        if self._watercourses is None or sub.location is None:
            return True
        try:
            near = await self._watercourses.find_near(sub.location, self._debris_km, limit=50)
        except ExternalDataUnavailableError:
            return True
        return any(c.kind is WatercourseKind.QUEBRADA for c in near)

    @staticmethod
    def _personalize(
        alert: Alert,
        zone: GeoZone | None,
        sub: Subscriber,
        decision: NotificationDecision,
        now: datetime,
    ) -> PushNotification:
        prefix = _TITLE_PREFIX.get(decision, "").format(level=alert.risk_level.value)
        body = alert.message
        if zone is not None and sub.location is not None:
            if zone.contains(sub.location):
                body = f"Su ubicación registrada está DENTRO de {zone.name}. {body}"
            else:
                km = sub.location.distance_km(zone.centroid)
                body = f"A {km:.1f} km de su ubicación registrada. {body}"
        remaining = int((alert.valid_until - now).total_seconds())
        return PushNotification(
            title=(prefix + alert.title)[:120],
            body=body[:2000],
            risk_level=alert.risk_level,
            # Una etiqueta por amenaza: un aviso de aluvión no reemplaza en pantalla al de desborde.
            tag=f"zona-{alert.zone_id}-{alert.threat_type.value}"[:64],
            url=f"/?alerta={alert.id}",
            ttl_seconds=max(0, min(remaining, 28 * 86_400)),
        )

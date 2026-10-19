"""Caso de uso: reportes vecinales con regla de corroboración (3 vecinos / 2 km).

Flujo:

1. Control de abuso: un mismo dispositivo no puede reportar más de una vez
   cada ``reporter_cooldown`` (10 min por defecto) → :class:`RateLimitedError`.
2. Se persiste el reporte (PENDIENTE).
3. Se buscan reportes recientes en el radio y se aplica
   :class:`CrowdValidationService`.
4. Si queda corroborado:
   * todos los reportes PENDIENTES que lo sustentan pasan a VERIFICADO
     (revisor ``sistema:consenso-vecinal``, auditable);
   * en cada zona que contiene el punto, si no hay ya una alerta activa de
     nivel igual o mayor, se emite una alerta ciudadana (AMARILLO/NARANJA),
     se actualiza el nivel de la zona y se despacha por Web Push.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from src.application.dto import Clock, CrowdReportOutcome, DispatchSummary
from src.application.use_cases.dispatch_alerts import DispatchAlertsUseCase
from src.domain.entities.alert import Alert, AlertKind, AlertSource
from src.domain.entities.base import utc_now
from src.domain.entities.citizen_report import CitizenReport, ReportStatus, WaterLevelObservation
from src.domain.entities.geo_zone import GeoZone
from src.domain.exceptions import RateLimitedError
from src.domain.repositories import AlertRepository, CitizenReportRepository, GeoZoneRepository
from src.domain.services.crowd_validation import CrowdDecision, CrowdValidationService
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.threat_type import ThreatType

logger = logging.getLogger(__name__)

SYSTEM_REVIEWER = "sistema:consenso-vecinal"

_THREAT_LABEL: dict[ThreatType, str] = {
    ThreatType.DESBORDE_RIO: "crecida o desborde de cauce",
    ThreatType.INUNDACION: "inundación",
    ThreatType.REMOCION_EN_MASA: "aluvión o remoción en masa",
}


class WaterLevelReportCommand(BaseModel):
    """Datos de entrada de un reporte vecinal (ya validados por la presentación)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reporter_hash: str = Field(min_length=8, max_length=128)
    location: Coordinates
    observed_level: WaterLevelObservation
    threat_type: ThreatType = ThreatType.DESBORDE_RIO
    description: str = Field(min_length=5, max_length=1000)
    photo_urls: tuple[HttpUrl, ...] = Field(default=(), max_length=5)


class ProcessCrowdsourcingUseCase:
    """Registra y evalúa reportes ciudadanos."""

    def __init__(
        self,
        *,
        reports: CitizenReportRepository,
        zones: GeoZoneRepository,
        alerts: AlertRepository,
        validator: CrowdValidationService,
        dispatcher: DispatchAlertsUseCase,
        clock: Clock = utc_now,
        alert_validity: timedelta = timedelta(hours=6),
    ) -> None:
        self._reports = reports
        self._zones = zones
        self._alerts = alerts
        self._validator = validator
        self._dispatcher = dispatcher
        self._clock = clock
        self._validity = alert_validity

    async def execute(self, command: WaterLevelReportCommand) -> CrowdReportOutcome:
        """Procesa el reporte.

        Raises:
            RateLimitedError: Si el dispositivo reportó dentro del cooldown.
        """
        now = self._clock()
        cfg = self._validator.config

        previous = await self._reports.find_by_reporter(command.reporter_hash, now - cfg.reporter_cooldown)
        if self._validator.is_rate_limited(previous, now):
            minutes = int(cfg.reporter_cooldown.total_seconds() // 60)
            raise RateLimitedError(f"Ya recibimos un reporte suyo. Puede volver a reportar en {minutes} minutos.")

        report = CitizenReport(
            reporter_hash=command.reporter_hash,
            location=command.location,
            threat_type=command.threat_type,
            observed_level=command.observed_level,
            description=command.description,
            photo_urls=command.photo_urls,
            reported_at=now,
        )
        await self._reports.save(report)

        nearby = await self._reports.find_near(command.location, cfg.radius_km, now - cfg.window)
        decision = self._validator.evaluate(report, nearby, now)

        if not decision.corroborated:
            return CrowdReportOutcome(
                report_id=report.id,
                status=report.status,
                corroborated=False,
                distinct_reporters=decision.distinct_reporters,
                required_reporters=decision.required_reporters,
                alert_id=None,
                dispatch=None,
                message=(
                    f"Reporte recibido. {decision.distinct_reporters} de {decision.required_reporters} "
                    "vecinos han reportado en esta zona; se requiere corroboración para emitir una alerta."
                ),
            )

        await self._verify_supporting(decision, {r.id: r for r in [*nearby, report]})
        alert, dispatch = await self._raise_alert(decision, command.location)
        message = (
            f"Reporte corroborado por {decision.distinct_reporters} vecinos. "
            + (
                f"Se emitió una alerta {alert.risk_level.value} para la zona."
                if alert
                else "Ya existe una alerta vigente igual o mayor para la zona, o el punto está fuera de las zonas monitoreadas."
            )
        )
        return CrowdReportOutcome(
            report_id=report.id,
            status=ReportStatus.VERIFICADO,
            corroborated=True,
            distinct_reporters=decision.distinct_reporters,
            required_reporters=decision.required_reporters,
            alert_id=alert.id if alert else None,
            dispatch=dispatch,
            message=message,
        )

    async def _verify_supporting(self, decision: CrowdDecision, pool: dict[UUID, CitizenReport]) -> None:
        for report_id in decision.supporting_report_ids:
            report = pool.get(report_id)
            if report is not None and report.status is ReportStatus.PENDIENTE:
                report.verify(SYSTEM_REVIEWER, f"Corroborado por {decision.distinct_reporters} vecinos en 2 km")
                await self._reports.save(report)

    async def _raise_alert(
        self, decision: CrowdDecision, location: Coordinates
    ) -> tuple[Alert | None, DispatchSummary | None]:
        now = self._clock()
        zones = await self._zones.find_containing(location)
        created: Alert | None = None
        dispatch: DispatchSummary | None = None
        for zone in zones:
            active = await self._alerts.find_active_by_zone(zone.id, now)
            if any(a.risk_level >= decision.suggested_level for a in active):
                continue
            alert = self._build_alert(zone, decision, now)
            alert.activate()
            await self._alerts.save(alert)
            if zone.update_risk(max(zone.current_risk_level, alert.risk_level)):
                await self._zones.save(zone)
            summary = await self._dispatcher.execute(alert)
            if created is None:
                created, dispatch = alert, summary
        return created, dispatch

    def _build_alert(self, zone: GeoZone, decision: CrowdDecision, start: datetime) -> Alert:
        label = _THREAT_LABEL.get(decision.threat_type, decision.threat_type.value.lower())
        return Alert(
            zone_id=zone.id,
            kind=AlertKind.ALERTA,
            risk_level=decision.suggested_level,
            threat_type=decision.threat_type,
            title=f"Alerta ciudadana {decision.suggested_level.value} – {zone.name}"[:120],
            message=(
                f"{decision.distinct_reporters} vecinos reportan {label} a menos de 2 km entre sí en "
                f"{zone.name}. Aléjese de riberas y quebradas y siga las instrucciones de la autoridad."
            ),
            source=AlertSource.REPORTE_CIUDADANO,
            confidence=min(1.0, decision.distinct_reporters / (2 * decision.required_reporters)),
            valid_from=start,
            valid_until=start + self._validity,
        )

"""Caso de uso: monitor periódico de zonas (motor de consenso → alertas → Web Push).

En cada pasada, por cada zona monitoreada:

1. Se calcula el consenso multi-modelo en el centroide de la zona.
2. Si el resultado exige comunicado (ALERTA_DESBORDE o AVISO_TORMENTA):
   * sin alerta de consenso activa → se crea, activa y despacha;
   * con alerta activa de menor nivel → se **escala** y se despacha;
   * con alerta activa de igual nivel → se re-despacha (el cooldown decide
     si corresponde un recordatorio);
   * con alerta activa de mayor nivel → se mantiene hasta su vencimiento
     (política conservadora: no se baja una alerta antes de tiempo).
3. **Aluvión.** Si la zona tiene quebradas cerca de su centro, se evalúa
   el detonante meteorológico: lluvia
   pronosticada o ya caída, intensidad horaria e isoterma 0 °C. Tener
   quebradas no basta: la alerta sólo se emite cuando la lluvia la
   justifica, y sigue el mismo ciclo crear/escalar/mantener por separado
   de la alerta de desborde.
4. El nivel de la zona se actualiza al más severo entre el consenso, el
   aluvión y sus alertas activas (de cualquier origen, incluidas las ciudadanas).

Una zona que falla (p. ej. Open-Meteo caído) no detiene a las demás.

.. warning::
   Ejecutar **una sola** instancia del monitor a la vez (un proceso, o sólo
   el cron externo). Dos monitores concurrentes podrían crear alertas
   duplicadas para la misma zona.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from uuid import UUID

from src.application.dto import Clock, MonitorSummary, ZoneMonitorResult
from src.application.use_cases.dispatch_alerts import DispatchAlertsUseCase
from src.domain.entities.alert import Alert, AlertKind, AlertSource
from src.domain.entities.base import utc_now
from src.domain.entities.geo_zone import GeoZone
from src.domain.entities.watercourse import WatercourseKind
from src.domain.exceptions import ExternalDataUnavailableError, InsufficientModelDataError
from src.domain.repositories import AlertRepository, GeoZoneRepository, WatercourseRepository, WeatherForecastProvider
from src.domain.repositories.weather_provider import EnsembleProvider
from src.domain.services.consensus_engine import ConsensusEngineService
from src.domain.services.probability import STORM_ALERT_PCT, ProbabilityService
from src.domain.services.debris_flow import DebrisFlowAssessmentService
from src.domain.value_objects.forecast import (
    ConsensusOutcome,
    ConsensusResult,
    HydrologicalConditions,
    ModelPrecipitationForecast,
)
from src.domain.value_objects.risk_level import RiskLevel
from src.domain.value_objects.threat_type import ThreatType, WeatherModel

logger = logging.getLogger(__name__)

_ALERTING_OUTCOMES = frozenset({ConsensusOutcome.ALERTA_DESBORDE, ConsensusOutcome.AVISO_TORMENTA_ELECTRICA})

_DEBRIS_ACTION: dict[RiskLevel, str] = {
    RiskLevel.AMARILLO: "Tenga lista su mochila y aléjese de la quebrada si suena distinto o baja con barro.",
    RiskLevel.NARANJA: "Prepárese para salir hacia terreno alto lateral. No acampe ni se estacione junto a quebradas.",
    RiskLevel.ROJO: "Suba AHORA a terreno alto lateral, no aguas abajo. No cruce quebradas, vados ni puentes.",
}


class MonitorZonesUseCase:
    """Evalúa todas las zonas y emite/escala alertas de consenso y de aluvión."""

    def __init__(
        self,
        *,
        zones: GeoZoneRepository,
        alerts: AlertRepository,
        weather: WeatherForecastProvider,
        engine: ConsensusEngineService,
        dispatcher: DispatchAlertsUseCase,
        watercourses: WatercourseRepository | None = None,
        debris_flow: DebrisFlowAssessmentService | None = None,
        clock: Clock = utc_now,
        alert_validity: timedelta = timedelta(hours=6),
        horizon_hours: int = 24,
        debris_search_km: float = 7.0,
        ensemble: EnsembleProvider | None = None,
        probability: ProbabilityService | None = None,
    ) -> None:
        self._ensemble = ensemble
        self._probability = probability or ProbabilityService()
        self._zones = zones
        self._alerts = alerts
        self._weather = weather
        self._engine = engine
        self._dispatcher = dispatcher
        self._watercourses = watercourses
        self._debris = debris_flow or DebrisFlowAssessmentService()
        self._clock = clock
        self._validity = alert_validity
        self._horizon = horizon_hours
        self._debris_km = debris_search_km
        self._debris_prone: dict[UUID, bool] = {}

    async def execute(self) -> MonitorSummary:
        """Una pasada completa sobre todas las zonas."""
        started = self._clock()
        results = [await self._evaluate_zone(z) for z in await self._zones.list_all()]
        summary = MonitorSummary(started_at=started, zones=tuple(results))
        logger.info(
            "Monitor: %s",
            ", ".join(
                f"{r.zone_name}={r.risk_level or 'ERROR'}({r.action}"
                + (f"; aluvión {r.debris_level}/{r.debris_action}" if r.debris_level else "")
                + ")"
                for r in results
            ),
        )
        return summary

    async def _evaluate_zone(self, zone: GeoZone) -> ZoneMonitorResult:
        try:
            forecasts = await self._weather.get_precipitation_forecasts(
                zone.centroid, list(WeatherModel), self._horizon
            )
            conditions = await self._weather.get_hydrological_conditions(zone.centroid, self._horizon)
            result = self._engine.evaluate(forecasts, conditions)
        except (ExternalDataUnavailableError, InsufficientModelDataError) as exc:
            logger.warning("Monitor: zona %s sin consenso (%s)", zone.name, exc)
            return ZoneMonitorResult(
                zone_id=zone.id, zone_name=zone.name, risk_level=None, action="sin_datos", error=str(exc)
            )

        now = self._clock()
        active = list(await self._alerts.find_active_by_zone(zone.id, now))
        consensus_alerts = [a for a in active if a.source is AlertSource.MOTOR_CONSENSO]

        action = "sin_cambios"
        alert: Alert | None = None
        base_level = result.risk_level
        if result.outcome is ConsensusOutcome.AVISO_TORMENTA_ELECTRICA and not await self._storm_confirmed(zone):
            action = "tormenta_descartada"
            base_level = RiskLevel.VERDE  # el amarillo venía sólo de la tormenta descartada
        elif result.outcome in _ALERTING_OUTCOMES:
            alert, action = await self._upsert(
                [a for a in consensus_alerts if a.threat_type is not ThreatType.REMOCION_EN_MASA],
                result.risk_level,
                lambda: Alert.from_consensus(zone.id, zone.name, result, validity=self._validity, now=now),
                now,
            )

        debris_level: RiskLevel | None = None
        debris_action: str | None = None
        debris_alert: Alert | None = None
        if await self._is_debris_prone(zone):
            debris_level = self._debris.trigger_level(
                rain_24h_mm=result.weighted_precipitation_mm,
                freezing_level_m=conditions.freezing_level_m,
                recent_rain_24h_mm=conditions.recent_precipitation_mm,
                max_hourly_mm=conditions.max_hourly_precipitation_mm,
                model_rain_mm=self._model_rain(forecasts),
            )
            debris_action = "sin_cambios"
            if debris_level > RiskLevel.VERDE:
                level = debris_level
                debris_alert, debris_action = await self._upsert(
                    [a for a in consensus_alerts if a.threat_type is ThreatType.REMOCION_EN_MASA],
                    level,
                    lambda: self._debris_alert(zone, level, result, conditions, now),
                    now,
                    refresh=lambda a: self._refresh_debris_text(a, zone, result, conditions),
                )

        for changed in (alert, debris_alert):
            if changed is not None:
                active = [a for a in active if a.id != changed.id] + [changed]
        effective = RiskLevel.most_severe(
            base_level, debris_level or RiskLevel.VERDE, *(a.risk_level for a in active)
        )
        if zone.update_risk(effective):
            await self._zones.save(zone)

        dispatch = await self._dispatcher.execute(alert) if alert is not None else None
        debris_dispatch = await self._dispatcher.execute(debris_alert) if debris_alert is not None else None
        return ZoneMonitorResult(
            zone_id=zone.id,
            zone_name=zone.name,
            risk_level=effective,
            action=action,
            dispatch=dispatch,
            debris_level=debris_level,
            debris_action=debris_action,
            debris_dispatch=debris_dispatch,
        )

    async def _storm_confirmed(self, zone: GeoZone) -> bool:
        """Con ensemble, la tormenta se avisa sólo si la probabilidad llega a :data:`STORM_ALERT_PCT`.

        Así la notificación push coincide con lo que muestra la app. Sin ensemble
        se mantiene el criterio del modelo principal.
        """
        if self._ensemble is None:
            return True
        try:
            members = await self._ensemble.get_ensemble(zone.centroid, self._horizon)
        except ExternalDataUnavailableError:
            return True
        pct = self._probability.storm_pct(members)
        if pct is None:
            return True
        logger.info("Monitor: zona %s, probabilidad de tormenta %s %%", zone.name, pct)
        return pct >= STORM_ALERT_PCT

    # ------------------------------------------------------------ alertas

    async def _upsert(
        self,
        existing: Sequence[Alert],
        level: RiskLevel,
        create: Callable[[], Alert],
        now: datetime,
        refresh: Callable[[Alert], None] | None = None,
    ) -> tuple[Alert, str]:
        """Crea, escala o mantiene la alerta de una amenaza. Guarda si hubo cambio."""
        current = max(existing, key=lambda a: a.risk_level, default=None)
        if current is None:
            alert = create()
            alert.activate()
            action = "alerta_creada"
        elif level > current.risk_level:
            current.escalate_to(level)
            if refresh is not None:
                refresh(current)
            alert, action = current, "alerta_escalada"
        else:
            return current, "alerta_vigente"
        await self._alerts.save(alert)
        return alert, action

    def _model_rain(self, forecasts: Sequence[ModelPrecipitationForecast]) -> list[tuple[float, float]]:
        weights = self._engine.config.model_weights
        return [(f.accumulated_precipitation_mm, weights[f.model]) for f in forecasts if f.model in weights]

    async def _is_debris_prone(self, zone: GeoZone) -> bool:
        """Zona con quebradas a menos de ``debris_search_km`` de su centro.

        No se usa ``exposed_threats``: la semilla marca todas las zonas como
        expuestas y eso convertiría al valle entero en zona de aluvión.
        """
        if self._watercourses is None:
            return False
        if zone.id not in self._debris_prone:
            try:
                near = await self._watercourses.find_near(zone.centroid, self._debris_km, limit=500)
            except ExternalDataUnavailableError as exc:
                logger.warning("Monitor: cauces no disponibles para %s (%s)", zone.name, exc)
                return False
            self._debris_prone[zone.id] = any(c.kind is WatercourseKind.QUEBRADA for c in near)
        return self._debris_prone[zone.id]

    def _debris_alert(
        self,
        zone: GeoZone,
        level: RiskLevel,
        result: ConsensusResult,
        conditions: HydrologicalConditions,
        now: datetime,
    ) -> Alert:
        alert = Alert(
            zone_id=zone.id,
            kind=AlertKind.ALERTA,
            risk_level=level,
            threat_type=ThreatType.REMOCION_EN_MASA,
            title="Alerta",
            message="Alerta",
            source=AlertSource.MOTOR_CONSENSO,
            confidence=result.confidence,
            valid_from=now,
            valid_until=now + self._validity,
        )
        self._refresh_debris_text(alert, zone, result, conditions)
        return alert

    def _refresh_debris_text(
        self, alert: Alert, zone: GeoZone, result: ConsensusResult, conditions: HydrologicalConditions
    ) -> None:
        """Título y mensaje según el nivel vigente (al escalar cambian las instrucciones)."""
        trigger = self._debris.describe_trigger(
            result.weighted_precipitation_mm,
            conditions.freezing_level_m,
            conditions.recent_precipitation_mm,
            conditions.max_hourly_precipitation_mm,
        )
        alert.title = f"Alerta de aluvión {alert.risk_level.value} – {zone.name}"[:120]
        alert.message = (
            f"Posible aluvión en quebradas de {zone.name}: {trigger}. "
            f"Si vive a menos de 500 m de una quebrada: {_DEBRIS_ACTION[alert.risk_level]}"
        )[:2000]

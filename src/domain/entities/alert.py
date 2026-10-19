"""Entidad ``Alert``: comunicado oficial emitido para una zona geográfica."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from src.domain.entities.base import Entity, utc_now
from src.domain.exceptions import InvalidStateTransitionError
from src.domain.value_objects.forecast import ConsensusOutcome, ConsensusResult
from src.domain.value_objects.risk_level import RiskLevel
from src.domain.value_objects.threat_type import ThreatType


class AlertStatus(StrEnum):
    """Ciclo de vida: ``BORRADOR → ACTIVA → (CANCELADA | EXPIRADA)``."""

    BORRADOR = "BORRADOR"
    ACTIVA = "ACTIVA"
    CANCELADA = "CANCELADA"
    EXPIRADA = "EXPIRADA"


class AlertKind(StrEnum):
    """Naturaleza del comunicado."""

    ALERTA = "ALERTA"
    AVISO_TORMENTA = "AVISO_TORMENTA"
    REPORTE_TRANQUILIDAD = "REPORTE_TRANQUILIDAD"


class AlertSource(StrEnum):
    """Origen del comunicado."""

    MOTOR_CONSENSO = "MOTOR_CONSENSO"
    OPERADOR = "OPERADOR"
    REPORTE_CIUDADANO = "REPORTE_CIUDADANO"


_OUTCOME_TO_KIND: dict[ConsensusOutcome, AlertKind] = {
    ConsensusOutcome.ALERTA_DESBORDE: AlertKind.ALERTA,
    ConsensusOutcome.AVISO_TORMENTA_ELECTRICA: AlertKind.AVISO_TORMENTA,
    ConsensusOutcome.REPORTE_TRANQUILIDAD: AlertKind.REPORTE_TRANQUILIDAD,
}


class Alert(Entity):
    """Comunicado de riesgo asociado a una :class:`GeoZone`.

    Attributes:
        zone_id: Identificador de la zona afectada.
        kind: Alerta, aviso de tormenta o reporte de tranquilidad.
        risk_level: Nivel de riesgo comunicado.
        threat_type: Amenaza principal.
        title: Título corto para notificaciones push.
        message: Cuerpo del mensaje para la ciudadanía.
        source: Origen del comunicado.
        confidence: Confianza 0-1 heredada del motor de consenso (1.0 si es manual).
        status: Estado del ciclo de vida.
        valid_from: Inicio de vigencia.
        valid_until: Fin de vigencia.
        cancellation_reason: Motivo de cancelación, si aplica.
    """

    zone_id: UUID
    kind: AlertKind
    risk_level: RiskLevel
    threat_type: ThreatType
    title: str = Field(min_length=3, max_length=120)
    message: str = Field(min_length=3, max_length=2000)
    source: AlertSource = AlertSource.MOTOR_CONSENSO
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    status: AlertStatus = AlertStatus.BORRADOR
    valid_from: datetime = Field(default_factory=utc_now)
    valid_until: datetime
    cancellation_reason: str | None = None

    @model_validator(mode="after")
    def _validate_window(self) -> Self:
        if self.valid_until <= self.valid_from:
            raise ValueError("'valid_until' debe ser posterior a 'valid_from'.")
        return self

    @classmethod
    def from_consensus(
        cls,
        zone_id: UUID,
        zone_name: str,
        result: ConsensusResult,
        validity: timedelta = timedelta(hours=24),
        now: datetime | None = None,
    ) -> Alert:
        """Fábrica: crea un comunicado en estado BORRADOR a partir del consenso.

        Args:
            zone_id: Zona a la que aplica el comunicado.
            zone_name: Nombre legible de la zona (para el título).
            result: Resultado del :class:`ConsensusEngineService`.
            validity: Duración de la vigencia.
            now: Instante de emisión (inyectable para tests).
        """
        start = now or utc_now()
        kind = _OUTCOME_TO_KIND[result.outcome]
        title = f"{kind.value.replace('_', ' ').title()} {result.risk_level.value} – {zone_name}"
        return cls(
            zone_id=zone_id,
            kind=kind,
            risk_level=result.risk_level,
            threat_type=result.threat_type,
            title=title[:120],
            message=result.summary,
            source=AlertSource.MOTOR_CONSENSO,
            confidence=result.confidence,
            valid_from=start,
            valid_until=start + validity,
        )

    def activate(self) -> None:
        """Publica el comunicado.

        Raises:
            InvalidStateTransitionError: Si no está en BORRADOR.
        """
        if self.status is not AlertStatus.BORRADOR:
            raise InvalidStateTransitionError(f"No se puede activar una alerta en estado {self.status}.")
        self.status = AlertStatus.ACTIVA

    def cancel(self, reason: str) -> None:
        """Cancela el comunicado indicando el motivo (trazabilidad obligatoria).

        Raises:
            InvalidStateTransitionError: Si ya está cancelada o expirada.
            ValueError: Si el motivo está vacío.
        """
        if self.status in (AlertStatus.CANCELADA, AlertStatus.EXPIRADA):
            raise InvalidStateTransitionError(f"No se puede cancelar una alerta en estado {self.status}.")
        if not reason.strip():
            raise ValueError("Debe indicarse el motivo de cancelación.")
        self.cancellation_reason = reason.strip()
        self.status = AlertStatus.CANCELADA

    def expire_if_due(self, now: datetime | None = None) -> bool:
        """Marca como EXPIRADA si su vigencia terminó. Devuelve ``True`` si cambió."""
        if self.status is AlertStatus.ACTIVA and (now or utc_now()) >= self.valid_until:
            self.status = AlertStatus.EXPIRADA
            return True
        return False

    def escalate_to(self, level: RiskLevel) -> None:
        """Sube el nivel de una alerta activa. Nunca permite bajar por esta vía.

        Raises:
            InvalidStateTransitionError: Si la alerta no está activa o el nivel no es mayor.
        """
        if self.status is not AlertStatus.ACTIVA:
            raise InvalidStateTransitionError("Sólo se pueden escalar alertas activas.")
        if level <= self.risk_level:
            raise InvalidStateTransitionError(
                f"El nuevo nivel ({level}) debe ser más severo que el actual ({self.risk_level})."
            )
        self.risk_level = level

    def is_active(self, now: datetime | None = None) -> bool:
        """``True`` si está ACTIVA y dentro de su ventana de vigencia."""
        instant = now or utc_now()
        return self.status is AlertStatus.ACTIVA and self.valid_from <= instant < self.valid_until

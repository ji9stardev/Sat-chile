"""Entidad ``CitizenReport``: reporte ciudadano georreferenciado."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, HttpUrl

from src.domain.entities.base import Entity, utc_now
from src.domain.exceptions import InvalidStateTransitionError
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.threat_type import ThreatType


class ReportStatus(StrEnum):
    """Ciclo de vida: ``PENDIENTE → (VERIFICADO | DESCARTADO)``."""

    PENDIENTE = "PENDIENTE"
    VERIFICADO = "VERIFICADO"
    DESCARTADO = "DESCARTADO"


class WaterLevelObservation(StrEnum):
    """Nivel del agua observado por el vecino."""

    NORMAL = "NORMAL"
    ALTO = "ALTO"
    DESBORDE = "DESBORDE"


class CitizenReport(Entity):
    """Observación de terreno enviada por la ciudadanía.

    Se usa como señal complementaria (*ground truth*) al motor de consenso.

    Attributes:
        reporter_hash: Identificador anonimizado del informante (nunca datos personales).
        location: Ubicación del evento observado.
        threat_type: Amenaza observada.
        observed_level: Nivel del agua observado (sólo amenazas hídricas).
        description: Descripción libre.
        photo_urls: Evidencia fotográfica opcional.
        reported_at: Instante del reporte.
        status: Estado de verificación.
        reviewed_by: Operador que verificó o descartó.
        review_note: Nota del operador.
    """

    reporter_hash: str = Field(min_length=8, max_length=128)
    location: Coordinates
    threat_type: ThreatType
    observed_level: WaterLevelObservation | None = None
    description: str = Field(min_length=5, max_length=1000)
    photo_urls: tuple[HttpUrl, ...] = Field(default=(), max_length=5)
    reported_at: datetime = Field(default_factory=utc_now)
    status: ReportStatus = ReportStatus.PENDIENTE
    reviewed_by: str | None = None
    review_note: str | None = None

    @property
    def indicates_hazard(self) -> bool:
        """``True`` salvo que el vecino informe explícitamente nivel NORMAL."""
        return self.observed_level is not WaterLevelObservation.NORMAL

    @property
    def has_evidence(self) -> bool:
        """``True`` si adjunta al menos una fotografía."""
        return len(self.photo_urls) > 0

    def verify(self, operator: str, note: str | None = None) -> None:
        """Marca el reporte como verificado por un operador."""
        self._review(ReportStatus.VERIFICADO, operator, note)

    def reject(self, operator: str, note: str) -> None:
        """Descarta el reporte; exige nota justificativa."""
        if not note.strip():
            raise ValueError("Descartar un reporte requiere una nota justificativa.")
        self._review(ReportStatus.DESCARTADO, operator, note)

    def _review(self, new_status: ReportStatus, operator: str, note: str | None) -> None:
        if self.status is not ReportStatus.PENDIENTE:
            raise InvalidStateTransitionError(f"El reporte ya fue revisado ({self.status}).")
        if not operator.strip():
            raise ValueError("Debe indicarse el operador revisor.")
        self.reviewed_by = operator.strip()
        self.review_note = note
        self.status = new_status

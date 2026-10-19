"""``CrowdValidationService``: regla de corroboración vecinal (3 vecinos en 2 km).

Un reporte ciudadano aislado puede ser un error o una broma. Se considera
**corroborado** cuando al menos ``min_distinct_reporters`` informantes
*distintos* (por ``reporter_hash``) reportan una amenaza de la misma familia
dentro de ``radius_km`` del nuevo reporte y en la ventana ``window``.

* Un mismo vecino reportando muchas veces cuenta una sola vez.
* Reportes DESCARTADOS por un operador y reportes de nivel NORMAL no suman.
* Las amenazas hídricas (inundación, desborde, remoción en masa) se tratan
  como una sola familia: un vecino puede describir como "inundación" lo que
  otro describe como "desborde".
* El nivel sugerido es NARANJA si al menos la mitad de los vecinos reporta
  DESBORDE; si no, AMARILLO. La comunidad nunca dispara ROJO por sí sola:
  eso requiere confirmación instrumental u operador.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.domain.entities.citizen_report import CitizenReport, ReportStatus, WaterLevelObservation
from src.domain.value_objects.risk_level import RiskLevel
from src.domain.value_objects.threat_type import ThreatType


class CrowdValidationConfig(BaseModel):
    """Parámetros de la regla vecinal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    radius_km: float = Field(default=2.0, gt=0)
    min_distinct_reporters: int = Field(default=3, ge=2)
    window: timedelta = timedelta(hours=3)
    reporter_cooldown: timedelta = timedelta(minutes=10)


class CrowdDecision(BaseModel):
    """Resultado de evaluar un reporte frente a sus vecinos."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    corroborated: bool
    distinct_reporters: int
    required_reporters: int
    supporting_report_ids: tuple[UUID, ...]
    suggested_level: RiskLevel
    threat_type: ThreatType


def same_threat_family(a: ThreatType, b: ThreatType) -> bool:
    """``True`` si ambas amenazas son iguales o ambas son hidrometeorológicas."""
    return a == b or (a.is_hydrometeorological and b.is_hydrometeorological)


class CrowdValidationService:
    """Evalúa la corroboración vecinal. Sin estado ni I/O."""

    def __init__(self, config: CrowdValidationConfig | None = None) -> None:
        self._config = config or CrowdValidationConfig()

    @property
    def config(self) -> CrowdValidationConfig:
        """Configuración activa."""
        return self._config

    def evaluate(
        self, new_report: CitizenReport, nearby: Sequence[CitizenReport], now: datetime
    ) -> CrowdDecision:
        """Decide si ``new_report`` queda corroborado por los reportes cercanos."""
        pool = {r.id: r for r in nearby}
        pool[new_report.id] = new_report
        supporting = [r for r in pool.values() if self._supports(new_report, r, now)]

        latest_by_reporter: dict[str, CitizenReport] = {}
        for report in supporting:
            current = latest_by_reporter.get(report.reporter_hash)
            if current is None or report.reported_at > current.reported_at:
                latest_by_reporter[report.reporter_hash] = report

        distinct = len(latest_by_reporter)
        overflow = sum(1 for r in latest_by_reporter.values() if r.observed_level is WaterLevelObservation.DESBORDE)
        level = RiskLevel.NARANJA if overflow > 0 and overflow * 2 >= distinct else RiskLevel.AMARILLO
        required = self._config.min_distinct_reporters
        return CrowdDecision(
            corroborated=new_report.indicates_hazard and distinct >= required,
            distinct_reporters=distinct,
            required_reporters=required,
            supporting_report_ids=tuple(sorted((r.id for r in supporting), key=str)),
            suggested_level=level,
            threat_type=new_report.threat_type,
        )

    def is_rate_limited(self, previous_from_reporter: Sequence[CitizenReport], now: datetime) -> bool:
        """``True`` si el mismo informante reportó dentro del cooldown."""
        limit = now - self._config.reporter_cooldown
        return any(r.reported_at > limit for r in previous_from_reporter)

    def _supports(self, new: CitizenReport, other: CitizenReport, now: datetime) -> bool:
        cfg = self._config
        return (
            other.status is not ReportStatus.DESCARTADO
            and other.indicates_hazard
            and same_threat_family(new.threat_type, other.threat_type)
            and now - other.reported_at <= cfg.window
            and new.location.distance_km(other.location) <= cfg.radius_km
        )

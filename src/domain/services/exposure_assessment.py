"""``ExposureAssessmentService``: riesgo personal según cercanía y altura sobre el cauce.

El riesgo de una zona (consenso meteorológico, alertas activas) es el
*peligro*; la exposición de cada persona depende de dónde está respecto del
cauce. Una persona en una terraza 30 m sobre el río no corre el mismo riesgo
que otra en la ribera, aunque ambas estén en la misma zona en ROJO.

Reglas (umbrales calibrables en :class:`ExposureConfig`):

====================  ===================================  ==================
Exposición            Condición                            Ajuste del nivel
====================  ===================================  ==================
CRITICA               ≤ 150 m del cauce y ≤ 3 m sobre él    +1 (tope ROJO)
ALTA                  ≤ 500 m y ≤ 10 m                      0
MODERADA              ≤ 2 km                                −1
BAJA                  > 2 km, o ≥ 25 m sobre el cauce       −2
====================  ===================================  ==================

Si la altura relativa no se conoce, se asume la condición desfavorable
(no se baja el nivel por altura sin evidencia).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domain.entities.watercourse import WatercourseKind
from src.domain.value_objects.risk_level import RiskLevel

DEFAULT_KIND_DISTANCE_FACTOR: Mapping[WatercourseKind, float] = {
    WatercourseKind.RIO: 1.0,
    WatercourseKind.QUEBRADA: 1.0,
    WatercourseKind.ESTERO: 1.5,
    WatercourseKind.CANAL: 4.0,
}
"""Multiplicador de distancia por tipo de cauce. Un canal de riego (caudal regulado)
a 50 m equivale en exposición a un río a 200 m; un estero a 100 m, a un río a 150 m."""


class ExposureLevel(StrEnum):
    """Exposición física de una persona frente a un cauce."""

    CRITICA = "CRITICA"
    ALTA = "ALTA"
    MODERADA = "MODERADA"
    BAJA = "BAJA"


_LEVEL_SHIFT: dict[ExposureLevel, int] = {
    ExposureLevel.CRITICA: 1,
    ExposureLevel.ALTA: 0,
    ExposureLevel.MODERADA: -1,
    ExposureLevel.BAJA: -2,
}

_RECOMMENDATION: dict[RiskLevel, str] = {
    RiskLevel.ROJO: (
        "Manténgase lejos de cauces, vados y puentes con agua, y siga las "
        "indicaciones de SENAPRED y su municipio."
    ),
    RiskLevel.NARANJA: (
        "Tenga a mano documentos, agua, linterna y medicamentos, "
        "y siga las instrucciones de SENAPRED y su municipio."
    ),
    RiskLevel.AMARILLO: "Manténgase informado y evite acercarse a riberas y quebradas.",
    RiskLevel.VERDE: "Sin riesgo significativo para su ubicación en este momento.",
}


def level_recommendation(level: RiskLevel) -> str:
    """Recomendación principal asociada a un nivel de riesgo personal."""
    return _RECOMMENDATION[level]


class ExposureConfig(BaseModel):
    """Umbrales de exposición (distancias en km, alturas en m)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    critical_distance_km: float = Field(default=0.15, gt=0)
    critical_height_m: float = Field(default=3.0, gt=0)
    high_distance_km: float = Field(default=0.5, gt=0)
    high_height_m: float = Field(default=10.0, gt=0)
    moderate_distance_km: float = Field(default=2.0, gt=0)
    safe_height_m: float = Field(default=25.0, gt=0)
    kind_distance_factor: Mapping[WatercourseKind, float] = Field(
        default_factory=lambda: dict(DEFAULT_KIND_DISTANCE_FACTOR)
    )

    def effective_distance(self, distance_km: float, kind: WatercourseKind | None) -> float:
        """Distancia ponderada por el tipo de cauce (para elegir el cauce que gobierna el riesgo)."""
        return distance_km * (self.kind_distance_factor.get(kind, 1.0) if kind else 1.0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not (self.critical_distance_km < self.high_distance_km < self.moderate_distance_km):
            raise ValueError("Las distancias deben ser crecientes: crítica < alta < moderada.")
        if not (self.critical_height_m < self.high_height_m < self.safe_height_m):
            raise ValueError("Las alturas deben ser crecientes: crítica < alta < segura.")
        return self


class ExposureAssessment(BaseModel):
    """Resultado inmutable de la evaluación personal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exposure: ExposureLevel
    hazard_level: RiskLevel
    personal_risk_level: RiskLevel
    distance_km: float | None
    relative_height_m: float | None
    recommendations: tuple[str, ...]


class ExposureAssessmentService:
    """Combina peligro de la zona y exposición física. Sin estado ni I/O."""

    def __init__(self, config: ExposureConfig | None = None) -> None:
        self._config = config or ExposureConfig()

    @property
    def config(self) -> ExposureConfig:
        """Configuración activa."""
        return self._config

    def classify(
        self,
        distance_km: float | None,
        relative_height_m: float | None,
        kind: WatercourseKind | None = None,
    ) -> ExposureLevel:
        """Clasifica la exposición. ``distance_km=None`` significa "sin cauce cercano"."""
        cfg = self._config
        if distance_km is None:
            return ExposureLevel.BAJA
        distance_km = cfg.effective_distance(distance_km, kind)
        if relative_height_m is not None and relative_height_m >= cfg.safe_height_m:
            return ExposureLevel.BAJA

        def height_at_most(limit: float) -> bool:
            return relative_height_m is None or relative_height_m <= limit

        if distance_km <= cfg.critical_distance_km and height_at_most(cfg.critical_height_m):
            return ExposureLevel.CRITICA
        if distance_km <= cfg.high_distance_km and height_at_most(cfg.high_height_m):
            return ExposureLevel.ALTA
        if distance_km <= cfg.moderate_distance_km:
            return ExposureLevel.MODERADA
        return ExposureLevel.BAJA

    def assess(
        self,
        *,
        distance_km: float | None,
        relative_height_m: float | None,
        hazard_level: RiskLevel,
        watercourse_name: str | None = None,
        kind: WatercourseKind | None = None,
    ) -> ExposureAssessment:
        """Calcula el nivel personal y las recomendaciones."""
        exposure = self.classify(distance_km, relative_height_m, kind)
        if hazard_level is RiskLevel.VERDE:
            personal = RiskLevel.VERDE
        else:
            shift = _LEVEL_SHIFT[exposure]
            personal = hazard_level.escalate(shift) if shift >= 0 else hazard_level.deescalate(-shift)

        recommendations = [_RECOMMENDATION[personal]]
        name = watercourse_name or "el cauce"
        if exposure in (ExposureLevel.CRITICA, ExposureLevel.ALTA):
            if relative_height_m is not None:
                recommendations.append(
                    f"Su ubicación está a {max(relative_height_m, 0):.0f} m sobre {name}, "
                    f"a {distance_km or 0:.2f} km de distancia."
                )
            else:
                recommendations.append(
                    f"Está a {distance_km or 0:.2f} km de {name} y no fue posible estimar su altura "
                    "sobre el cauce: ante la duda, considere su ubicación como expuesta."
                )
        return ExposureAssessment(
            exposure=exposure,
            hazard_level=hazard_level,
            personal_risk_level=personal,
            distance_km=None if distance_km is None else round(distance_km, 3),
            relative_height_m=None if relative_height_m is None else round(relative_height_m, 1),
            recommendations=tuple(recommendations),
        )

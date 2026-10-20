"""``ConsensusEngineService``: núcleo de decisión del SAT-Maule.

El flujo de evaluación es determinista y auditable:

1. **Consenso multi-modelo.** Se pondera la precipitación acumulada de
   ``ecmwf_ifs025``, ``icon_seamless`` y ``gfs_seamless`` (los pesos se
   renormalizan sobre los modelos disponibles) y se mide la dispersión.
2. **Nivel crudo.** La precipitación de consenso se mapea a un
   :class:`RiskLevel` según umbrales configurables.
3. **Penalización por desacuerdo.** Si menos de la mitad del peso respalda
   el nivel de consenso, se rebaja un nivel (evita falsas alarmas por un
   único modelo extremo).
4. **Filtro hidrológico.** Si la isoterma 0 °C está bajo 1850 m (precipita
   como nieve en la cuenca alta) o el suelo está seco (humedad < 0.28 m³/m³,
   alta infiltración), cada condición rebaja un nivel. Si el nivel llega a
   VERDE se emite un *Reporte de Tranquilidad*, o bien un *Aviso de Tormenta
   Eléctrica sin riesgo de desborde* si la convección (CAPE) es alta.
5. **Tormenta eléctrica.** Con CAPE alto y algo de lluvia se avisa aunque
   la lluvia total esté bajo los umbrales de desborde: los rayos no
   dependen del acumulado.

El servicio no conoce bases de datos ni APIs: recibe Value Objects y
devuelve un :class:`ConsensusResult` inmutable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domain.exceptions import InsufficientModelDataError
from src.domain.services import consensus_math as cm
from src.domain.value_objects.forecast import (
    ConsensusOutcome,
    ConsensusResult,
    DiscardReason,
    HydrologicalConditions,
    ModelPrecipitationForecast,
)
from src.domain.value_objects.risk_level import RiskLevel
from src.domain.value_objects.threat_type import ThreatType, WeatherModel

DEFAULT_MODEL_WEIGHTS: Mapping[WeatherModel, float] = {
    WeatherModel.ECMWF_IFS025: 0.45,
    WeatherModel.ICON_SEAMLESS: 0.30,
    WeatherModel.GFS_SEAMLESS: 0.25,
}
"""Pesos iniciales. ECMWF recibe mayor peso por su mejor desempeño histórico
en sistemas frontales del Pacífico Sur; deben recalibrarse con datos locales."""


class ConsensusConfig(BaseModel):
    """Parámetros calibrables del motor (inmutables por evaluación).

    Attributes:
        model_weights: Peso relativo de cada modelo global.
        min_models_required: Mínimo de modelos para emitir un consenso.
        freezing_level_threshold_m: Bajo esta isoterma 0 °C se descarta desborde.
        dry_soil_threshold_m3m3: Bajo esta humedad de suelo se descarta desborde.
        thunderstorm_cape_threshold_j_kg: CAPE desde el cual se avisa tormenta eléctrica.
        thunderstorm_min_precipitation_mm: Lluvia mínima en la ventana para que
            el CAPE alto cuente como tormenta (sin lluvia no hay disparador).
        amarillo_threshold_mm: Precipitación (en la ventana) para nivel AMARILLO.
        naranja_threshold_mm: Precipitación para nivel NARANJA.
        rojo_threshold_mm: Precipitación para nivel ROJO.
        min_agreement_ratio: Fracción mínima de peso que debe respaldar el nivel.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_weights: Mapping[WeatherModel, float] = Field(
        default_factory=lambda: dict(DEFAULT_MODEL_WEIGHTS)
    )
    min_models_required: int = Field(default=2, ge=1, le=len(WeatherModel))
    freezing_level_threshold_m: float = Field(default=1850.0, gt=0.0)
    dry_soil_threshold_m3m3: float = Field(default=0.28, gt=0.0, lt=1.0)
    thunderstorm_cape_threshold_j_kg: float = Field(default=300.0, gt=0.0)  # 300 J/kg: en Chile central muchas tormentas (bajas segregadas) se forman con CAPE de 200-500.
    thunderstorm_min_precipitation_mm: float = Field(default=1.0, ge=0.0)
    amarillo_threshold_mm: float = Field(default=20.0, gt=0.0)
    naranja_threshold_mm: float = Field(default=40.0, gt=0.0)
    rojo_threshold_mm: float = Field(default=70.0, gt=0.0)
    min_agreement_ratio: float = Field(default=0.5, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if not (self.amarillo_threshold_mm < self.naranja_threshold_mm < self.rojo_threshold_mm):
            raise ValueError("Los umbrales deben ser estrictamente crecientes: AMARILLO < NARANJA < ROJO.")
        if any(w <= 0 for w in self.model_weights.values()):
            raise ValueError("Todos los pesos de modelo deben ser positivos.")
        return self

    def level_for(self, precipitation_mm: float) -> RiskLevel:
        """Mapea una precipitación acumulada a nivel de riesgo."""
        if precipitation_mm >= self.rojo_threshold_mm:
            return RiskLevel.ROJO
        if precipitation_mm >= self.naranja_threshold_mm:
            return RiskLevel.NARANJA
        if precipitation_mm >= self.amarillo_threshold_mm:
            return RiskLevel.AMARILLO
        return RiskLevel.VERDE


class ConsensusEngineService:
    """Servicio de dominio sin estado que decide el comunicado a emitir."""

    def __init__(self, config: ConsensusConfig | None = None) -> None:
        self._config = config or ConsensusConfig()

    @property
    def config(self) -> ConsensusConfig:
        """Configuración activa (de sólo lectura)."""
        return self._config

    def evaluate(
        self,
        forecasts: Sequence[ModelPrecipitationForecast],
        conditions: HydrologicalConditions,
    ) -> ConsensusResult:
        """Evalúa pronósticos multi-modelo y condiciones hidrológicas.

        Args:
            forecasts: Un pronóstico por modelo (misma ventana temporal).
            conditions: Isoterma 0 °C, humedad de suelo y CAPE de la zona.

        Returns:
            Un :class:`ConsensusResult` con el comunicado y sus métricas.

        Raises:
            InsufficientModelDataError: Si hay menos modelos que el mínimo,
                modelos duplicados, modelos sin peso o ventanas distintas.
        """
        usable = self._select_forecasts(forecasts)
        cfg = self._config

        values = [f.accumulated_precipitation_mm for f in usable]
        weights = cm.normalize_weights([cfg.model_weights[f.model] for f in usable])

        consensus_mm = cm.weighted_mean(values, weights)
        cv = cm.coefficient_of_variation(values, weights)
        raw_level = cfg.level_for(consensus_mm)

        agreement = self._agreement_ratio(values, weights, raw_level)
        level = raw_level
        downgraded = False
        if raw_level > RiskLevel.VERDE and agreement < cfg.min_agreement_ratio:
            level = raw_level.deescalate()
            downgraded = True

        coverage = sum(cfg.model_weights[f.model] for f in usable) / sum(cfg.model_weights.values())
        metrics = _Metrics(
            raw_level=raw_level,
            consensus_mm=consensus_mm,
            cv=cv,
            agreement=agreement,
            confidence=cm.clamp(agreement * (1.0 - min(cv, 1.0)) * coverage),
            models_used=tuple(f.model for f in usable),
            downgraded=downgraded,
        )

        if level is RiskLevel.VERDE:
            # La tormenta eléctrica no depende del total de lluvia: con convección
            # fuerte basta un chubasco corto para que haya rayos.
            if self._thunderstorm_likely(consensus_mm, conditions):
                return metrics.to_result(
                    outcome=ConsensusOutcome.AVISO_TORMENTA_ELECTRICA,
                    level=RiskLevel.AMARILLO,
                    threat=ThreatType.TORMENTA_ELECTRICA,
                    reasons=(),
                    summary=self._thunderstorm_summary(consensus_mm, (), conditions),
                )
            return metrics.to_result(
                outcome=ConsensusOutcome.REPORTE_TRANQUILIDAD,
                level=RiskLevel.VERDE,
                threat=ThreatType.INUNDACION,
                reasons=(),
                summary=self._tranquility_summary(consensus_mm, ()),
            )

        reasons = self.discard_reasons(conditions)
        # Cada condición atenuante baja un nivel; ya no anula la alerta entera: con 70 mm el agua
        # escurre aunque el suelo esté seco, y bajo la isoterma (el valle) sigue lloviendo.
        for _ in reasons:
            level = level.deescalate()
        if level > RiskLevel.VERDE:
            summary = self._flood_summary(consensus_mm, level, len(usable), reasons)
            if self._thunderstorm_likely(consensus_mm, conditions):
                summary += " Además hay posibilidad de tormenta eléctrica: evite espacios abiertos."
            return metrics.to_result(
                outcome=ConsensusOutcome.ALERTA_DESBORDE,
                level=level,
                threat=ThreatType.DESBORDE_RIO,
                reasons=reasons,
                summary=summary,
            )
        if reasons and self._thunderstorm_likely(consensus_mm, conditions):
            return metrics.to_result(
                outcome=ConsensusOutcome.AVISO_TORMENTA_ELECTRICA,
                level=RiskLevel.AMARILLO,
                threat=ThreatType.TORMENTA_ELECTRICA,
                reasons=reasons,
                summary=self._thunderstorm_summary(consensus_mm, reasons, conditions),
            )
        return metrics.to_result(
            outcome=ConsensusOutcome.REPORTE_TRANQUILIDAD,
            level=RiskLevel.VERDE,
            threat=ThreatType.INUNDACION,
            reasons=reasons,
            summary=self._tranquility_summary(consensus_mm, reasons),
        )

    def _thunderstorm_likely(self, consensus_mm: float, conditions: HydrologicalConditions) -> bool:
        """Convección fuerte (CAPE alto) con al menos algo de precipitación que la dispare."""
        cfg = self._config
        return (
            conditions.cape_j_kg >= cfg.thunderstorm_cape_threshold_j_kg
            and consensus_mm >= cfg.thunderstorm_min_precipitation_mm
        )

    def discard_reasons(self, conditions: HydrologicalConditions) -> tuple[DiscardReason, ...]:
        """Aplica las reglas del filtro de descarte hidrológico.

        * ``ISOTERMA_BAJA``: ``freezing_level_m < freezing_level_threshold_m``.
        * ``SUELO_SECO``: ``soil_moisture_m3m3 < dry_soil_threshold_m3m3``.
        """
        cfg = self._config
        reasons: list[DiscardReason] = []
        if conditions.freezing_level_m < cfg.freezing_level_threshold_m:
            reasons.append(DiscardReason.ISOTERMA_BAJA)
        if conditions.soil_moisture_m3m3 < cfg.dry_soil_threshold_m3m3:
            reasons.append(DiscardReason.SUELO_SECO)
        return tuple(reasons)

    # ------------------------------------------------------------------ helpers

    def _select_forecasts(
        self, forecasts: Sequence[ModelPrecipitationForecast]
    ) -> list[ModelPrecipitationForecast]:
        """Valida el conjunto de pronósticos y conserva sólo modelos con peso."""
        models = [f.model for f in forecasts]
        if len(models) != len(set(models)):
            raise InsufficientModelDataError("Se recibió más de un pronóstico para el mismo modelo.")
        usable = [f for f in forecasts if f.model in self._config.model_weights]
        if len(usable) < self._config.min_models_required:
            raise InsufficientModelDataError(
                f"Se requieren al menos {self._config.min_models_required} modelos; "
                f"se recibieron {len(usable)} utilizables."
            )
        if len({f.horizon_hours for f in usable}) > 1:
            raise InsufficientModelDataError("Todos los pronósticos deben cubrir la misma ventana horaria.")
        return usable

    def _agreement_ratio(
        self, values: Sequence[float], weights: Sequence[float], consensus_level: RiskLevel
    ) -> float:
        """Fracción del peso cuyos modelos, individualmente, respaldan el nivel.

        Para VERDE, "respaldar" significa que el modelo también indica VERDE;
        para niveles superiores, que indica un nivel igual o mayor.
        """
        cfg = self._config
        if consensus_level is RiskLevel.VERDE:
            support = (w for v, w in zip(values, weights, strict=True) if cfg.level_for(v) is RiskLevel.VERDE)
        else:
            support = (w for v, w in zip(values, weights, strict=True) if cfg.level_for(v) >= consensus_level)
        return cm.clamp(sum(support))

    @staticmethod
    def _flood_summary(mm: float, level: RiskLevel, n_models: int, reasons: tuple[DiscardReason, ...] = ()) -> str:
        context = (
            f"El nivel se rebajó por {_describe_reasons(reasons)}." if reasons
            else "El suelo está húmedo y la isoterma alta."
        )
        return (
            f"Alerta {level.value}: el consenso de {n_models} modelos globales proyecta "
            f"{mm:.1f} mm de precipitación. {context} "
            "Existe riesgo de crecida y desborde de cauces. Siga las instrucciones de la autoridad."
        )

    @staticmethod
    def _tranquility_summary(mm: float, reasons: tuple[DiscardReason, ...]) -> str:
        if not reasons:
            return f"Reporte de Tranquilidad: precipitación de consenso {mm:.1f} mm, bajo umbrales de riesgo."
        return (
            f"Reporte de Tranquilidad: aunque se esperan {mm:.1f} mm, no hay riesgo de desborde "
            f"({_describe_reasons(reasons)})."
        )

    @staticmethod
    def _thunderstorm_summary(
        mm: float, reasons: tuple[DiscardReason, ...], conditions: HydrologicalConditions
    ) -> str:
        why = f" ({_describe_reasons(reasons)})" if reasons else ""
        return (
            f"Aviso de Tormenta Eléctrica (CAPE {conditions.cape_j_kg:.0f} J/kg) con {mm:.1f} mm "
            f"esperados, SIN riesgo de desborde{why}. "
            "Evite espacios abiertos y resguárdese bajo techo durante la actividad eléctrica."
        )


class _Metrics(NamedTuple):
    """Métricas intermedias comunes a todos los comunicados."""

    raw_level: RiskLevel
    consensus_mm: float
    cv: float
    agreement: float
    confidence: float
    models_used: tuple[WeatherModel, ...]
    downgraded: bool

    def to_result(
        self,
        *,
        outcome: ConsensusOutcome,
        level: RiskLevel,
        threat: ThreatType,
        reasons: tuple[DiscardReason, ...],
        summary: str,
    ) -> ConsensusResult:
        return ConsensusResult(
            outcome=outcome,
            risk_level=level,
            raw_risk_level=self.raw_level,
            threat_type=threat,
            flood_risk=outcome is ConsensusOutcome.ALERTA_DESBORDE,
            weighted_precipitation_mm=round(self.consensus_mm, 2),
            dispersion_cv=round(self.cv, 4),
            agreement_ratio=round(self.agreement, 4),
            confidence=round(self.confidence, 4),
            models_used=self.models_used,
            discard_reasons=reasons,
            downgraded_by_disagreement=self.downgraded,
            summary=summary,
        )


_REASON_TEXT: Mapping[DiscardReason, str] = {
    DiscardReason.ISOTERMA_BAJA: "isoterma 0 °C baja: la precipitación cae como nieve en altura",
    DiscardReason.SUELO_SECO: "suelo seco con alta capacidad de infiltración",
}


def _describe_reasons(reasons: tuple[DiscardReason, ...]) -> str:
    return "; ".join(_REASON_TEXT[r] for r in reasons)

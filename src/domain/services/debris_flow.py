"""``DebrisFlowAssessmentService``: posibilidad de aluvión en la ubicación del usuario.

Un aluvión (flujo de detritos) requiere dos condiciones a la vez:

1. **Susceptibilidad del lugar**: una quebrada a menos de 500 m, o un estero
   a menos de 500 m en terreno de montaña (≥ 400 m s.n.m.), donde los
   esteros se comportan como quebradas.
2. **Detonante meteorológico**: lluvia fuerte con isoterma 0 °C alta. En la
   zona central de Chile los aluviones ocurren cuando llueve —y no nieva—
   sobre las cuencas altas; con isoterma baja la precipitación queda como
   nieve y el riesgo cae.

Niveles (lluvia en 24 h, con isoterma ≥ 2200 m):
AMARILLO ≥ 15 mm · NARANJA ≥ 30 mm · ROJO ≥ 50 mm.

La lluvia considerada es la mayor entre la pronosticada para las próximas
24 h y la ya caída en las últimas 24 h: un temporal en curso cuenta aunque
el pronóstico restante sea bajo. Un chubasco intenso (≥ 10 mm/h) sube un
nivel, porque las quebradas responden a la intensidad más que al total.
Igual que en el desborde, si menos de la mitad del peso de los modelos
respalda el nivel (p. ej. sólo GFS ve lluvia fuerte), se baja un nivel.

.. warning::
   Son umbrales heurísticos iniciales, más bajos que los de desborde porque
   las cuencas pequeñas reaccionan en minutos. Deben calibrarse con los
   registros de SERNAGEOMIN y eventos históricos del Maule.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domain.value_objects.risk_level import RiskLevel


class DebrisFlowConfig(BaseModel):
    """Umbrales del análisis de aluvión."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    radius_km: float = Field(default=0.5, gt=0)
    mountain_elevation_m: float = Field(default=400.0, ge=0)
    min_freezing_level_m: float = Field(default=2200.0, gt=0)
    amarillo_mm: float = Field(default=15.0, gt=0)
    naranja_mm: float = Field(default=30.0, gt=0)
    rojo_mm: float = Field(default=50.0, gt=0)
    intense_hourly_mm: float = Field(default=10.0, gt=0)
    min_agreement_ratio: float = Field(default=0.5, gt=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not (self.amarillo_mm < self.naranja_mm < self.rojo_mm):
            raise ValueError("Umbrales de aluvión deben ser crecientes.")
        return self


class DebrisFlowAssessment(BaseModel):
    """Resultado inmutable del análisis de aluvión."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    susceptible: bool
    level: RiskLevel
    reason: str


class DebrisFlowAssessmentService:
    """Combina susceptibilidad del lugar y detonante meteorológico. Sin estado ni I/O."""

    def __init__(self, config: DebrisFlowConfig | None = None) -> None:
        self._config = config or DebrisFlowConfig()

    @property
    def config(self) -> DebrisFlowConfig:
        return self._config

    def assess(
        self,
        *,
        nearest_quebrada_km: float | None,
        nearest_stream_km: float | None,
        elevation_m: float | None,
        rain_24h_mm: float | None,
        freezing_level_m: float | None,
        recent_rain_24h_mm: float | None = None,
        max_hourly_mm: float | None = None,
        model_rain_mm: Sequence[tuple[float, float]] | None = None,
    ) -> DebrisFlowAssessment:
        cfg = self._config
        near_quebrada = nearest_quebrada_km is not None and nearest_quebrada_km <= cfg.radius_km
        mountain_stream = (
            nearest_stream_km is not None
            and nearest_stream_km <= cfg.radius_km
            and elevation_m is not None
            and elevation_m >= cfg.mountain_elevation_m
        )
        if not (near_quebrada or mountain_stream):
            return DebrisFlowAssessment(
                susceptible=False,
                level=RiskLevel.VERDE,
                reason="Sin quebradas ni esteros de montaña a menos de 500 m: lugar no expuesto a aluviones.",
            )

        place = (
            f"Hay una quebrada a {nearest_quebrada_km * 1000:.0f} m"
            if near_quebrada and nearest_quebrada_km is not None
            else f"Hay un estero de montaña a {(nearest_stream_km or 0) * 1000:.0f} m"
        )
        if rain_24h_mm is None or freezing_level_m is None:
            return DebrisFlowAssessment(
                susceptible=True,
                level=RiskLevel.VERDE,
                reason=f"{place}. Sin pronóstico disponible: ante lluvia intensa, aléjese del cauce.",
            )
        if freezing_level_m < cfg.min_freezing_level_m:
            return DebrisFlowAssessment(
                susceptible=True,
                level=RiskLevel.VERDE,
                reason=f"{place}, pero la isoterma está baja ({freezing_level_m:.0f} m): en altura precipita nieve.",
            )

        level = self.trigger_level(
            rain_24h_mm=rain_24h_mm,
            freezing_level_m=freezing_level_m,
            recent_rain_24h_mm=recent_rain_24h_mm,
            max_hourly_mm=max_hourly_mm,
            model_rain_mm=model_rain_mm,
        )
        rain = self.effective_rain_mm(rain_24h_mm, recent_rain_24h_mm)
        if level is RiskLevel.VERDE:
            reason = f"{place}. Con {rain:.0f} mm en 24 h no hay condiciones de aluvión."
        else:
            trigger = self.describe_trigger(rain_24h_mm, freezing_level_m, recent_rain_24h_mm, max_hourly_mm)
            reason = (
                f"POSIBLE ALUVIÓN: {place.lower()}, {trigger}. "
                "Aléjese del cauce y busque terreno alto lateral, no aguas abajo."
            )
        return DebrisFlowAssessment(susceptible=True, level=level, reason=reason)

    # ------------------------------------------------------------ detonante

    @staticmethod
    def effective_rain_mm(rain_24h_mm: float, recent_rain_24h_mm: float | None) -> float:
        """Mayor entre lo pronosticado (24 h) y lo ya caído (últimas 24 h)."""
        return max(rain_24h_mm, recent_rain_24h_mm or 0.0)

    def trigger_level(
        self,
        *,
        rain_24h_mm: float,
        freezing_level_m: float,
        recent_rain_24h_mm: float | None = None,
        max_hourly_mm: float | None = None,
        model_rain_mm: Sequence[tuple[float, float]] | None = None,
    ) -> RiskLevel:
        """Nivel del detonante meteorológico, sin mirar el lugar.

        Lo usa el monitor de zonas para decidir si avisar a quienes viven
        cerca de quebradas: sólo hay alerta si la lluvia la justifica.

        Args:
            model_rain_mm: Pares (lluvia 24 h, peso) de cada modelo. Si se
                entregan, se rebaja un nivel cuando el respaldo es insuficiente.
        """
        level = self._raw_level(rain_24h_mm, freezing_level_m, recent_rain_24h_mm, max_hourly_mm)
        if level is RiskLevel.VERDE or not model_rain_mm:
            return level
        total = sum(w for _, w in model_rain_mm)
        support = sum(
            w
            for mm, w in model_rain_mm
            if self._raw_level(mm, freezing_level_m, recent_rain_24h_mm, max_hourly_mm) >= level
        )
        if total > 0 and support / total < self._config.min_agreement_ratio:
            return level.deescalate()
        return level

    def _raw_level(
        self,
        rain_24h_mm: float,
        freezing_level_m: float,
        recent_rain_24h_mm: float | None,
        max_hourly_mm: float | None,
    ) -> RiskLevel:
        cfg = self._config
        if freezing_level_m < cfg.min_freezing_level_m:
            return RiskLevel.VERDE
        rain = self.effective_rain_mm(rain_24h_mm, recent_rain_24h_mm)
        if rain >= cfg.rojo_mm:
            level = RiskLevel.ROJO
        elif rain >= cfg.naranja_mm:
            level = RiskLevel.NARANJA
        elif rain >= cfg.amarillo_mm:
            level = RiskLevel.AMARILLO
        else:
            level = RiskLevel.VERDE
        if max_hourly_mm is not None and max_hourly_mm >= cfg.intense_hourly_mm:
            level = RiskLevel.most_severe(level.escalate(), RiskLevel.AMARILLO)
        return level

    def describe_trigger(
        self,
        rain_24h_mm: float,
        freezing_level_m: float,
        recent_rain_24h_mm: float | None = None,
        max_hourly_mm: float | None = None,
    ) -> str:
        """Frase legible del detonante (para recomendaciones y alertas push)."""
        parts = [f"se esperan {rain_24h_mm:.0f} mm en 24 h"]
        if recent_rain_24h_mm:
            parts.append(f"ya cayeron {recent_rain_24h_mm:.0f} mm")
        if max_hourly_mm is not None and max_hourly_mm >= self._config.intense_hourly_mm:
            parts.append(f"chubascos de hasta {max_hourly_mm:.0f} mm/h")
        return ", ".join(parts) + f" con isoterma alta ({freezing_level_m:.0f} m)"

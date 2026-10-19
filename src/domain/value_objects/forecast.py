"""Value Objects de entrada y salida del motor de consenso."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.risk_level import RiskLevel
from src.domain.value_objects.threat_type import ThreatType, WeatherModel


class ModelPrecipitationForecast(BaseModel):
    """Pronóstico de precipitación de un único modelo global para una ventana.

    Attributes:
        model: Modelo de Open-Meteo que emite el pronóstico.
        accumulated_precipitation_mm: Precipitación acumulada en la ventana.
        horizon_hours: Largo de la ventana de pronóstico, en horas.
        hourly_mm: Lluvia de cada hora de la ventana, desde la hora actual (vacío si no se pidió).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: WeatherModel
    accumulated_precipitation_mm: float = Field(ge=0.0)
    horizon_hours: int = Field(default=24, gt=0, le=384)
    hourly_mm: tuple[float, ...] = ()


class HydrologicalConditions(BaseModel):
    """Estado hidrológico/atmosférico usado por el filtro de descarte.

    Attributes:
        freezing_level_m: Altura de la isoterma 0 °C (m s.n.m.).
        soil_moisture_m3m3: Humedad volumétrica del suelo (m³/m³), 0 a 1.
        cape_j_kg: Energía potencial convectiva disponible (J/kg); indica
            potencial de tormenta eléctrica.
        recent_precipitation_mm: Lluvia de las últimas 24 h (lo que ya está
            cayendo), según el análisis horario del modelo.
        max_hourly_precipitation_mm: Intensidad máxima horaria entre las
            últimas 24 h y la ventana pronosticada (mm/h).
        cape_hourly: CAPE de cada hora de la ventana, desde la hora actual
            (para saber *cuándo* puede haber tormenta eléctrica).
        max_wind_gust_kmh: Ráfaga máxima a 10 m en la ventana (km/h); ``None``
            si el modelo no la entregó.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    freezing_level_m: float = Field(ge=0.0)
    soil_moisture_m3m3: float = Field(ge=0.0, le=1.0)
    cape_j_kg: float = Field(default=0.0, ge=0.0)
    recent_precipitation_mm: float = Field(default=0.0, ge=0.0)
    max_hourly_precipitation_mm: float = Field(default=0.0, ge=0.0)
    cape_hourly: tuple[float, ...] = ()
    max_wind_gust_kmh: float | None = Field(default=None, ge=0.0)


class RiverDischargeForecast(BaseModel):
    """Pronóstico diario de caudal (GloFAS) para un punto de la red fluvial.

    Attributes:
        location: Punto consultado (debe caer sobre el cauce en la grilla de ~5 km).
        dates: Días pronosticados.
        discharge_m3s: Caudal pronosticado (control determinístico), por día.
        discharge_max_m3s: Máximo del ensamble por día (escenario pesimista).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    location: Coordinates
    dates: tuple[date, ...] = Field(min_length=1)
    discharge_m3s: tuple[float, ...]
    discharge_max_m3s: tuple[float, ...] | None = None

    @model_validator(mode="after")
    def _aligned(self) -> Self:
        n = len(self.dates)
        if len(self.discharge_m3s) != n or (
            self.discharge_max_m3s is not None and len(self.discharge_max_m3s) != n
        ):
            raise ValueError("Las series de caudal deben tener el mismo largo que 'dates'.")
        return self

    @property
    def peak_discharge_m3s(self) -> float:
        """Caudal máximo del horizonte, usando el máximo del ensamble si existe."""
        series = self.discharge_max_m3s or self.discharge_m3s
        return max(series)

    @property
    def peak_date(self) -> date:
        """Día en que se alcanza :attr:`peak_discharge_m3s`."""
        series = self.discharge_max_m3s or self.discharge_m3s
        return self.dates[series.index(max(series))]


class DiscardReason(StrEnum):
    """Motivos por los que se descarta el riesgo de desborde."""

    ISOTERMA_BAJA = "ISOTERMA_BAJA"
    """La precipitación cae como nieve en la cuenca alta: no escurre de inmediato."""

    SUELO_SECO = "SUELO_SECO"
    """El suelo tiene capacidad de infiltración: no se genera escorrentía relevante."""


class ConsensusOutcome(StrEnum):
    """Tipo de comunicado que emite el motor de consenso."""

    ALERTA_DESBORDE = "ALERTA_DESBORDE"
    AVISO_TORMENTA_ELECTRICA = "AVISO_TORMENTA_ELECTRICA"
    REPORTE_TRANQUILIDAD = "REPORTE_TRANQUILIDAD"


class ConsensusResult(BaseModel):
    """Resultado inmutable y auditable de una evaluación de consenso.

    Attributes:
        outcome: Tipo de comunicado resultante.
        risk_level: Nivel final, tras desacuerdo entre modelos y filtro hidrológico.
        raw_risk_level: Nivel derivado sólo de la precipitación ponderada.
        threat_type: Amenaza principal asociada al comunicado.
        flood_risk: ``True`` si existe riesgo de desborde/inundación.
        weighted_precipitation_mm: Precipitación de consenso (media ponderada).
        dispersion_cv: Coeficiente de variación ponderado entre modelos.
        agreement_ratio: Fracción del peso que respalda el nivel de consenso.
        confidence: Confianza 0-1 (acuerdo × (1 − dispersión) × cobertura).
        models_used: Modelos que participaron efectivamente.
        discard_reasons: Reglas de descarte hidrológico activadas.
        downgraded_by_disagreement: Si el nivel bajó por desacuerdo entre modelos.
        summary: Mensaje legible en español para operadores y ciudadanía.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: ConsensusOutcome
    risk_level: RiskLevel
    raw_risk_level: RiskLevel
    threat_type: ThreatType
    flood_risk: bool
    weighted_precipitation_mm: float = Field(ge=0.0)
    dispersion_cv: float = Field(ge=0.0)
    agreement_ratio: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    models_used: tuple[WeatherModel, ...]
    discard_reasons: tuple[DiscardReason, ...] = ()
    downgraded_by_disagreement: bool = False
    summary: str


class PointOutlook(BaseModel):
    """Resumen del tiempo de las próximas horas en un punto (vista regional).

    Usa un solo modelo (``best_match``) para poder consultar muchas comunas en
    una llamada: es un panorama, no el diagnóstico con consenso de 3 modelos.

    Attributes:
        rain_mm: Lluvia acumulada en la ventana (mm).
        max_hourly_mm: Intensidad horaria máxima (mm/h).
        freezing_level_m: Isoterma 0 °C máxima (m); ``None`` si no vino.
        cape_j_kg: CAPE máximo (J/kg).
        max_wind_gust_kmh: Ráfaga máxima (km/h); ``None`` si no vino.
        storm_hours: Horas con CAPE de tormenta *y* lluvia en esa misma hora.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rain_mm: float = Field(ge=0.0)
    max_hourly_mm: float = Field(default=0.0, ge=0.0)
    freezing_level_m: float | None = None
    cape_j_kg: float = Field(default=0.0, ge=0.0)
    max_wind_gust_kmh: float | None = Field(default=None, ge=0.0)
    storm_hours: int = Field(default=0, ge=0)


class EnsembleMember(BaseModel):
    """Una corrida de un pronóstico por conjunto, hora a hora desde ahora.

    Attributes:
        model: Ensemble de origen (``icon_seamless_eps``, ``gfs025``).
        precipitation: Lluvia de cada hora (mm).
        cape: CAPE de cada hora (J/kg); vacío si el ensemble no lo entrega.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    precipitation: tuple[float, ...]
    cape: tuple[float, ...] = ()

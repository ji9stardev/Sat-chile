"""Pruebas del ConsensusEngineService y su matemática pura."""

import pytest

from src.domain.exceptions import InsufficientModelDataError
from src.domain.services import ConsensusConfig, ConsensusEngineService
from src.domain.services import consensus_math as cm
from src.domain.value_objects import (
    ConsensusOutcome,
    DiscardReason,
    HydrologicalConditions,
    ModelPrecipitationForecast,
    RiskLevel,
    ThreatType,
    WeatherModel,
)

WET = HydrologicalConditions(freezing_level_m=3000, soil_moisture_m3m3=0.40, cape_j_kg=100)


def forecasts(ecmwf: float, icon: float, gfs: float) -> list[ModelPrecipitationForecast]:
    return [
        ModelPrecipitationForecast(model=WeatherModel.ECMWF_IFS025, accumulated_precipitation_mm=ecmwf),
        ModelPrecipitationForecast(model=WeatherModel.ICON_SEAMLESS, accumulated_precipitation_mm=icon),
        ModelPrecipitationForecast(model=WeatherModel.GFS_SEAMLESS, accumulated_precipitation_mm=gfs),
    ]


@pytest.fixture
def engine() -> ConsensusEngineService:
    return ConsensusEngineService()


# ---------------------------------------------------------------- matemática pura


def test_weighted_mean_and_cv() -> None:
    assert cm.weighted_mean([10, 20], [1, 1]) == pytest.approx(15)
    assert cm.weighted_mean([10, 20], [3, 1]) == pytest.approx(12.5)
    assert cm.coefficient_of_variation([0, 0, 0], [1, 1, 1]) == 0.0
    assert cm.coefficient_of_variation([10, 10], [1, 2]) == pytest.approx(0.0)


def test_normalize_weights_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        cm.normalize_weights([0, 0])
    with pytest.raises(ValueError):
        cm.normalize_weights([1, -1])


# ---------------------------------------------------------------- consenso


def test_dry_forecast_gives_tranquility(engine: ConsensusEngineService) -> None:
    result = engine.evaluate(forecasts(2, 3, 1), WET)
    assert result.outcome is ConsensusOutcome.REPORTE_TRANQUILIDAD
    assert result.risk_level is RiskLevel.VERDE
    assert not result.flood_risk


def test_agreeing_heavy_rain_on_wet_soil_gives_red_flood_alert(engine: ConsensusEngineService) -> None:
    result = engine.evaluate(forecasts(90, 85, 80), WET)
    assert result.outcome is ConsensusOutcome.ALERTA_DESBORDE
    assert result.risk_level is RiskLevel.ROJO
    assert result.threat_type is ThreatType.DESBORDE_RIO
    assert result.flood_risk
    assert result.confidence > 0.85
    assert result.weighted_precipitation_mm == pytest.approx(0.45 * 90 + 0.30 * 85 + 0.25 * 80)


def test_single_outlier_model_is_downgraded(engine: ConsensusEngineService) -> None:
    # Sólo GFS (peso 0.25) ve un evento extremo; el consenso cae en AMARILLO
    # pero sólo 25 % del peso lo respalda → se rebaja a VERDE.
    result = engine.evaluate(forecasts(5, 5, 120), WET)
    assert result.raw_risk_level is RiskLevel.AMARILLO
    assert result.downgraded_by_disagreement
    assert result.risk_level is RiskLevel.VERDE
    assert result.outcome is ConsensusOutcome.REPORTE_TRANQUILIDAD


def test_works_with_two_models_and_renormalizes(engine: ConsensusEngineService) -> None:
    result = engine.evaluate(forecasts(50, 50, 50)[:2], WET)
    assert result.weighted_precipitation_mm == pytest.approx(50)
    assert result.models_used == (WeatherModel.ECMWF_IFS025, WeatherModel.ICON_SEAMLESS)
    assert result.confidence < 1.0  # penalizada por cobertura incompleta


def test_insufficient_models_raises(engine: ConsensusEngineService) -> None:
    with pytest.raises(InsufficientModelDataError):
        engine.evaluate(forecasts(50, 50, 50)[:1], WET)


def test_duplicate_models_raise(engine: ConsensusEngineService) -> None:
    dup = forecasts(50, 50, 50)
    dup[1] = dup[0]
    with pytest.raises(InsufficientModelDataError):
        engine.evaluate(dup, WET)


def test_mismatched_horizons_raise(engine: ConsensusEngineService) -> None:
    fs = forecasts(50, 50, 50)
    fs[2] = ModelPrecipitationForecast(
        model=WeatherModel.GFS_SEAMLESS, accumulated_precipitation_mm=50, horizon_hours=48
    )
    with pytest.raises(InsufficientModelDataError):
        engine.evaluate(fs, WET)


# ---------------------------------------------------------------- filtro hidrológico


def test_low_freezing_level_discards_flood(engine: ConsensusEngineService) -> None:
    cold = HydrologicalConditions(freezing_level_m=1500, soil_moisture_m3m3=0.40)
    result = engine.evaluate(forecasts(90, 85, 80), cold)
    assert result.outcome is ConsensusOutcome.REPORTE_TRANQUILIDAD
    assert result.risk_level is RiskLevel.VERDE
    assert result.raw_risk_level is RiskLevel.ROJO
    assert result.discard_reasons == (DiscardReason.ISOTERMA_BAJA,)


def test_dry_soil_discards_flood(engine: ConsensusEngineService) -> None:
    dry = HydrologicalConditions(freezing_level_m=3000, soil_moisture_m3m3=0.20)
    result = engine.evaluate(forecasts(50, 45, 42), dry)
    assert result.outcome is ConsensusOutcome.REPORTE_TRANQUILIDAD
    assert result.discard_reasons == (DiscardReason.SUELO_SECO,)


def test_discard_with_high_cape_gives_thunderstorm_advisory(engine: ConsensusEngineService) -> None:
    storm = HydrologicalConditions(freezing_level_m=1700, soil_moisture_m3m3=0.15, cape_j_kg=1500)
    result = engine.evaluate(forecasts(50, 45, 42), storm)
    assert result.outcome is ConsensusOutcome.AVISO_TORMENTA_ELECTRICA
    assert result.threat_type is ThreatType.TORMENTA_ELECTRICA
    assert result.risk_level is RiskLevel.AMARILLO
    assert not result.flood_risk
    assert set(result.discard_reasons) == {DiscardReason.ISOTERMA_BAJA, DiscardReason.SUELO_SECO}
    assert "SIN riesgo de desborde" in result.summary


def test_thunderstorm_is_warned_even_with_little_rain(engine: ConsensusEngineService) -> None:
    convective = HydrologicalConditions(freezing_level_m=3000, soil_moisture_m3m3=0.40, cape_j_kg=1500)
    result = engine.evaluate(forecasts(5, 4, 6), convective)
    assert result.outcome is ConsensusOutcome.AVISO_TORMENTA_ELECTRICA
    assert result.risk_level is RiskLevel.AMARILLO
    assert result.discard_reasons == ()
    assert "SIN riesgo de desborde." in result.summary

    # Sin lluvia no hay disparador: el CAPE alto solo no es tormenta.
    assert engine.evaluate(forecasts(0, 0, 0), convective).outcome is ConsensusOutcome.REPORTE_TRANQUILIDAD


def test_flood_alert_mentions_thunderstorm_when_convective(engine: ConsensusEngineService) -> None:
    convective = HydrologicalConditions(freezing_level_m=3000, soil_moisture_m3m3=0.40, cape_j_kg=1500)
    result = engine.evaluate(forecasts(90, 85, 80), convective)
    assert result.outcome is ConsensusOutcome.ALERTA_DESBORDE
    assert "tormenta eléctrica" in result.summary


def test_thresholds_are_strict(engine: ConsensusEngineService) -> None:
    edge = HydrologicalConditions(freezing_level_m=1850, soil_moisture_m3m3=0.28)
    assert engine.discard_reasons(edge) == ()


def test_config_rejects_non_increasing_thresholds() -> None:
    with pytest.raises(ValueError):
        ConsensusConfig(amarillo_threshold_mm=50, naranja_threshold_mm=40)

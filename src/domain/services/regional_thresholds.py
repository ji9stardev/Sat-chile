"""Umbrales de lluvia e isoterma por macrozona de Chile.

Un mismo acumulado no significa lo mismo en Arica que en Puerto Montt: en
el Norte Grande 20-40 mm en un día bastaron para los aluviones de Atacama
(2015) y Tarapacá, mientras en el Sur esa lluvia es habitual en invierno.
Las cuencas del norte son además más altas, por lo que la isoterma que
separa lluvia de nieve sobre ellas también lo es.

.. warning::
   Los valores son **heurísticos iniciales**, escalados desde los del
   Maule (CENTRO). Antes de usarlos con la comunidad deben calibrarse con
   eventos históricos de cada macrozona (SERNAGEOMIN, DMC, DGA). No hay
   una fuente oficial única de umbrales para todo el país.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

from src.domain.services.consensus_engine import ConsensusConfig, ConsensusEngineService
from src.domain.services.debris_flow import DebrisFlowAssessmentService, DebrisFlowConfig
from src.domain.value_objects.admin_area import MacroZone
from src.domain.value_objects.risk_level import RiskLevel


class _Thresholds(NamedTuple):
    flood_mm: tuple[float, float, float]
    """Desborde: AMARILLO, NARANJA, ROJO (lluvia de consenso en 24 h)."""
    flood_freezing_m: float
    """Bajo esta isoterma se descarta el desborde (precipita nieve)."""
    debris_mm: tuple[float, float, float]
    """Aluvión / deslizamiento: AMARILLO, NARANJA, ROJO."""
    debris_freezing_m: float
    """Isoterma mínima para que llueva sobre las cuencas altas."""


THRESHOLDS: Mapping[MacroZone, _Thresholds] = {
    MacroZone.NORTE_GRANDE: _Thresholds((10, 20, 35), 2600, (8, 15, 25), 3000),
    MacroZone.NORTE_CHICO: _Thresholds((15, 30, 50), 2300, (10, 20, 35), 2700),
    MacroZone.CENTRO: _Thresholds((20, 40, 70), 1850, (15, 30, 50), 2200),
    MacroZone.SUR: _Thresholds((35, 60, 100), 1400, (25, 45, 70), 1600),
    MacroZone.AUSTRAL: _Thresholds((40, 70, 110), 1000, (30, 55, 80), 1200),
}


def rain_level(zone: MacroZone, rain_mm: float, thresholds: Mapping[MacroZone, _Thresholds] = THRESHOLDS) -> RiskLevel:
    """Nivel por lluvia en 24 h con los umbrales de desborde de la macrozona."""
    amarillo, naranja, rojo = thresholds[zone].flood_mm
    if rain_mm >= rojo:
        return RiskLevel.ROJO
    if rain_mm >= naranja:
        return RiskLevel.NARANJA
    if rain_mm >= amarillo:
        return RiskLevel.AMARILLO
    return RiskLevel.VERDE


class RegionalRules:
    """Entrega el motor de consenso y el servicio de aluvión de cada macrozona."""

    def __init__(self, thresholds: Mapping[MacroZone, _Thresholds] = THRESHOLDS) -> None:
        self._engines = {
            zone: ConsensusEngineService(
                ConsensusConfig(
                    amarillo_threshold_mm=t.flood_mm[0],
                    naranja_threshold_mm=t.flood_mm[1],
                    rojo_threshold_mm=t.flood_mm[2],
                    freezing_level_threshold_m=t.flood_freezing_m,
                )
            )
            for zone, t in thresholds.items()
        }
        self._debris = {
            zone: DebrisFlowAssessmentService(
                DebrisFlowConfig(
                    amarillo_mm=t.debris_mm[0],
                    naranja_mm=t.debris_mm[1],
                    rojo_mm=t.debris_mm[2],
                    min_freezing_level_m=t.debris_freezing_m,
                )
            )
            for zone, t in thresholds.items()
        }

    def engine(self, zone: MacroZone) -> ConsensusEngineService:
        return self._engines[zone]

    def debris(self, zone: MacroZone) -> DebrisFlowAssessmentService:
        return self._debris[zone]

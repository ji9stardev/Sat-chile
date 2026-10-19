"""Entidad ``GeoZone``: zona geográfica monitoreada (comuna, cuenca o sector)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from src.domain.entities.base import Entity
from src.domain.value_objects.coordinates import Coordinates, GeoPolygon
from src.domain.value_objects.risk_level import RiskLevel
from src.domain.value_objects.threat_type import ThreatType


class MauleProvince(StrEnum):
    """Provincias de la Región del Maule."""

    TALCA = "TALCA"
    CURICO = "CURICO"
    LINARES = "LINARES"
    CAUQUENES = "CAUQUENES"


class GeoZone(Entity):
    """Zona con geometría propia y nivel de riesgo vigente.

    Attributes:
        name: Nombre de la zona (p. ej. "Ribera Río Claro – Talca").
        commune: Comuna a la que pertenece.
        province: Provincia del Maule.
        boundary: Polígono GeoJSON de la zona.
        exposed_threats: Amenazas a las que está expuesta.
        river_alert_level_m: Altura de río que dispara alerta, si es ribereña.
        current_risk_level: Nivel de riesgo vigente.
    """

    name: str = Field(min_length=2, max_length=120)
    commune: str = Field(min_length=2, max_length=60)
    province: MauleProvince
    boundary: GeoPolygon
    exposed_threats: frozenset[ThreatType] = frozenset()
    river_alert_level_m: float | None = Field(default=None, gt=0.0)
    current_risk_level: RiskLevel = RiskLevel.VERDE

    @property
    def centroid(self) -> Coordinates:
        """Punto representativo de la zona (para consultas a Open-Meteo)."""
        return self.boundary.centroid

    def contains(self, point: Coordinates) -> bool:
        """``True`` si el punto cae dentro de la zona."""
        return self.boundary.contains(point)

    def is_exposed_to(self, threat: ThreatType) -> bool:
        """``True`` si la zona está catalogada como expuesta a la amenaza."""
        return threat in self.exposed_threats

    def update_risk(self, level: RiskLevel) -> bool:
        """Actualiza el nivel vigente. Devuelve ``True`` si hubo cambio."""
        if level == self.current_risk_level:
            return False
        self.current_risk_level = level
        return True

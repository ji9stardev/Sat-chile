"""Value Objects de división político-administrativa de Chile: regiones, macrozonas y comunas.

La **macrozona** agrupa regiones de clima parecido. Se usa para elegir
umbrales de lluvia: en el Norte Grande un aluvión se forma con mucha menos
lluvia que en el Sur, donde 40 mm en un día son habituales.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from functools import cached_property

from pydantic import BaseModel, ConfigDict, Field

from src.domain.value_objects.coordinates import Coordinates, GeoPolygon


class MacroZone(StrEnum):
    """Macrozonas climáticas de Chile continental."""

    NORTE_GRANDE = "NORTE_GRANDE"
    NORTE_CHICO = "NORTE_CHICO"
    CENTRO = "CENTRO"
    SUR = "SUR"
    AUSTRAL = "AUSTRAL"


CHILE_REGIONS: Mapping[str, tuple[str, MacroZone]] = {
    "CL-AP": ("Arica y Parinacota", MacroZone.NORTE_GRANDE),
    "CL-TA": ("Tarapacá", MacroZone.NORTE_GRANDE),
    "CL-AN": ("Antofagasta", MacroZone.NORTE_GRANDE),
    "CL-AT": ("Atacama", MacroZone.NORTE_GRANDE),
    "CL-CO": ("Coquimbo", MacroZone.NORTE_CHICO),
    "CL-VS": ("Valparaíso", MacroZone.CENTRO),
    "CL-RM": ("Metropolitana de Santiago", MacroZone.CENTRO),
    "CL-LI": ("Libertador General Bernardo O'Higgins", MacroZone.CENTRO),
    "CL-ML": ("Maule", MacroZone.CENTRO),
    "CL-NB": ("Ñuble", MacroZone.SUR),
    "CL-BI": ("Biobío", MacroZone.SUR),
    "CL-AR": ("La Araucanía", MacroZone.SUR),
    "CL-LR": ("Los Ríos", MacroZone.SUR),
    "CL-LL": ("Los Lagos", MacroZone.AUSTRAL),
    "CL-AI": ("Aysén del General Carlos Ibáñez del Campo", MacroZone.AUSTRAL),
    "CL-MA": ("Magallanes y de la Antártica Chilena", MacroZone.AUSTRAL),
}
"""Código ISO 3166-2 → (nombre, macrozona). Orden norte → sur."""

# Límites sur aproximados de cada macrozona, para cuando no se conoce la comuna.
_LATITUDE_BANDS: tuple[tuple[float, MacroZone], ...] = (
    (-29.3, MacroZone.NORTE_GRANDE),
    (-32.2, MacroZone.NORTE_CHICO),
    (-36.0, MacroZone.CENTRO),
    (-40.6, MacroZone.SUR),
)


def macrozone_for(region_code: str | None, latitude: float) -> MacroZone:
    """Macrozona por región; sin región conocida, por banda de latitud."""
    if region_code is not None and region_code in CHILE_REGIONS:
        return CHILE_REGIONS[region_code][1]
    for southern_limit, zone in _LATITUDE_BANDS:
        if latitude >= southern_limit:
            return zone
    return MacroZone.AUSTRAL


class AdminArea(BaseModel):
    """Comuna con su región y su límite (uno o más polígonos: islas incluidas).

    Attributes:
        comuna: Nombre de la comuna.
        region: Nombre de la región.
        region_code: Código ISO 3166-2 (``CL-ML``).
        parts: Anillos exteriores de la comuna.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    comuna: str = Field(min_length=2, max_length=80)
    region: str = Field(min_length=2, max_length=80)
    region_code: str = Field(pattern=r"^CL-[A-Z]{2}$")
    parts: tuple[GeoPolygon, ...] = Field(min_length=1)

    @cached_property
    def bbox(self) -> tuple[float, float, float, float]:
        """(lat_min, lat_max, lon_min, lon_max) para descartar rápido."""
        lats = [c.latitude for p in self.parts for c in p.ring]
        lons = [c.longitude for p in self.parts for c in p.ring]
        return min(lats), max(lats), min(lons), max(lons)

    @property
    def macrozone(self) -> MacroZone:
        return macrozone_for(self.region_code, (self.bbox[0] + self.bbox[1]) / 2)

    @cached_property
    def representative_point(self) -> Coordinates:
        """Un punto dentro de la comuna, lo más cerca posible del centro de su parte mayor.

        El centro del rectángulo puede caer fuera (comunas en forma de C, islas),
        así que se prueba una grilla y se elige el punto interior más cercano.
        """
        main = max(self.parts, key=lambda p: len(p.ring))
        lat_c = sum(c.latitude for c in main.ring) / len(main.ring)
        lon_c = sum(c.longitude for c in main.ring) / len(main.ring)
        center = Coordinates(latitude=lat_c, longitude=lon_c)
        if main.contains(center):
            return center
        lats = [c.latitude for c in main.ring]
        lons = [c.longitude for c in main.ring]
        steps = 12
        inside = [
            Coordinates(
                latitude=min(lats) + (max(lats) - min(lats)) * (i + 0.5) / steps,
                longitude=min(lons) + (max(lons) - min(lons)) * (j + 0.5) / steps,
            )
            for i in range(steps)
            for j in range(steps)
        ]
        inside = [c for c in inside if main.contains(c)]
        if not inside:
            return main.ring[0]
        return min(inside, key=lambda c: (c.latitude - lat_c) ** 2 + (c.longitude - lon_c) ** 2)

    def contains(self, point: Coordinates) -> bool:
        lat_min, lat_max, lon_min, lon_max = self.bbox
        if not (lat_min <= point.latitude <= lat_max and lon_min <= point.longitude <= lon_max):
            return False
        return any(p.contains(point) for p in self.parts)

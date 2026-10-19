"""Localidades (pueblos, villas, sectores, caseríos) para nombrar la ubicación."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.domain.value_objects.coordinates import Coordinates


class PlaceKind(StrEnum):
    """Tipo de lugar poblado según OpenStreetMap (``place=*``)."""

    CITY = "city"
    TOWN = "town"
    VILLAGE = "village"
    SUBURB = "suburb"
    QUARTER = "quarter"
    NEIGHBOURHOOD = "neighbourhood"
    HAMLET = "hamlet"
    LOCALITY = "locality"
    ISOLATED_DWELLING = "isolated_dwelling"

    @property
    def preference_km(self) -> float:
        """Ventaja de distancia: un pueblo a 1,5 km nombra mejor el lugar que una casa aislada a 1 km."""
        return {
            PlaceKind.CITY: 1.0,
            PlaceKind.TOWN: 1.0,
            PlaceKind.VILLAGE: 0.8,
            PlaceKind.SUBURB: 0.4,
            PlaceKind.QUARTER: 0.3,
            PlaceKind.NEIGHBOURHOOD: 0.2,
            PlaceKind.HAMLET: 0.3,
            PlaceKind.LOCALITY: 0.0,
            PlaceKind.ISOLATED_DWELLING: -0.3,
        }[self]


class Locality(BaseModel):
    """Lugar poblado con nombre."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: PlaceKind
    location: Coordinates

"""Entidad ``Watercourse``: río, estero o quebrada monitoreado."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.domain.entities.base import Entity
from src.domain.value_objects.coordinates import GeoLineString
from src.domain.value_objects.threat_type import ThreatType


class WatercourseKind(StrEnum):
    """Tipo de cauce."""

    RIO = "RIO"
    ESTERO = "ESTERO"
    QUEBRADA = "QUEBRADA"
    CANAL = "CANAL"
    """Canal de riego o acequia (artificial, caudal regulado)."""

    @property
    def primary_threat(self) -> ThreatType:
        """Quebradas: aluvión; canales: anegamiento; ríos y esteros: desborde."""
        if self is WatercourseKind.QUEBRADA:
            return ThreatType.REMOCION_EN_MASA
        if self is WatercourseKind.CANAL:
            return ThreatType.INUNDACION
        return ThreatType.DESBORDE_RIO


class Watercourse(Entity):
    """Eje de un cauce usado para medir la exposición de una persona.

    Attributes:
        name: Nombre oficial (p. ej. "Río Achibueno").
        kind: Río, estero o quebrada.
        commune: Comuna principal.
        geometry: Polilínea del eje del cauce.
        zone_id: Zona monitoreada asociada, si existe.
        geometry_source: Procedencia del trazado (p. ej. "DGA-BNA", "IDE Chile", "referencial").
    """

    name: str = Field(min_length=2, max_length=120)
    kind: WatercourseKind
    commune: str = Field(min_length=2, max_length=60)
    geometry: GeoLineString
    zone_id: UUID | None = None
    geometry_source: str = Field(default="referencial", max_length=60)

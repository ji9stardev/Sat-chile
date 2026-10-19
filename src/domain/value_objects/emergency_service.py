"""Servicios de emergencia locales (Carabineros, Bomberos, salud, municipalidad)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.domain.value_objects.coordinates import Coordinates


class ServiceKind(StrEnum):
    CARABINEROS = "CARABINEROS"
    """Comisarías, subcomisarías, tenencias y retenes (también PDI)."""
    BOMBEROS = "BOMBEROS"
    SALUD = "SALUD"
    """Hospitales, SAR, SAPU, CESFAM y postas rurales."""
    MUNICIPALIDAD = "MUNICIPALIDAD"
    PUNTO_ENCUENTRO = "PUNTO_ENCUENTRO"
    """Punto de encuentro o zona de seguridad (muchos son de evacuación por tsunami)."""
    ALBERGUE = "ALBERGUE"
    """Albergue o refugio techado."""


class EmergencyService(BaseModel):
    """Un servicio con su ubicación y, si se conoce, su teléfono directo.

    Attributes:
        kind: Tipo de servicio.
        name: Nombre (por ejemplo «4ta Comisaría Linares»).
        phone: Teléfono tal como figura en la fuente; ``None`` si no hay.
        location: Punto del servicio.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ServiceKind
    name: str = Field(min_length=1, max_length=160)
    phone: str | None = None
    location: Coordinates
    detail: str | None = Field(default=None, max_length=160)
    """Para qué amenaza sirve (``tsunami``, ``incendio``...) si la fuente lo indica."""


EMERGENCY_KINDS = (ServiceKind.CARABINEROS, ServiceKind.BOMBEROS, ServiceKind.SALUD, ServiceKind.MUNICIPALIDAD)
REFUGE_KINDS = (ServiceKind.PUNTO_ENCUENTRO, ServiceKind.ALBERGUE)

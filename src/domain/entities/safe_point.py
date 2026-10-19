"""Entidad ``SafePoint``: punto de encuentro / albergue de evacuación."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from src.domain.entities.base import Entity
from src.domain.exceptions import CapacityExceededError
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.threat_type import ThreatType


class SafePoint(Entity):
    """Lugar seguro al que se dirige a la población en una evacuación.

    Attributes:
        name: Nombre del lugar (p. ej. "Liceo Abate Molina").
        commune: Comuna.
        location: Ubicación del acceso principal.
        capacity: Capacidad máxima de personas.
        current_occupancy: Personas actualmente albergadas.
        safe_from: Amenazas frente a las que el lugar es seguro.
        is_operational: Si está habilitado en este momento.
        contact_phone: Teléfono de contacto del encargado.
    """

    name: str = Field(min_length=2, max_length=120)
    commune: str = Field(min_length=2, max_length=60)
    location: Coordinates
    capacity: int = Field(gt=0)
    current_occupancy: int = Field(default=0, ge=0)
    safe_from: frozenset[ThreatType] = frozenset()
    is_operational: bool = True
    contact_phone: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _occupancy_within_capacity(self) -> Self:
        if self.current_occupancy > self.capacity:
            raise ValueError("La ocupación no puede superar la capacidad.")
        return self

    @property
    def available_capacity(self) -> int:
        """Cupos libres."""
        return self.capacity - self.current_occupancy

    @property
    def occupancy_ratio(self) -> float:
        """Fracción de ocupación (0-1)."""
        return self.current_occupancy / self.capacity

    def is_safe_for(self, threat: ThreatType) -> bool:
        """``True`` si está operativo, tiene cupos y es seguro ante la amenaza."""
        return self.is_operational and self.available_capacity > 0 and threat in self.safe_from

    def distance_km(self, origin: Coordinates) -> float:
        """Distancia en línea recta desde ``origin``."""
        return self.location.distance_km(origin)

    def register_arrivals(self, people: int) -> None:
        """Registra el ingreso de personas.

        Raises:
            ValueError: Si ``people`` no es positivo.
            CapacityExceededError: Si no hay cupos suficientes.
        """
        if people <= 0:
            raise ValueError("La cantidad de personas debe ser positiva.")
        if people > self.available_capacity:
            raise CapacityExceededError(
                f"'{self.name}' sólo tiene {self.available_capacity} cupos disponibles."
            )
        self.current_occupancy += people

    def register_departures(self, people: int) -> None:
        """Registra la salida de personas (nunca deja la ocupación negativa)."""
        if people <= 0:
            raise ValueError("La cantidad de personas debe ser positiva.")
        self.current_occupancy = max(0, self.current_occupancy - people)

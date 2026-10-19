"""Entidad ``Subscriber``: dispositivo suscrito a alertas push."""

from __future__ import annotations

from uuid import UUID

from src.domain.entities.base import Entity
from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.notification import PushSubscription
from src.domain.value_objects.risk_level import RiskLevel


class Subscriber(Entity):
    """Navegador/dispositivo que recibe alertas.

    Attributes:
        subscription: Datos Web Push del navegador.
        location: Ubicación declarada (opcional) para alertas por cercanía.
        zone_ids: Zonas que el usuario sigue explícitamente.
        min_risk_level: Nivel mínimo a partir del cual quiere ser notificado.
        active: ``False`` cuando el servicio push informó que expiró.
    """

    subscription: PushSubscription
    location: Coordinates | None = None
    zone_ids: frozenset[UUID] = frozenset()
    min_risk_level: RiskLevel = RiskLevel.AMARILLO
    active: bool = True

    def wants(self, level: RiskLevel) -> bool:
        """``True`` si el suscriptor está activo y el nivel alcanza su mínimo."""
        return self.active and level >= self.min_risk_level

    def deactivate(self) -> None:
        """Marca la suscripción como expirada."""
        self.active = False

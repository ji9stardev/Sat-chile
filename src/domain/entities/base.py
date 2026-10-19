"""Clase base para entidades del dominio."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Instante actual en UTC (nunca *naive*), con precisión de milisegundos.

    Se trunca a milisegundos porque es la precisión de los almacenes de
    fechas habituales (BSON, JavaScript); así una entidad persistida y
    recuperada es idéntica a la original.
    """
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


class Entity(BaseModel):
    """Entidad con identidad propia.

    Dos entidades son iguales si y sólo si comparten ``id``, aunque difieran
    sus atributos. ``validate_assignment`` garantiza que las mutaciones
    hechas por los métodos de negocio sigan respetando las invariantes.
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=utc_now)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity):
            return NotImplemented
        return type(self) is type(other) and self.id == other.id

    def __hash__(self) -> int:
        return hash((type(self).__name__, self.id))

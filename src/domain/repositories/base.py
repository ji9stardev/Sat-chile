"""Contrato genérico de repositorio.

Las implementaciones concretas (MongoDB/Motor en la Fase 2) viven en la capa
de infraestructura; el dominio sólo depende de estas abstracciones
(Principio de Inversión de Dependencias).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from uuid import UUID

from src.domain.entities.base import Entity

E = TypeVar("E", bound=Entity)


class Repository(ABC, Generic[E]):
    """Operaciones CRUD mínimas y asíncronas sobre un agregado."""

    @abstractmethod
    async def get_by_id(self, entity_id: UUID) -> E | None:
        """Obtiene la entidad por id, o ``None`` si no existe."""

    @abstractmethod
    async def save(self, entity: E) -> None:
        """Inserta o actualiza (upsert) la entidad."""

    @abstractmethod
    async def delete(self, entity_id: UUID) -> bool:
        """Elimina la entidad. Devuelve ``True`` si existía."""

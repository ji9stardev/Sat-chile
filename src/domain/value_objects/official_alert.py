"""Alertas oficiales de SENAPRED (Servicio Nacional de Prevención y Respuesta ante Desastres)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class OfficialLevel(StrEnum):
    """Niveles de alerta de SENAPRED, de menor a mayor."""

    TEMPRANA_PREVENTIVA = "TEMPRANA_PREVENTIVA"
    AMARILLA = "AMARILLA"
    ROJA = "ROJA"

    @property
    def severity(self) -> int:
        return {OfficialLevel.TEMPRANA_PREVENTIVA: 1, OfficialLevel.AMARILLA: 2, OfficialLevel.ROJA: 3}[self]


class OfficialAlert(BaseModel):
    """Una alerta vigente publicada por SENAPRED.

    Attributes:
        id: Identificador de la publicación más reciente.
        title: Título tal como lo publica SENAPRED.
        level: Nivel declarado.
        hazard: Amenaza (``Tormentas eléctricas``, ``Evento meteorológico``...).
        region_codes: Regiones (ISO 3166-2) a las que aplica.
        comunas: Comunas nombradas en el título; vacío si es para toda la región o provincias.
        scope: Texto del alcance («Región del Maule», «Comunas de Mostazal, ...»).
        summary: Primeras líneas del contenido, sin HTML.
        published_at: Fecha y hora de la última actualización.
        url: Página de la alerta en senapred.cl.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str = Field(min_length=1, max_length=400)
    level: OfficialLevel
    hazard: str
    region_codes: tuple[str, ...]
    comunas: tuple[str, ...] = ()
    scope: str
    summary: str = ""
    published_at: datetime
    url: str

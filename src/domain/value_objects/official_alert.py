"""Alertas oficiales de SENAPRED (Servicio Nacional de Prevención y Respuesta ante Desastres)."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

_HYDROMET_WORDS = (
    "meteorolog", "lluvia", "precipitac", "frontal", "tormenta", "remocion", "aluvion",
    "inundac", "anegamiento", "crecida", "desborde",
)
"""Amenazas que este sistema evalúa (lluvia, crecidas, aluviones, tormentas). Incendios, volcanes,
sismos o emergencias sanitarias se informan, pero no cambian el nivel de crecida/aluvión."""


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


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

    @property
    def is_hydrometeorological(self) -> bool:
        """La amenaza es de lluvia, crecida, aluvión o tormenta (lo que evalúa SAT-Chile)."""
        hazard = _fold(self.hazard)
        return any(word in hazard for word in _HYDROMET_WORDS)

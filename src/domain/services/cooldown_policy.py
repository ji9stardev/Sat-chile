"""``CooldownPolicy``: anti-spam de notificaciones.

Notificar de más es tan peligroso como notificar de menos: la gente silencia
la aplicación ("fatiga de alertas"). La política decide, por suscriptor y
zona, si corresponde enviar:

* **Primera vez** → se envía.
* **Escalamiento** (nivel mayor que el último enviado) → se envía siempre,
  sin esperar el cooldown.
* **Desescalamiento** → se envía si pasó ``min_interval`` (evita el
  "parpadeo" cuando el nivel oscila en el borde de un umbral).
* **Mismo nivel** → sólo como recordatorio cuando vence el cooldown del
  nivel (más corto cuanto más grave: ROJO 15 min … VERDE 12 h).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.domain.value_objects.notification import NotificationRecord
from src.domain.value_objects.risk_level import RiskLevel

DEFAULT_COOLDOWNS: Mapping[RiskLevel, timedelta] = {
    RiskLevel.ROJO: timedelta(minutes=15),
    RiskLevel.NARANJA: timedelta(minutes=30),
    RiskLevel.AMARILLO: timedelta(hours=2),
    RiskLevel.VERDE: timedelta(hours=12),
}


class NotificationDecision(StrEnum):
    """Decisión de la política para un suscriptor."""

    PRIMERA = "PRIMERA"
    ESCALAMIENTO = "ESCALAMIENTO"
    DESESCALAMIENTO = "DESESCALAMIENTO"
    RECORDATORIO = "RECORDATORIO"
    SUPRIMIR = "SUPRIMIR"

    @property
    def should_send(self) -> bool:
        """``True`` salvo SUPRIMIR."""
        return self is not NotificationDecision.SUPRIMIR


class CooldownConfig(BaseModel):
    """Tiempos de espera por nivel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cooldowns: Mapping[RiskLevel, timedelta] = Field(default_factory=lambda: dict(DEFAULT_COOLDOWNS))
    min_interval: timedelta = timedelta(minutes=10)


class CooldownPolicy:
    """Política anti-spam. Sin estado ni I/O."""

    def __init__(self, config: CooldownConfig | None = None) -> None:
        self._config = config or CooldownConfig()

    def decide(self, last: NotificationRecord | None, level: RiskLevel, now: datetime) -> NotificationDecision:
        """Decide si notificar ``level`` dado el último envío ``last``."""
        if last is None:
            return NotificationDecision.PRIMERA
        elapsed = now - last.sent_at
        if level > last.risk_level:
            return NotificationDecision.ESCALAMIENTO
        if level < last.risk_level:
            return (
                NotificationDecision.DESESCALAMIENTO
                if elapsed >= self._config.min_interval
                else NotificationDecision.SUPRIMIR
            )
        cooldown = self._config.cooldowns.get(level, DEFAULT_COOLDOWNS[level])
        return NotificationDecision.RECORDATORIO if elapsed >= cooldown else NotificationDecision.SUPRIMIR

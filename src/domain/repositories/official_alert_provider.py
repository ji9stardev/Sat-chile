"""Puerto hacia las alertas oficiales (SENAPRED)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from src.domain.value_objects.official_alert import OfficialAlert


class OfficialAlertProvider(Protocol):
    async def active_alerts(self) -> Sequence[OfficialAlert]:
        """Alertas vigentes de todo el país; vacío si la fuente no responde."""
        ...

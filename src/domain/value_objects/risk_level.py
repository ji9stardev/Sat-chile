"""Value Object ``RiskLevel``: semáforo de riesgo del SAT-Maule."""

from __future__ import annotations

from enum import StrEnum


class RiskLevel(StrEnum):
    """Nivel de riesgo con orden total por severidad.

    El orden es ``VERDE < AMARILLO < NARANJA < ROJO``. Los operadores de
    comparación se basan en :attr:`severity` (no en el orden alfabético del
    string), de modo que ``max(niveles)`` devuelve siempre el más severo.
    """

    VERDE = "VERDE"
    AMARILLO = "AMARILLO"
    NARANJA = "NARANJA"
    ROJO = "ROJO"

    @property
    def severity(self) -> int:
        """Índice numérico de severidad (0 = VERDE, 3 = ROJO)."""
        return _SEVERITY_ORDER.index(self)

    @classmethod
    def from_severity(cls, severity: int) -> RiskLevel:
        """Construye un nivel a partir de su índice, acotado al rango válido."""
        clamped = max(0, min(severity, len(_SEVERITY_ORDER) - 1))
        return _SEVERITY_ORDER[clamped]

    def escalate(self, steps: int = 1) -> RiskLevel:
        """Devuelve el nivel ``steps`` posiciones más severo (tope: ROJO)."""
        return RiskLevel.from_severity(self.severity + steps)

    def deescalate(self, steps: int = 1) -> RiskLevel:
        """Devuelve el nivel ``steps`` posiciones menos severo (piso: VERDE)."""
        return RiskLevel.from_severity(self.severity - steps)

    @property
    def requires_public_alert(self) -> bool:
        """``True`` si el nivel exige difusión pública (NARANJA o ROJO)."""
        return self.severity >= RiskLevel.NARANJA.severity

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.severity < other.severity

    def __le__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.severity <= other.severity

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.severity > other.severity

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.severity >= other.severity

    @classmethod
    def most_severe(cls, *levels: RiskLevel) -> RiskLevel:
        """Devuelve el nivel más severo de los entregados (VERDE si no hay)."""
        return max(levels, default=RiskLevel.VERDE)


_SEVERITY_ORDER: tuple[RiskLevel, ...] = (
    RiskLevel.VERDE,
    RiskLevel.AMARILLO,
    RiskLevel.NARANJA,
    RiskLevel.ROJO,
)

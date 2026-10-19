"""Funciones matemáticas puras del motor de consenso.

Sin estado, sin I/O y sin dependencias del dominio: reciben secuencias de
números y devuelven números. Esto permite probarlas de forma aislada y
reutilizarlas (p. ej. en notebooks de calibración).
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def normalize_weights(weights: Sequence[float]) -> list[float]:
    """Escala los pesos para que sumen 1.

    Raises:
        ValueError: Si hay pesos negativos o la suma es cero.
    """
    if any(w < 0 for w in weights):
        raise ValueError("Los pesos no pueden ser negativos.")
    total = math.fsum(weights)
    if total <= 0:
        raise ValueError("La suma de los pesos debe ser positiva.")
    return [w / total for w in weights]


def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    """Media ponderada ``Σ wᵢ·xᵢ / Σ wᵢ``."""
    _check_lengths(values, weights)
    norm = normalize_weights(weights)
    return math.fsum(v * w for v, w in zip(values, norm, strict=True))


def weighted_std(values: Sequence[float], weights: Sequence[float]) -> float:
    """Desviación estándar poblacional ponderada."""
    _check_lengths(values, weights)
    norm = normalize_weights(weights)
    mean = math.fsum(v * w for v, w in zip(values, norm, strict=True))
    variance = math.fsum(w * (v - mean) ** 2 for v, w in zip(values, norm, strict=True))
    return math.sqrt(max(variance, 0.0))


def coefficient_of_variation(values: Sequence[float], weights: Sequence[float]) -> float:
    """CV ponderado ``σ / μ``; 0 si la media es ~0 (todos los modelos secos).

    Un CV alto indica que los modelos discrepan fuertemente entre sí.
    """
    mean = weighted_mean(values, weights)
    if mean <= 1e-9:
        return 0.0
    return weighted_std(values, weights) / mean


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Acota ``value`` al intervalo ``[low, high]``."""
    return max(low, min(high, value))


def _check_lengths(values: Sequence[float], weights: Sequence[float]) -> None:
    if len(values) != len(weights):
        raise ValueError("'values' y 'weights' deben tener el mismo largo.")
    if not values:
        raise ValueError("Se requiere al menos un valor.")

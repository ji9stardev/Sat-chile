"""Errores de infraestructura (servicios externos y persistencia)."""

from src.domain.exceptions import ExternalDataUnavailableError


class InfrastructureError(Exception):
    """Error base de la capa de infraestructura."""


class ExternalServiceError(InfrastructureError, ExternalDataUnavailableError):
    """Un servicio externo respondió con error o datos inutilizables.

    Hereda de :class:`ExternalDataUnavailableError` para que la capa de
    aplicación pueda capturarlo sin depender de la infraestructura.
    """


class DatabaseUnavailableError(InfrastructureError, ExternalDataUnavailableError):
    """MongoDB no responde (clúster inalcanzable o reintentos agotados)."""


class WeatherDataUnavailableError(ExternalServiceError):
    """Open-Meteo no entregó las variables necesarias para la ubicación/ventana."""

"""Clientes de APIs externas."""

from src.infrastructure.external_apis.cached_provider import CachedWeatherProvider
from src.infrastructure.external_apis.open_meteo_client import OpenMeteoClient

__all__ = ["CachedWeatherProvider", "OpenMeteoClient"]

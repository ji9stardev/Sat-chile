"""Routers de la API REST."""

from src.presentation.api.routers import gps, pwa_support, reports, subscriptions, zones

ALL_ROUTERS = (subscriptions.router, gps.router, reports.router, zones.router, pwa_support.router)

__all__ = ["ALL_ROUTERS"]

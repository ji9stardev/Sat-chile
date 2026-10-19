"""Endpoints de soporte para la PWA: clave VAPID, capas del mapa y monitor."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel

from src.application.dto import MapLayers, MonitorSummary
from src.application.use_cases import GetMapLayersUseCase, MonitorZonesUseCase
from src.presentation.api.dependencies import SettingsDep, get_map_layers, get_monitor_zones

router = APIRouter(prefix="/api", tags=["PWA"])


class PublicKeyResponse(BaseModel):
    """Clave para ``pushManager.subscribe({applicationServerKey})``."""

    public_key: str


@router.get(
    "/push/public-key",
    response_model=PublicKeyResponse,
    summary="Clave pública VAPID",
    responses={503: {"description": "Web Push no configurado en el servidor"}},
)
async def push_public_key(settings: SettingsDep, response: Response) -> PublicKeyResponse:
    """Clave pública VAPID que el navegador necesita para suscribirse a las alertas."""
    if not settings.vapid_public_key or settings.vapid_private_key is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Web Push no está configurado en el servidor.")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return PublicKeyResponse(public_key=settings.vapid_public_key)


@router.get("/map", response_model=MapLayers, summary="Capas del mapa (zonas, cauces, puntos seguros)")
async def map_layers(
    response: Response, use_case: Annotated[GetMapLayersUseCase, Depends(get_map_layers)]
) -> MapLayers:
    """Geometrías en pares ``[lat, lon]`` listas para Leaflet. La PWA las guarda para uso offline."""
    response.headers["Cache-Control"] = "public, max-age=120"
    return await use_case.execute()


@router.post(
    "/internal/monitor",
    response_model=MonitorSummary,
    summary="Ejecutar el monitor de zonas (cron externo)",
    include_in_schema=True,
    responses={404: {"description": "Endpoint deshabilitado (sin SAT_CRON_TOKEN)"}, 401: {}},
)
async def run_monitor(
    settings: SettingsDep,
    use_case: Annotated[MonitorZonesUseCase, Depends(get_monitor_zones)],
    x_cron_token: Annotated[str | None, Header()] = None,
) -> MonitorSummary:
    """Evalúa todas las zonas y despacha alertas. Pensado para un cron externo gratuito
    (cron-job.org, GitHub Actions) que además mantiene despierto un hosting gratuito.

    Requiere la cabecera ``X-Cron-Token`` igual a ``SAT_CRON_TOKEN``.
    """
    if settings.cron_token is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    expected = settings.cron_token.get_secret_value()
    if x_cron_token is None or not hmac.compare_digest(x_cron_token.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido.")
    return await use_case.execute()

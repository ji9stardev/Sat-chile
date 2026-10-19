"""Router del estado de zonas y salud del servicio."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from src.application.dto import ZoneStatus
from src.application.use_cases import GetZonesStatusUseCase
from src.presentation.api.dependencies import ContainerDep, get_zones_status
from src.presentation.api.schemas import HealthResponse

router = APIRouter(prefix="/api", tags=["Zonas"])


@router.get("/zones", response_model=list[ZoneStatus], summary="Estado actual de las zonas del Maule")
async def list_zones(
    response: Response,
    use_case: Annotated[GetZonesStatusUseCase, Depends(get_zones_status)],
    name: Annotated[
        list[str] | None,
        Query(description="Filtra por nombre de zona o comuna (repetible): ?name=Talca&name=Licantén"),
    ] = None,
) -> list[ZoneStatus]:
    """Zonas monitoreadas (Vilches, Talca, Cumpeo, Achibueno, Licantén…) ordenadas
    de mayor a menor riesgo, con su nivel efectivo (el más severo entre el nivel
    de la zona y sus alertas activas)."""
    response.headers["Cache-Control"] = "public, max-age=60"
    return await use_case.execute(name or [])


@router.get("/health", response_model=HealthResponse, tags=["Operación"], summary="Estado del servicio")
async def health(container: ContainerDep) -> HealthResponse:
    """Siempre responde 200 si la API está viva; informa el estado de cada dependencia."""
    checks = await container.health_check()
    return HealthResponse(status="ok" if all(checks.values()) else "degradado", **checks)

"""Router de suscripciones Web Push."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from src.application.dto import SubscriptionResult
from src.application.use_cases import RegisterSubscriberUseCase
from src.presentation.api.dependencies import get_register_subscriber
from src.presentation.api.schemas import ErrorResponse, SubscribeRequest

router = APIRouter(prefix="/api", tags=["Suscripciones"])


@router.post(
    "/subscribe",
    response_model=SubscriptionResult,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar el dispositivo para recibir alertas",
    responses={422: {"model": ErrorResponse}},
)
async def subscribe(
    body: SubscribeRequest,
    use_case: Annotated[RegisterSubscriberUseCase, Depends(get_register_subscriber)],
) -> SubscriptionResult:
    """Registra (o actualiza, si el ``endpoint`` ya existe) la suscripción Push del celular.

    * ``location``: ubicación base del vecino. Si no se indican ``zone_ids``,
      se suscribe automáticamente a las zonas que la contienen, y además
      recibirá alertas de zonas cercanas (≤ 5 km).
    * ``min_risk_level``: nivel mínimo a notificar (por defecto AMARILLO).

    La operación es idempotente: re-suscribir el mismo navegador no duplica registros.
    """
    return await use_case.execute(
        subscription=body.subscription.to_domain(),
        location=body.location.to_coordinates() if body.location else None,
        zone_ids=body.zone_ids,
        min_risk_level=body.min_risk_level,
    )

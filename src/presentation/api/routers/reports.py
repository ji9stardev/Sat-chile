"""Router de reportes ciudadanos (crowdsourcing)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from src.application.dto import CrowdReportOutcome
from src.application.use_cases import ProcessCrowdsourcingUseCase, WaterLevelReportCommand
from src.domain.value_objects.coordinates import Coordinates
from src.presentation.api.dependencies import SettingsDep, get_process_crowdsourcing
from src.presentation.api.schemas import ErrorResponse, WaterLevelReportRequest
from src.presentation.api.security import anonymize_device_id

router = APIRouter(prefix="/api", tags=["Reportes ciudadanos"])


@router.post(
    "/report-water-level",
    response_model=CrowdReportOutcome,
    status_code=status.HTTP_201_CREATED,
    summary="Reportar una crecida o desborde",
    responses={429: {"model": ErrorResponse, "description": "Reporte repetido dentro del cooldown"}},
)
async def report_water_level(
    body: WaterLevelReportRequest,
    settings: SettingsDep,
    use_case: Annotated[ProcessCrowdsourcingUseCase, Depends(get_process_crowdsourcing)],
) -> CrowdReportOutcome:
    """Registra la observación de un vecino.

    Un reporte aislado queda **PENDIENTE**. Cuando **3 vecinos distintos** reportan
    en un radio de **2 km** dentro de 3 horas, los reportes se verifican
    automáticamente y se emite una alerta ciudadana (AMARILLO, o NARANJA si
    la mayoría reporta DESBORDE) que se notifica por Web Push.

    El ``device_id`` se anonimiza con HMAC antes de guardarse. Cada dispositivo
    puede reportar una vez cada 10 minutos.
    """
    command = WaterLevelReportCommand(
        reporter_hash=anonymize_device_id(body.device_id, settings.reporter_hash_secret.get_secret_value()),
        location=Coordinates(latitude=body.lat, longitude=body.lon),
        observed_level=body.observed_level,
        threat_type=body.threat_type,
        description=body.description,
        photo_urls=tuple(body.photo_urls),
    )
    return await use_case.execute(command)

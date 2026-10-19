"""Router del diagnóstico de riesgo por GPS."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from src.application.dto import GpsRiskDiagnosis, NearbyService, PlaceOption, RegionOverview, WindyStudy
from src.application.use_cases import (
    EvaluateGpsRiskUseCase,
    RegionNotFoundError,
    RegionOverviewUseCase,
    WindyNotConfiguredError,
    WindyStudyUseCase,
    WindyUnavailableError,
)
from src.domain.value_objects.coordinates import Coordinates
from src.infrastructure.exceptions import ExternalServiceError, WeatherDataUnavailableError
from src.infrastructure.geodata import InMemoryRefugeFinder, PlaceIndex, chile_comunas, chile_refuges, load_localities
from src.presentation.api.dependencies import get_evaluate_gps_risk, get_region_overview, get_windy_study

router = APIRouter(prefix="/api", tags=["Diagnóstico GPS"])


@lru_cache(maxsize=1)
def get_place_index() -> PlaceIndex:
    return PlaceIndex(chile_comunas(), load_localities())


@router.get(
    "/region-overview",
    response_model=RegionOverview,
    summary="Nivel por lluvia de todas las comunas de una región",
)
async def region_overview(
    response: Response,
    region: Annotated[str, Query(pattern=r"^CL-[A-Z]{2}$", description="Código ISO de la región", examples=["CL-ML"])],
    use_case: Annotated[RegionOverviewUseCase, Depends(get_region_overview)],
) -> RegionOverview:
    """Panorama regional para comparar comunas (un modelo, sólo lluvia, isoterma y tormenta).

    No reemplaza el diagnóstico personal de ``/api/gps-check``.
    """
    try:
        overview = await use_case.execute(region)
    except RegionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="No hay comunas cargadas para esa región.") from exc
    except (ExternalServiceError, WeatherDataUnavailableError) as exc:
        raise HTTPException(status_code=503, detail="El pronóstico regional no está disponible ahora.") from exc
    response.headers["Cache-Control"] = "no-store"
    return overview


@router.get(
    "/windy-study",
    response_model=WindyStudy,
    summary="Estudio del punto con los modelos de Windy (GFS, ICON)",
)
async def windy_study(
    lat: Annotated[float, Query(ge=-90, le=90)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    use_case: Annotated[WindyStudyUseCase, Depends(get_windy_study)],
) -> WindyStudy:
    """Segunda opinión independiente de Open-Meteo: lluvia, chubascos, CAPE y ráfagas en 24 h."""
    try:
        return await use_case.execute(Coordinates(latitude=lat, longitude=lon))
    except WindyNotConfiguredError as exc:
        raise HTTPException(status_code=501, detail="Falta configurar SAT_WINDY_POINT_API_KEY.") from exc
    except WindyUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Windy no respondió ahora.") from exc


@router.get(
    "/refuges",
    response_model=list[NearbyService],
    summary="Puntos de encuentro y albergues cerca de un punto",
)
async def refuges(
    lat: Annotated[float, Query(ge=-90, le=90)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    finder: Annotated[InMemoryRefugeFinder, Depends(chile_refuges)],
    radius_km: Annotated[float, Query(gt=0, le=30)] = 10.0,
) -> list[NearbyService]:
    """Hasta 60, del más cercano al más lejano (OpenStreetMap, importado por región).

    Muchos puntos de encuentro son de evacuación por **tsunami**: sirven para
    alejarse del mar, no necesariamente de un río o una quebrada.
    """
    return [
        NearbyService(
            kind=r.kind,
            name=r.name,
            phone=r.phone,
            distance_km=round(d, 2),
            latitude=r.location.latitude,
            longitude=r.location.longitude,
            detail=r.detail,
        )
        for r, d in finder.find_near(Coordinates(latitude=lat, longitude=lon), radius_km)
    ]


@router.get(
    "/places",
    response_model=list[PlaceOption],
    summary="Buscar una comuna, pueblo o localidad para consultar su riesgo",
)
async def search_places(
    q: Annotated[str, Query(min_length=2, max_length=60, description="Nombre o parte del nombre", examples=["Talca"])],
    index: Annotated[PlaceIndex, Depends(get_place_index)],
) -> list[PlaceOption]:
    """Hasta 10 comunas, pueblos, villas o sectores de Chile cuyo nombre coincide, sin importar tildes.

    Cada resultado trae su comuna y un punto para pedir ``/api/gps-check``.
    """
    return [
        PlaceOption(
            name=m.name, kind=m.kind, comuna=m.comuna, region=m.region,
            latitude=m.location.latitude, longitude=m.location.longitude,
        )
        for m in index.search_places(q)
    ]


@router.get(
    "/gps-check",
    response_model=GpsRiskDiagnosis,
    summary="Diagnóstico de riesgo personalizado para una coordenada",
)
async def gps_check(
    response: Response,
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitud WGS84", examples=[-35.4264])],
    lon: Annotated[float, Query(ge=-180, le=180, description="Longitud WGS84", examples=[-71.6554])],
    use_case: Annotated[EvaluateGpsRiskUseCase, Depends(get_evaluate_gps_risk)],
) -> GpsRiskDiagnosis:
    """Evalúa el riesgo **en la ubicación exacta del usuario**:

    * **Peligro**: consenso de 3 modelos globales (ECMWF, ICON, GFS) con filtro
      hidrológico, más las alertas vigentes de las zonas que contienen el punto.
    * **Exposición**: distancia al cauce o quebrada más cercano y altura
      relativa sobre él (modelo de elevación de ~90 m).
    * **Resultado**: nivel personal, recomendaciones y hasta 3 puntos seguros.

    Si alguna fuente no responde, el diagnóstico se entrega igual con
    ``data_quality=PARCIAL`` y ``missing_sources`` indicando qué faltó.
    """
    response.headers["Cache-Control"] = "no-store"
    return await use_case.execute(Coordinates(latitude=lat, longitude=lon))

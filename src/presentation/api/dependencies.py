"""Dependencias FastAPI: exponen los casos de uso del contenedor a los routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from src.application.use_cases import (
    EvaluateGpsRiskUseCase,
    GetMapLayersUseCase,
    GetZonesStatusUseCase,
    MonitorZonesUseCase,
    ProcessCrowdsourcingUseCase,
    RegionOverviewUseCase,
    RegisterSubscriberUseCase,
    WindyStudyUseCase,
)
from src.infrastructure.config import Settings
from src.presentation.api.container import Container


def get_container(request: Request) -> Container:
    """Contenedor creado en el ``lifespan`` de la aplicación."""
    container: Container = request.app.state.container
    return container


def get_settings_dep(request: Request) -> Settings:
    """Configuración con la que se creó la aplicación."""
    settings: Settings = request.app.state.settings
    return settings


ContainerDep = Annotated[Container, Depends(get_container)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def get_evaluate_gps_risk(c: ContainerDep) -> EvaluateGpsRiskUseCase:
    return c.evaluate_gps_risk


def get_process_crowdsourcing(c: ContainerDep) -> ProcessCrowdsourcingUseCase:
    return c.process_crowdsourcing


def get_register_subscriber(c: ContainerDep) -> RegisterSubscriberUseCase:
    return c.register_subscriber


def get_zones_status(c: ContainerDep) -> GetZonesStatusUseCase:
    return c.get_zones_status


def get_map_layers(c: ContainerDep) -> GetMapLayersUseCase:
    return c.get_map_layers


def get_monitor_zones(c: ContainerDep) -> MonitorZonesUseCase:
    return c.monitor_zones


def get_region_overview(c: ContainerDep) -> RegionOverviewUseCase:
    return c.region_overview


def get_windy_study(c: ContainerDep) -> WindyStudyUseCase:
    return c.windy_study

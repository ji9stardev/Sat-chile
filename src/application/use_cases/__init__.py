"""Casos de uso de SAT-Maule."""

from src.application.use_cases.dispatch_alerts import DispatchAlertsUseCase
from src.application.use_cases.evaluate_gps_risk import EvaluateGpsRiskUseCase
from src.application.use_cases.get_map_layers import GetMapLayersUseCase
from src.application.use_cases.get_zones_status import GetZonesStatusUseCase
from src.application.use_cases.monitor_zones import MonitorZonesUseCase
from src.application.use_cases.process_crowdsourcing import (
    ProcessCrowdsourcingUseCase,
    WaterLevelReportCommand,
)
from src.application.use_cases.region_overview import RegionNotFoundError, RegionOverviewUseCase
from src.application.use_cases.register_subscriber import RegisterSubscriberUseCase
from src.application.use_cases.windy_study import (
    WindyNotConfiguredError,
    WindyStudyUseCase,
    WindyUnavailableError,
)

__all__ = [
    "DispatchAlertsUseCase",
    "EvaluateGpsRiskUseCase",
    "GetMapLayersUseCase",
    "GetZonesStatusUseCase",
    "MonitorZonesUseCase",
    "ProcessCrowdsourcingUseCase",
    "RegionNotFoundError",
    "RegionOverviewUseCase",
    "RegisterSubscriberUseCase",
    "WaterLevelReportCommand",
    "WindyNotConfiguredError",
    "WindyStudyUseCase",
    "WindyUnavailableError",
]

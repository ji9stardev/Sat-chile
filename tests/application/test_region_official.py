"""La vista regional aplica las alertas de SENAPRED por comuna."""

from __future__ import annotations

from datetime import UTC, datetime

from src.application.dto import ComunaOutlook
from src.application.use_cases.region_overview import with_official
from src.domain.value_objects.official_alert import OfficialAlert, OfficialLevel
from src.domain.value_objects.risk_level import RiskLevel


def alert(level: OfficialLevel, comunas: tuple[str, ...] = (), scope: str = "Región Metropolitana") -> OfficialAlert:
    return OfficialAlert(
        id="x", title="t", level=level, hazard="Remoción en masa", region_codes=("CL-RM",), comunas=comunas,
        scope=scope, published_at=datetime(2026, 10, 18, tzinfo=UTC), url="https://senapred.cl/alerta/x",
    )


def comuna(name: str) -> ComunaOutlook:
    return ComunaOutlook(comuna=name, level=RiskLevel.VERDE, rain_mm=6.0, max_hourly_mm=1.0)


ALERTS = [
    alert(OfficialLevel.ROJA, ("Las Condes", "Peñalolén")),
    alert(OfficialLevel.AMARILLA),  # toda la región
    alert(OfficialLevel.ROJA, scope="provincias de Talagante"),  # provincias: no se puede confirmar la comuna
]


def test_named_comuna_takes_the_red_alert() -> None:
    c = with_official(comuna("Penalolen"), ALERTS)
    assert (c.level, c.official_level) == (RiskLevel.ROJO, OfficialLevel.ROJA)


def test_region_wide_alert_applies_to_every_comuna() -> None:
    c = with_official(comuna("Maipú"), ALERTS)
    assert (c.level, c.official_level) == (RiskLevel.AMARILLO, OfficialLevel.AMARILLA)


def test_without_alerts_the_rain_level_stays() -> None:
    assert with_official(comuna("Maipú"), []).level is RiskLevel.VERDE

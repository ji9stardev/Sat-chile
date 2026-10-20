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


def test_region_wide_alert_is_noted_but_does_not_paint_every_comuna() -> None:
    c = with_official(comuna("Maipú"), ALERTS)
    assert (c.level, c.official_level) == (RiskLevel.VERDE, OfficialLevel.AMARILLA)


def test_alert_for_another_hazard_does_not_raise_the_level() -> None:
    fire = alert(OfficialLevel.ROJA, ("Maipú",)).model_copy(update={"hazard": "Incendio forestal"})
    c = with_official(comuna("Maipú"), [fire])
    assert (c.level, c.official_level, c.official_hazard) == (RiskLevel.VERDE, OfficialLevel.ROJA, "Incendio forestal")


def test_hazard_classification() -> None:
    def hazard(text: str) -> bool:
        return alert(OfficialLevel.AMARILLA).model_copy(update={"hazard": text}).is_hydrometeorological

    assert all(map(hazard, ("Evento meteorológico", "Remoción en Masa", "Inundaciones - Anegamientos", "Tormentas eléctricas")))
    assert not any(map(hazard, ("Incendio forestal", "Actividad Volcánica", "Evento zoosanitario", "Viento - Temporal viento")))


def test_without_alerts_the_rain_level_stays() -> None:
    assert with_official(comuna("Maipú"), []).level is RiskLevel.VERDE

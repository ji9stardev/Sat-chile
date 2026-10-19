"""Interpretación de las alertas publicadas por SENAPRED (títulos reales de octubre de 2026)."""

from __future__ import annotations

from src.domain.value_objects.official_alert import OfficialLevel
from src.infrastructure.external_apis.senapred_client import (
    parse_comunas,
    parse_hazard,
    parse_level,
    summarize,
    to_alerts,
)

RM = "774548fb"
REGIONS = {RM: "CL-RM", "cf05c71a": "CL-ML"}

ROJA = "Se declara Alerta Roja y cancela la Alerta Amarilla para las comunas de San José de Maipo, Puente Alto, Pirque, La Florida y Peñalolén por remoción en masa"
AMARILLA = "Monitoreo Alerta Amarilla para las comunas de San José de Maipo, Puente Alto, Pirque, La Florida y Peñalolén por remoción en masa"
REGIONAL = "Se declara Alerta Temprana Preventiva para la Región Metropolitana por evento meteorológico"


def item(title: str, when: str, *, slug: str, region: str = RM, active: bool = True, hazard: str = "Otros") -> dict:  # type: ignore[type-arg]
    return {
        "id": slug + when,
        "titulo": title,
        "contenido": "<p>Según la <strong>DMC</strong>:</p><table><tr><td>Lluvia</td><td>20-10</td></tr></table>",
        "fechaHora": when,
        "isActive": active,
        "isDeleted": False,
        "urlAccess": slug,
        "regionesIds": [region],
        "variableRiesgo": {"nombre": hazard},
    }


def test_levels_comunas_and_hazard_from_title() -> None:
    assert parse_level(ROJA) is OfficialLevel.ROJA
    assert parse_level(REGIONAL) is OfficialLevel.TEMPRANA_PREVENTIVA
    assert parse_comunas(ROJA) == ("San José de Maipo", "Puente Alto", "Pirque", "La Florida", "Peñalolén")
    assert parse_comunas(REGIONAL) == ()
    assert parse_hazard(REGIONAL, "Otros") == "Evento meteorológico"
    assert parse_hazard(REGIONAL, "Tormentas eléctricas") == "Tormentas eléctricas"


def test_summary_is_plain_text() -> None:
    assert summarize(item(REGIONAL, "x", slug="a")["contenido"]) == "Según la DMC: · Lluvia · 20-10"


def test_red_replaces_yellow_for_the_same_comunas_and_updates_are_grouped() -> None:
    alerts = to_alerts(
        [
            item(AMARILLA, "2026-10-18T10:00:00.000-03:00", slug="amarilla"),
            item(ROJA, "2026-10-18T20:00:00.000-03:00", slug="roja"),
            item(REGIONAL, "2026-10-17T09:00:00.000-03:00", slug="regional"),
            item("Monitoreo " + REGIONAL, "2026-10-18T21:00:00.000-03:00", slug="regional"),  # actualización
        ],
        REGIONS,
    )
    assert [(a.level, a.title.startswith("Monitoreo")) for a in alerts] == [
        (OfficialLevel.ROJA, False),
        (OfficialLevel.TEMPRANA_PREVENTIVA, True),
    ]
    assert alerts[1].url == "https://senapred.cl/alerta/regional"


def test_inactive_and_cancelled_alerts_are_dropped() -> None:
    alerts = to_alerts(
        [
            item(REGIONAL, "2026-10-18T09:00:00.000-03:00", slug="a", active=False),
            item("Se cancela Alerta Temprana Preventiva para la Región del Maule", "2026-10-18T09:00:00.000-03:00", slug="b", region="cf05c71a"),
            item(REGIONAL, "2026-10-18T09:00:00.000-03:00", slug="c", region="desconocida"),
        ],
        REGIONS,
    )
    assert alerts == []


def test_cancel_and_declare_keeps_the_declared_level() -> None:
    title = "Se cancela Alerta Temprana Preventiva y Declara Alerta Amarilla para la Región Metropolitana por evento meteorológico"
    assert parse_level(title) is OfficialLevel.AMARILLA
    assert parse_level("Se cancela Alerta Roja y declara Alerta Amarilla para las comunas de Pirque por remoción en masa") is OfficialLevel.AMARILLA
    alerts = to_alerts(
        [
            item(REGIONAL, "2026-10-16T18:38:00.000-03:00", slug="rm"),
            item(title, "2026-10-18T22:16:00.000-03:00", slug="rm"),
        ],
        REGIONS,
    )
    assert [a.level for a in alerts] == [OfficialLevel.AMARILLA]

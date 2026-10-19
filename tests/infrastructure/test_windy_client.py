"""Resumen de la Windy Point Forecast API y conclusión del estudio."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from src.application.use_cases.windy_study import WindyStudyUseCase, verdict
from src.domain.value_objects.coordinates import Coordinates
from src.infrastructure.external_apis.windy_client import WindyClient, summarize

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
H = 3_600_000
T0 = int(NOW.timestamp() * 1000)


def payload(cape: list[float], conv_m: list[float], rain_m: list[float]) -> dict:  # type: ignore[type-arg]
    n = len(cape)
    return {
        "ts": [T0 + 3 * H * (i + 1) for i in range(n)],
        "units": {
            "past3hprecip-surface": "m",
            "past3hconvprecip-surface": "m",
            "cape-surface": "J*kg-1",
            "gust-surface": "m*s-1",
        },
        "past3hprecip-surface": rain_m,
        "past3hconvprecip-surface": conv_m,
        "cape-surface": cape,
        "gust-surface": [10.0] * n,
    }


def test_storm_needs_cape_and_shower_in_the_same_block() -> None:
    p = payload(cape=[500, 50, 400], conv_m=[0.0, 0.002, 0.001], rain_m=[0.001, 0.004, 0.002])
    study = summarize("gfs", p, now=NOW)
    assert study is not None
    assert study.storm_periods == 1  # el tercer bloque: CAPE 400 y 1 mm de chubasco
    assert study.first_storm_at == datetime(2026, 10, 18, 18, tzinfo=UTC)
    assert (study.rain_mm, study.max_3h_mm, study.convective_mm) == (7.0, 4.0, 3.0)
    assert study.max_gust_kmh == 36


def test_only_next_24_hours_are_counted() -> None:
    p = payload(cape=[0] * 12, conv_m=[0.0] * 12, rain_m=[0.001] * 12)
    study = summarize("icon", p, now=NOW)
    assert study is not None and study.rain_mm == 8.0


@pytest.mark.asyncio
async def test_client_skips_models_that_fail_and_verdict_counts_storms() -> None:
    p = payload(cape=[900], conv_m=[0.003], rain_m=[0.005])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=p) if b'"gfs"' in request.content else httpx.Response(500)

    client = WindyClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "k")
    p["ts"] = [int(datetime.now(UTC).timestamp() * 1000) + 3 * H]
    study = await WindyStudyUseCase(client).execute(Coordinates(latitude=-33.52, longitude=-70.6))
    assert [m.model for m in study.models] == ["gfs"]
    assert study.storm_models == 1
    assert study.verdict.startswith("Todos los modelos")
    assert verdict([]).startswith("Windy no ve")

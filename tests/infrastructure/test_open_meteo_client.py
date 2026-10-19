"""Pruebas del cliente Open-Meteo con transporte HTTP simulado (sin red)."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from src.domain.repositories import RiverDischargeProvider, WeatherForecastProvider
from src.domain.value_objects import Coordinates, WeatherModel
from src.infrastructure.exceptions import ExternalServiceError, WeatherDataUnavailableError
from src.infrastructure.external_apis import OpenMeteoClient

TALCA = Coordinates(latitude=-35.43, longitude=-71.66)
ALL_MODELS = list(WeatherModel)


async def _no_sleep(_: float) -> None:
    return None


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> OpenMeteoClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenMeteoClient(http, max_retries=2, sleep=_no_sleep)


def json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


async def test_from_settings_builds_a_valid_http_client() -> None:
    from src.infrastructure.config import Settings

    client = OpenMeteoClient.from_settings(Settings())
    await client.aclose()


def test_implements_domain_ports() -> None:
    client = make_client(lambda r: json_response({}))
    assert isinstance(client, WeatherForecastProvider)
    assert isinstance(client, RiverDischargeProvider)


async def test_multi_model_precipitation_parsing() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return json_response(
            {
                "hourly": {
                    "time": ["t"] * 4,
                    "precipitation_ecmwf_ifs025": [1.0, 2.0, 3.0, 4.0],
                    "precipitation_icon_seamless": [0.5, None, 0.5, 1.0],  # 75 % cobertura → se omite
                    "precipitation_gfs_seamless": [2.0, 2.0, 2.0, 2.0],
                }
            }
        )

    result = await make_client(handler).get_precipitation_forecasts(TALCA, ALL_MODELS, horizon_hours=4)

    assert seen["models"] == "ecmwf_ifs025,icon_seamless,gfs_seamless"
    assert seen["forecast_hours"] == "4"
    by_model = {f.model: f.accumulated_precipitation_mm for f in result}
    assert by_model == {WeatherModel.ECMWF_IFS025: 10.0, WeatherModel.GFS_SEAMLESS: 8.0}
    assert result[0].hourly_mm == (1.0, 2.0, 3.0, 4.0)


async def test_hydrological_conditions_use_window_maximum() -> None:
    seen: dict[str, str] = {}
    past = [None] * 24

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return json_response(
            {
                "hourly": {
                    # Las 24 horas pasadas van primero; sólo la lluvia se mira hacia atrás.
                    "freezing_level_height": [4000.0] * 24 + [1500.0, 2400.0, None],
                    "soil_moisture_0_to_1cm": [0.9] * 24 + [0.20, 0.31, 0.25],
                    "cape": past + [None, None, None],
                    "precipitation": [1.0] * 24 + [0.0, 12.5, None],
                    # La ráfaga pasada (120) no cuenta: sólo la ventana futura.
                    "wind_gusts_10m": [120.0] * 24 + [30.0, 62.4, None],
                }
            }
        )

    cond = await make_client(handler).get_hydrological_conditions(TALCA, horizon_hours=3)
    assert seen["past_hours"] == "24" and seen["forecast_hours"] == "3"
    assert cond.freezing_level_m == 2400.0
    assert cond.soil_moisture_m3m3 == 0.31
    assert cond.cape_j_kg == 0.0
    assert cond.recent_precipitation_mm == 24.0
    assert cond.max_hourly_precipitation_mm == 12.5
    assert cond.cape_hourly == (0.0, 0.0, 0.0)
    assert cond.max_wind_gust_kmh == 62.4


async def test_missing_soil_moisture_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {"hourly": {"freezing_level_height": [None] * 24 + [3000.0], "soil_moisture_0_to_1cm": [None] * 25}}
        )

    with pytest.raises(WeatherDataUnavailableError):
        await make_client(handler).get_hydrological_conditions(TALCA, horizon_hours=1)


async def test_retries_transient_errors_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return json_response(
            {"daily": {"time": ["2026-07-01", "2026-07-02"], "river_discharge": [120.0, 340.0],
                       "river_discharge_max": [150.0, 410.0]}}
        )

    flood = await make_client(handler).get_river_discharge(TALCA, forecast_days=2)
    assert calls["n"] == 3
    assert flood.peak_discharge_m3s == 410.0
    assert flood.peak_date.isoformat() == "2026-07-02"


async def test_gives_up_after_max_retries() -> None:
    with pytest.raises(ExternalServiceError):
        await make_client(lambda r: httpx.Response(503)).get_river_discharge(TALCA)


async def test_api_error_payload_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return json_response({"error": True, "reason": "Latitude must be in range"}, status=400)

    with pytest.raises(ExternalServiceError, match="Latitude"):
        await make_client(handler).get_precipitation_forecasts(TALCA, ALL_MODELS)
    assert calls["n"] == 1


async def test_ensemble_combines_models_and_skips_failed_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["models"] == "gfs025":
            return httpx.Response(500, json={"error": True, "reason": "caído"})
        return json_response(
            {
                "hourly": {
                    "precipitation": [1.0, 2.0],
                    "precipitation_member01": [0.0, 3.0],
                    "precipitation_member02": [None, None],
                    "cape": [None, None],
                }
            }
        )

    members = await make_client(handler).get_ensemble(TALCA, horizon_hours=2)
    assert [(m.model, m.precipitation, m.cape) for m in members] == [
        ("icon_seamless_eps", (1.0, 2.0), ()),
        ("icon_seamless_eps", (0.0, 3.0), ()),
    ]


async def test_outlooks_one_call_for_many_points() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        row = {"hourly": {"precipitation": [1.0, 4.0], "freezing_level_height": [2000.0, 2500.0],
                          "cape": [0.0, 900.0], "wind_gusts_10m": [10.0, 40.0]}}
        return json_response([row, row])  # type: ignore[arg-type]

    out = await make_client(handler).get_outlooks([TALCA, TALCA], horizon_hours=2)
    assert seen["latitude"].count(",") == 1
    assert [(o.rain_mm, o.max_hourly_mm, o.freezing_level_m, o.cape_j_kg, o.max_wind_gust_kmh) for o in out] == [
        (5.0, 4.0, 2500.0, 900.0, 40.0)
    ] * 2

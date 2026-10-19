"""Probabilidades desde corridas de ensemble."""

from __future__ import annotations

from src.domain.services.probability import HailSize, ProbabilityService
from src.domain.value_objects.forecast import EnsembleMember

DEBRIS = (15.0, 30.0, 50.0)


def member(total_mm: float, *, peak_mm: float = 0.0, cape: float | None = None, model: str = "icon") -> EnsembleMember:
    """24 horas: la lluvia se reparte pareja salvo un pico opcional en la hora 10."""
    rest = max(total_mm - peak_mm, 0.0) / 23 if peak_mm else total_mm / 24
    rain = [rest] * 24
    if peak_mm:
        rain[10] = peak_mm
    capes = () if cape is None else tuple(cape if i == 10 else 0.0 for i in range(24))
    return EnsembleMember(model=model, precipitation=tuple(rain), cape=capes)


def assess(members: list[EnsembleMember], freezing: float | None = 3000.0):  # type: ignore[no-untyped-def]
    return ProbabilityService().assess(
        members, debris_thresholds_mm=DEBRIS, debris_min_freezing_m=2200.0, freezing_level_m=freezing
    )


def test_fraction_of_members_over_each_threshold() -> None:
    members = [member(0.0)] * 5 + [member(20.0)] * 3 + [member(35.0)] + [member(60.0)]
    out = assess(members)
    assert out is not None
    assert out.members == 10
    assert out.rain_pct == 50
    assert out.debris_pct == (50, 20, 10)
    assert out.rain_p90_mm == 35.0


def test_intense_rain_counts_members_with_a_heavy_hour() -> None:
    out = assess([member(12.0, peak_mm=11.0), member(12.0), member(12.0), member(12.0)])
    assert out is not None and out.intense_pct == 25


def test_low_freezing_level_means_no_debris_probability() -> None:
    out = assess([member(80.0)] * 4, freezing=1500.0)
    assert out is not None and out.snow and out.debris_pct == (0, 0, 0)


def test_hail_needs_instability_and_a_shower_in_the_same_hour() -> None:
    members = [
        member(10.0, peak_mm=5.0, cape=2500.0, model="gfs"),
        member(10.0, peak_mm=5.0, cape=2100.0, model="gfs"),
        member(10.0, cape=2500.0, model="gfs"),  # CAPE alto pero sin chubasco esa hora (0,4 mm)
        member(10.0, peak_mm=5.0, cape=300.0, model="gfs"),
        member(10.0),  # ICON: sin CAPE, no cuenta para granizo
    ]
    out = assess(members)
    assert out is not None
    assert (out.hail_pct, out.hail_members, out.hail_size) == (50, 4, HailSize.MEDIANO)


def test_hail_melts_with_very_high_freezing_level() -> None:
    out = assess([member(10.0, peak_mm=5.0, cape=2500.0)], freezing=4800.0)
    assert out is not None and out.hail_pct == 0 and out.hail_size is None


def test_without_cape_hail_is_unknown_and_without_members_nothing() -> None:
    out = assess([member(5.0)])
    assert out is not None and out.hail_pct is None
    assert assess([]) is None


def test_storm_counts_unstable_hours_with_some_rain() -> None:
    members = [
        member(10.0, peak_mm=5.0, cape=900.0, model="gfs"),
        member(10.0, peak_mm=5.0, cape=200.0, model="gfs"),  # bajo el umbral de 300
        member(0.0, cape=1500.0, model="gfs"),  # inestable pero sin nube que llueva
        member(10.0, peak_mm=5.0, cape=1200.0, model="gfs"),
    ]
    out = assess(members)
    assert out is not None and out.storm_pct == 50


def test_storm_unknown_without_cape() -> None:
    out = assess([member(5.0)])
    assert out is not None and out.storm_pct is None

"""Pruebas de los pipelines PySpark. Se omiten si no hay Java disponible."""

import os
import shutil
from datetime import datetime, timedelta

import pytest

pytest.importorskip("pyspark")
if not (shutil.which("java") or os.environ.get("JAVA_HOME")):
    pytest.skip("PySpark requiere Java (JAVA_HOME)", allow_module_level=True)

from pyspark.sql import SparkSession  # noqa: E402

from src.infrastructure.spark.historical_batch import (  # noqa: E402
    HISTORICAL_SCHEMA,
    add_rolling_accumulations,
    extract_critical_thresholds,
    label_flood_onsets,
    resolve_flood_stages,
)
from src.infrastructure.spark.spark_session import build_spark_session  # noqa: E402


@pytest.fixture(scope="module")
def spark() -> SparkSession:
    session = build_spark_session("sat-maule-tests", shuffle_partitions=2, driver_memory="1g")
    yield session  # type: ignore[misc]
    session.stop()


def test_rolling_windows_are_time_based(spark: SparkSession) -> None:
    t0 = datetime(2026, 6, 1)
    rows = [("S1", t0 + timedelta(hours=h), 1.0, 0.5, 0.3) for h in (0, 1, 2, 5, 6, 30)]
    df = add_rolling_accumulations(spark.createDataFrame(rows, HISTORICAL_SCHEMA))
    got = {r["recorded_at"].hour + 24 * (r["recorded_at"].day - 1): r for r in df.collect()}
    assert got[6]["precip_6h"] == 4.0  # horas 1, 2, 5, 6 (la hora 0 queda fuera de (0, 6])
    assert got[30]["precip_24h"] == 2.0  # horas 6 y 30
    assert got[30]["precip_72h"] == 6.0


def test_thresholds_from_flood_onsets(spark: SparkSession) -> None:
    t0 = datetime(2026, 6, 1)
    rows = []
    for event in range(3):
        base = t0 + timedelta(days=5 * event)
        rows += [("S1", base + timedelta(hours=h), 10.0, 1.0, 0.4) for h in range(3)]
        rows += [("S1", base + timedelta(hours=3), 10.0, 5.0, 0.4)]  # crecida
    df = add_rolling_accumulations(spark.createDataFrame(rows, HISTORICAL_SCHEMA))
    stages = spark.createDataFrame([("S1", 4.0)], "station_id string, flood_stage_m double")
    labeled = label_flood_onsets(df, resolve_flood_stages(df, stages))
    result = extract_critical_thresholds(labeled).collect()
    assert len(result) == 1
    assert result[0]["n_events"] == 3
    assert result[0]["p10_precip_6h"] == 40.0

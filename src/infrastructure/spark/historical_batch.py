"""Procesamiento *batch* de series históricas del Maule para calibrar umbrales.

Entrada: lecturas horarias (o sub-horarias) por estación con el esquema
:data:`HISTORICAL_SCHEMA` (CSV o Parquet; p. ej. exportaciones DGA/DMC).

Pipeline (funciones puras ``DataFrame → DataFrame``, testeables por separado):

1. :func:`add_rolling_accumulations` — precipitación acumulada en ventanas
   móviles de 6 h, 24 h y 72 h por estación (``Window.partitionBy`` +
   ``rangeBetween`` sobre segundos, robusto a horas faltantes).
2. :func:`label_flood_onsets` — marca el **inicio** de cada episodio de
   crecida: la altura del río cruza la cota de alerta (entregada por estación
   o, si no existe, el percentil 95 histórico de la propia estación).
3. :func:`extract_critical_thresholds` — por estación, percentiles P10/P50 de
   la lluvia acumulada en los inicios de crecida. El P10 es el umbral
   crítico: el 90 % de las crecidas observadas ocurrió con lluvia mayor.
4. :func:`evaluate_false_alarm_rate` — fracción de horas sin crecida que
   igualmente superan el umbral de 24 h (costo en falsas alarmas).

Uso::

    python -m src.infrastructure.spark.historical_batch \\
        --input data/maule_horario.parquet --output out/umbrales \\
        [--stations data/cotas_alerta.csv]
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from pyspark.sql import Column, DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, StringType, StructField, StructType, TimestampType

from src.infrastructure.config import get_settings
from src.infrastructure.spark.spark_session import spark_from_settings

WINDOWS_HOURS: tuple[int, ...] = (6, 24, 72)
DEFAULT_STAGE_QUANTILE = 0.95
MIN_EVENTS_PER_STATION = 3

HISTORICAL_SCHEMA = StructType(
    [
        StructField("station_id", StringType(), nullable=False),
        StructField("recorded_at", TimestampType(), nullable=False),
        StructField("precipitation_mm", DoubleType(), nullable=True),
        StructField("river_level_m", DoubleType(), nullable=True),
        StructField("soil_moisture_m3m3", DoubleType(), nullable=True),
    ]
)

STATIONS_SCHEMA = StructType(
    [
        StructField("station_id", StringType(), nullable=False),
        StructField("flood_stage_m", DoubleType(), nullable=False),
    ]
)


def precip_col(hours: int) -> str:
    """Nombre de la columna de acumulado para una ventana."""
    return f"precip_{hours}h"


def load_historical(spark: SparkSession, path: str, fmt: str = "parquet") -> DataFrame:
    """Lee el dataset histórico aplicando el esquema explícito (sin inferencia)."""
    reader = spark.read.schema(HISTORICAL_SCHEMA)
    if fmt == "csv":
        reader = reader.option("header", "true").option("timestampFormat", "yyyy-MM-dd HH:mm:ss")
    return reader.format(fmt).load(path).dropDuplicates(["station_id", "recorded_at"])


def add_rolling_accumulations(df: DataFrame, windows_hours: Sequence[int] = WINDOWS_HOURS) -> DataFrame:
    """Agrega ``precip_{h}h`` = lluvia en ``(t − h, t]`` y ``soil_moisture_24h`` (media).

    Se usa ``rangeBetween`` sobre el *epoch* en segundos (no ``rowsBetween``)
    para que una hora faltante no desplace la ventana.
    """
    ts = "_ts"
    out = df.withColumn(ts, F.col("recorded_at").cast("long")).fillna({"precipitation_mm": 0.0})
    by_station = Window.partitionBy("station_id").orderBy(ts)
    for hours in windows_hours:
        window = by_station.rangeBetween(-(hours * 3600 - 1), 0)
        out = out.withColumn(precip_col(hours), F.sum("precipitation_mm").over(window))
    out = out.withColumn(
        "soil_moisture_24h", F.avg("soil_moisture_m3m3").over(by_station.rangeBetween(-(24 * 3600 - 1), 0))
    )
    return out.drop(ts)


def resolve_flood_stages(
    df: DataFrame, stations: DataFrame | None = None, quantile: float = DEFAULT_STAGE_QUANTILE
) -> DataFrame:
    """Cota de alerta por estación: oficial si existe; si no, el percentil ``quantile``."""
    empirical = df.groupBy("station_id").agg(
        F.percentile_approx("river_level_m", quantile, 10_000).alias("empirical_stage_m")
    )
    if stations is None:
        return empirical.select("station_id", F.col("empirical_stage_m").alias("flood_stage_m"))
    return empirical.join(stations, "station_id", "left").select(
        "station_id", F.coalesce("flood_stage_m", "empirical_stage_m").alias("flood_stage_m")
    )


def label_flood_onsets(df: DataFrame, stages: DataFrame) -> DataFrame:
    """Agrega ``in_flood`` (río ≥ cota) y ``flood_onset`` (primera hora de cada episodio)."""
    by_station = Window.partitionBy("station_id").orderBy("recorded_at")
    in_flood: Column = F.coalesce(F.col("river_level_m") >= F.col("flood_stage_m"), F.lit(False))
    return (
        df.join(F.broadcast(stages), "station_id", "left")
        .withColumn("in_flood", in_flood)
        .withColumn(
            "flood_onset",
            F.col("in_flood") & ~F.coalesce(F.lag("in_flood").over(by_station), F.lit(False)),
        )
    )


def extract_critical_thresholds(
    labeled: DataFrame,
    windows_hours: Sequence[int] = WINDOWS_HOURS,
    min_events: int = MIN_EVENTS_PER_STATION,
) -> DataFrame:
    """Percentiles P10/P50 de lluvia acumulada al inicio de las crecidas, por estación.

    Estaciones con menos de ``min_events`` crecidas se excluyen: un umbral
    estimado con 1-2 eventos no es estadísticamente defendible.
    """
    aggs: list[Column] = [F.count(F.lit(1)).alias("n_events"), F.first("flood_stage_m").alias("flood_stage_m")]
    for hours in windows_hours:
        col = precip_col(hours)
        aggs.append(F.percentile_approx(col, 0.10, 10_000).alias(f"p10_{col}"))
        aggs.append(F.percentile_approx(col, 0.50, 10_000).alias(f"p50_{col}"))
    aggs.append(F.avg("soil_moisture_24h").alias("avg_soil_moisture_at_onset"))
    return (
        labeled.where(F.col("flood_onset"))
        .groupBy("station_id")
        .agg(*aggs)
        .where(F.col("n_events") >= min_events)
    )


def evaluate_false_alarm_rate(labeled: DataFrame, thresholds: DataFrame, hours: int = 24) -> DataFrame:
    """Agrega ``false_alarm_rate_{h}h``: horas sin crecida que superan el umbral P10."""
    col, threshold = precip_col(hours), f"p10_{precip_col(hours)}"
    rates = (
        labeled.join(F.broadcast(thresholds.select("station_id", threshold)), "station_id")
        .where(~F.col("in_flood"))
        .groupBy("station_id")
        .agg(F.avg((F.col(col) >= F.col(threshold)).cast("double")).alias(f"false_alarm_rate_{hours}h"))
    )
    return thresholds.join(rates, "station_id", "left")


def run(
    spark: SparkSession,
    input_path: str,
    output_path: str,
    *,
    input_format: str = "parquet",
    stations_path: str | None = None,
) -> DataFrame:
    """Ejecuta el pipeline completo y escribe los umbrales (Parquet + JSON)."""
    history = add_rolling_accumulations(load_historical(spark, input_path, input_format)).cache()
    stations = (
        spark.read.schema(STATIONS_SCHEMA).option("header", "true").csv(stations_path)
        if stations_path
        else None
    )
    labeled = label_flood_onsets(history, resolve_flood_stages(history, stations)).cache()
    thresholds = evaluate_false_alarm_rate(labeled, extract_critical_thresholds(labeled))

    thresholds.coalesce(1).write.mode("overwrite").parquet(f"{output_path}/parquet")
    thresholds.coalesce(1).write.mode("overwrite").json(f"{output_path}/json")
    return thresholds


def main(argv: Sequence[str] | None = None) -> None:
    """Punto de entrada CLI."""
    parser = argparse.ArgumentParser(description="SAT-Maule: umbrales críticos de inundación")
    parser.add_argument("--input", required=True, help="Ruta del dataset histórico")
    parser.add_argument("--format", default="parquet", choices=("parquet", "csv"))
    parser.add_argument("--stations", help="CSV opcional station_id,flood_stage_m (cotas oficiales)")
    parser.add_argument("--output", required=True, help="Directorio de salida")
    args = parser.parse_args(argv)

    spark = spark_from_settings(get_settings(), app_name="sat-maule-historical")
    try:
        result = run(spark, args.input, args.output, input_format=args.format, stations_path=args.stations)
        result.orderBy("station_id").show(truncate=False)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

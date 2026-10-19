"""Structured Streaming: consolidación de telemetría en tiempo real hacia MongoDB.

Flujo::

    fuente (JSON en carpeta | Kafka)
      → watermark 2 h (tolera lecturas atrasadas de estaciones remotas)
      → ventanas deslizantes de 6 h cada 1 h, por estación
      → foreachBatch:
           cruce con umbrales históricos (salida de historical_batch)
           → flags de superación
           → upsert idempotente en ``station_state`` (MongoDB)

El cruce con los umbrales se hace dentro de ``foreachBatch`` (sobre un
DataFrame *batch*), lo que evita las restricciones de joins después de
agregaciones en streaming y permite recargar umbrales sin reiniciar el job.

Garantías: ``foreachBatch`` es *at-least-once*; la clave determinística
``{station_id}|{window_start}`` hace que reprocesar un micro-batch sea
idempotente (upsert sobre el mismo documento).

Uso::

    python -m src.infrastructure.spark.realtime_stream \\
        --source data/stream_in --thresholds out/umbrales/parquet \\
        --checkpoint chk/realtime
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError
from pymongo.write_concern import WriteConcern
from pyspark.sql import DataFrame, Row, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.streaming.query import StreamingQuery
from pyspark.sql.types import DoubleType, StringType, StructField, StructType, TimestampType

from src.infrastructure.config import get_settings
from src.infrastructure.database.mongo_client import Collections
from src.infrastructure.spark.spark_session import spark_from_settings

logger = logging.getLogger(__name__)

WATERMARK = "2 hours"
WINDOW_DURATION = "6 hours"
WINDOW_SLIDE = "1 hour"
KAFKA_PACKAGE = "org.apache.spark:spark-sql-kafka-0-10_2.13:{spark_version}"

TELEMETRY_STREAM_SCHEMA = StructType(
    [
        StructField("station_id", StringType(), nullable=False),
        StructField("recorded_at", TimestampType(), nullable=False),
        StructField("latitude", DoubleType(), nullable=False),
        StructField("longitude", DoubleType(), nullable=False),
        StructField("precipitation_mm", DoubleType(), nullable=True),
        StructField("river_level_m", DoubleType(), nullable=True),
        StructField("soil_moisture_m3m3", DoubleType(), nullable=True),
    ]
)


# ---------------------------------------------------------------- fuentes


def read_json_stream(spark: SparkSession, source_dir: str, max_files_per_trigger: int = 50) -> DataFrame:
    """Fuente de archivos JSON (una lectura por línea) depositados en ``source_dir``."""
    return (
        spark.readStream.schema(TELEMETRY_STREAM_SCHEMA)
        .option("maxFilesPerTrigger", max_files_per_trigger)
        .json(source_dir)
    )


def read_kafka_stream(spark: SparkSession, bootstrap_servers: str, topic: str) -> DataFrame:
    """Fuente Kafka; requiere ``spark.jars.packages`` con :data:`KAFKA_PACKAGE`."""
    raw = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", bootstrap_servers)
        .option("subscribe", topic)
        .option("startingOffsets", "latest")
        .option("failOnDataLoss", "false")
        .load()
    )
    return raw.select(F.from_json(F.col("value").cast("string"), TELEMETRY_STREAM_SCHEMA).alias("t")).select("t.*")


# ---------------------------------------------------------------- transformaciones


def aggregate_windows(stream: DataFrame) -> DataFrame:
    """Agrega por estación y ventana deslizante de 6 h (paso 1 h) con watermark."""
    return (
        stream.where(F.col("station_id").isNotNull() & F.col("recorded_at").isNotNull())
        .withWatermark("recorded_at", WATERMARK)
        .groupBy("station_id", F.window("recorded_at", WINDOW_DURATION, WINDOW_SLIDE).alias("w"))
        .agg(
            F.sum(F.coalesce("precipitation_mm", F.lit(0.0))).alias("precip_6h"),
            F.max("river_level_m").alias("max_river_level_m"),
            F.avg("soil_moisture_m3m3").alias("avg_soil_moisture_m3m3"),
            F.last("latitude", ignorenulls=True).alias("latitude"),
            F.last("longitude", ignorenulls=True).alias("longitude"),
            F.count(F.lit(1)).alias("n_readings"),
            F.max("recorded_at").alias("last_reading_at"),
        )
        .select(
            "station_id",
            F.col("w.start").alias("window_start"),
            F.col("w.end").alias("window_end"),
            "precip_6h",
            "max_river_level_m",
            "avg_soil_moisture_m3m3",
            "latitude",
            "longitude",
            "n_readings",
            "last_reading_at",
        )
    )


def cross_with_thresholds(windows: DataFrame, thresholds: DataFrame) -> DataFrame:
    """Cruza ventanas agregadas con umbrales históricos y calcula flags.

    Las estaciones sin umbral calibrado conservan sus métricas con flags nulos
    (no se infiere superación sin evidencia histórica).
    """
    reference = thresholds.select("station_id", "flood_stage_m", "p10_precip_6h", "p50_precip_6h")
    return (
        windows.join(F.broadcast(reference), "station_id", "left")
        .withColumn("exceeds_p10_6h", F.col("precip_6h") >= F.col("p10_precip_6h"))
        .withColumn("exceeds_p50_6h", F.col("precip_6h") >= F.col("p50_precip_6h"))
        .withColumn("river_above_stage", F.col("max_river_level_m") >= F.col("flood_stage_m"))
    )


# ---------------------------------------------------------------- sink MongoDB


def row_to_update(row: Row, now: datetime) -> UpdateOne:
    """Convierte una fila consolidada en un upsert idempotente."""
    data: dict[str, Any] = row.asDict(recursive=True)
    window_start: datetime = data["window_start"]
    doc_id = f"{data['station_id']}|{window_start.astimezone(UTC).isoformat()}"
    lat, lon = data.pop("latitude"), data.pop("longitude")
    if lat is not None and lon is not None:
        data["location"] = {"type": "Point", "coordinates": [lon, lat]}
    data["updated_at"] = now
    return UpdateOne({"_id": doc_id}, {"$set": data}, upsert=True)


class MongoStateSink:
    """Callable para ``foreachBatch`` que persiste el estado consolidado.

    Se ejecuta en el *driver*: usa PyMongo síncrono (Motor es para asyncio) y
    escrituras ``w=1`` no ordenadas, coherentes con el perfil AP.
    """

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        thresholds_loader: Callable[[], DataFrame],
        *,
        client_factory: Callable[[str], MongoClient[dict[str, Any]]] | None = None,
    ) -> None:
        self._uri = mongo_uri
        self._db_name = db_name
        self._thresholds_loader = thresholds_loader
        self._client_factory = client_factory or _default_client
        self._client: MongoClient[dict[str, Any]] | None = None

    def __call__(self, batch: DataFrame, batch_id: int) -> None:
        rows = cross_with_thresholds(batch, self._thresholds_loader()).collect()
        if not rows:
            return
        now = datetime.now(UTC)
        operations = [row_to_update(r, now) for r in rows]
        collection = self._collection()
        try:
            result = collection.bulk_write(operations, ordered=False)
            logger.info(
                "batch %d: %d upserts, %d actualizados", batch_id, result.upserted_count, result.modified_count
            )
        except BulkWriteError as exc:
            logger.warning("batch %d: escritura parcial (%d errores)", batch_id, len(exc.details.get("writeErrors", [])))

    def _collection(self) -> Any:
        if self._client is None:
            self._client = self._client_factory(self._uri)
        return self._client[self._db_name].get_collection(
            Collections.STATION_STATE.value, write_concern=WriteConcern(w=1)
        )

    def close(self) -> None:
        """Cierra la conexión Mongo."""
        if self._client is not None:
            self._client.close()
            self._client = None


def _default_client(uri: str) -> MongoClient[dict[str, Any]]:
    return MongoClient(uri, tz_aware=True, retryWrites=True, w=1, appname="sat-maule-stream")


def start_stream(
    spark: SparkSession,
    stream: DataFrame,
    sink: Callable[[DataFrame, int], None],
    checkpoint_dir: str,
    trigger_seconds: int = 60,
) -> StreamingQuery:
    """Arranca la consulta en modo ``update`` (sólo ventanas que cambiaron)."""
    return (
        aggregate_windows(stream)
        .writeStream.outputMode("update")
        .foreachBatch(sink)
        .option("checkpointLocation", checkpoint_dir)
        .trigger(processingTime=f"{trigger_seconds} seconds")
        .queryName("sat_maule_station_state")
        .start()
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Punto de entrada CLI."""
    parser = argparse.ArgumentParser(description="SAT-Maule: consolidación de telemetría en streaming")
    parser.add_argument("--source", required=True, help="Carpeta JSON o 'kafka://host:port/topic'")
    parser.add_argument("--thresholds", required=True, help="Parquet de umbrales (historical_batch)")
    parser.add_argument("--checkpoint", required=True, help="Directorio de checkpoints")
    parser.add_argument("--trigger-seconds", type=int, default=60)
    args = parser.parse_args(argv)

    settings = get_settings()
    extra: dict[str, str] = {}
    is_kafka = args.source.startswith("kafka://")
    if is_kafka:
        from pyspark import __version__ as spark_version

        extra["spark.jars.packages"] = KAFKA_PACKAGE.format(spark_version=spark_version)

    spark = spark_from_settings(settings, app_name="sat-maule-stream", extra_conf=extra)
    if is_kafka:
        host_port, _, topic = args.source.removeprefix("kafka://").partition("/")
        stream = read_kafka_stream(spark, host_port, topic)
    else:
        stream = read_json_stream(spark, args.source)

    sink = MongoStateSink(
        settings.mongo_uri.get_secret_value(),
        settings.mongo_db,
        thresholds_loader=lambda: spark.read.parquet(args.thresholds),
    )
    query = start_stream(spark, stream, sink, args.checkpoint, args.trigger_seconds)
    try:
        query.awaitTermination()
    finally:
        sink.close()
        spark.stop()


if __name__ == "__main__":
    main()

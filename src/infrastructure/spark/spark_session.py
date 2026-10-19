"""Fábrica de ``SparkSession`` optimizada para ejecución local.

Requisitos: Java 17+ (``JAVA_HOME``). En Windows, para escribir en disco
Spark necesita además ``winutils.exe`` en ``%HADOOP_HOME%\\bin``.
"""

from __future__ import annotations

from collections.abc import Mapping

from pyspark.sql import SparkSession

from src.infrastructure.config import Settings

LOCAL_DEFAULTS: Mapping[str, str] = {
    # Adaptive Query Execution: reajusta particiones y joins en tiempo de ejecución.
    "spark.sql.adaptive.enabled": "true",
    "spark.sql.adaptive.coalescePartitions.enabled": "true",
    "spark.sql.adaptive.skewJoin.enabled": "true",
    # Todas las marcas de tiempo se interpretan en UTC (la telemetría llega en UTC).
    "spark.sql.session.timeZone": "UTC",
    # Conversión rápida Spark ⇄ pandas.
    "spark.sql.execution.arrow.pyspark.enabled": "true",
    "spark.serializer": "org.apache.spark.serializer.KryoSerializer",
    # Broadcast de tablas pequeñas (umbrales por estación, zonas) hasta 32 MB.
    "spark.sql.autoBroadcastJoinThreshold": str(32 * 1024 * 1024),
    "spark.sql.files.maxPartitionBytes": str(64 * 1024 * 1024),
    "spark.ui.showConsoleProgress": "false",
    # Streaming: retiene metadatos de pocos micro-batches para ahorrar disco.
    "spark.sql.streaming.minBatchesToRetain": "20",
}


def build_spark_session(
    app_name: str = "sat-maule",
    *,
    master: str = "local[*]",
    driver_memory: str = "2g",
    shuffle_partitions: int = 8,
    extra_conf: Mapping[str, str] | None = None,
) -> SparkSession:
    """Crea (o reutiliza) una ``SparkSession`` local.

    Args:
        app_name: Nombre visible en la UI de Spark.
        master: ``local[*]`` usa todos los núcleos; en clúster, la URL del master.
        driver_memory: Memoria del driver (en local, es toda la memoria de Spark).
        shuffle_partitions: Particiones de *shuffle*. El valor por defecto de
            Spark (200) es excesivo para datasets regionales en una máquina.
        extra_conf: Configuración adicional (p. ej. ``spark.jars.packages`` para Kafka).
    """
    builder = (
        SparkSession.builder.appName(app_name)
        .master(master)
        .config("spark.driver.memory", driver_memory)
        .config("spark.sql.shuffle.partitions", str(shuffle_partitions))
    )
    for key, value in {**LOCAL_DEFAULTS, **(extra_conf or {})}.items():
        builder = builder.config(key, value)
    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    return spark


def spark_from_settings(
    settings: Settings, app_name: str = "sat-maule", extra_conf: Mapping[str, str] | None = None
) -> SparkSession:
    """Atajo que toma los parámetros de :class:`Settings`."""
    return build_spark_session(
        app_name,
        master=settings.spark_master,
        driver_memory=settings.spark_driver_memory,
        shuffle_partitions=settings.spark_shuffle_partitions,
        extra_conf=extra_conf,
    )

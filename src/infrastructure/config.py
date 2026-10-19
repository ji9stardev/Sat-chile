"""Configuración de infraestructura leída de variables de entorno / ``.env``.

Todas las variables usan el prefijo ``SAT_`` (p. ej. ``SAT_MONGO_URI``).
Los secretos se tipan como ``SecretStr`` para que nunca aparezcan en logs.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Parámetros de conexión y operación."""

    model_config = SettingsConfigDict(env_prefix="SAT_", env_file=".env", extra="ignore")

    # --- MongoDB Atlas
    mongo_uri: SecretStr = SecretStr("mongodb://localhost:27017")
    mongo_db: str = "sat_maule"
    mongo_server_selection_timeout_ms: int = Field(default=5000, gt=0)
    mongo_max_pool_size: int = Field(default=50, gt=0)
    telemetry_ttl_days: int = Field(default=90, gt=0)
    station_state_ttl_days: int = Field(default=7, gt=0)

    # --- Open-Meteo (gratuito para uso no comercial, sin API key)
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_flood_url: str = "https://flood-api.open-meteo.com/v1/flood"
    open_meteo_ensemble_url: str = "https://ensemble-api.open-meteo.com/v1/ensemble"
    open_meteo_elevation_url: str = "https://api.open-meteo.com/v1/elevation"
    # --- Windy Point Forecast API (clave en api.windy.com/keys; sin clave el estudio numérico se desactiva)
    windy_point_api_key: SecretStr | None = None
    http_timeout_s: float = Field(default=10.0, gt=0)
    http_max_retries: int = Field(default=3, ge=0)
    weather_cache_ttl_s: int = Field(default=900, ge=0)
    """Caché de pronósticos por celda de ~1 km: protege la cuota gratuita en emergencias."""

    # --- API
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    reporter_hash_secret: SecretStr = SecretStr("cambiar-en-produccion")
    """Clave HMAC para anonimizar el identificador de dispositivo de los reportes."""
    notification_log_ttl_days: int = Field(default=7, gt=0)

    # --- Web Push (VAPID): generar con `python -m src.infrastructure.notifications.generate_vapid`
    vapid_private_key: SecretStr | None = None
    vapid_public_key: str | None = None
    """Clave pública (base64url, punto P-256 sin comprimir) que usa el navegador."""
    vapid_subject: str = "mailto:alertas@sat-maule.cl"
    push_max_concurrency: int = Field(default=20, gt=0)

    # --- Monitor de zonas
    monitor_interval_min: int = Field(default=30, ge=0)
    """Cada cuántos minutos evaluar todas las zonas dentro del proceso (0 = desactivado)."""
    cron_token: SecretStr | None = None
    """Token para POST /api/internal/monitor (cron externo). Sin token, el endpoint queda deshabilitado."""

    # --- Spark
    spark_master: str = "local[*]"
    spark_driver_memory: str = "2g"
    spark_shuffle_partitions: int = Field(default=8, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instancia única (cacheada) de la configuración."""
    return Settings()

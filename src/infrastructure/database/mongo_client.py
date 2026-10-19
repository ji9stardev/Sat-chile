"""Conexión asíncrona a MongoDB Atlas con perfil AP (Disponibilidad + Tolerancia a particiones).

Decisiones respecto al Teorema CAP
----------------------------------
Durante una emergencia es preferible responder con un dato levemente
desactualizado que no responder. Por eso:

* **Escrituras ``w=1``** (sólo el primario confirma). Con ``w="majority"`` una
  partición que aísle a los secundarios bloquearía la emisión de alertas.
  Las alertas usan además ``j=True`` (journal) para no perderse ante un
  reinicio del primario.
* **Lecturas desde secundarios** cuando el primario no está disponible
  (``primaryPreferred``) o directamente desde el nodo más cercano
  (``nearest``) para datos de referencia que cambian poco.
* **retryWrites/retryReads** y reintentos con *backoff* ante errores
  transitorios (elecciones de primario, cortes de red).
* Escrituras masivas con ``ordered=False``: un documento inválido no
  detiene la ingesta del resto.

El costo aceptado es consistencia eventual: un lector podría no ver una
escritura de hace pocos segundos.

.. note::
   MongoDB anunció la deprecación de Motor en favor de la API asíncrona de
   PyMongo (``pymongo.AsyncMongoClient``). Todo el acceso a Motor queda
   encapsulado en este módulo y en los repositorios, así que migrar
   consiste en cambiar la clase del cliente.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, TypeVar

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, GEOSPHERE, IndexModel
from pymongo.errors import AutoReconnect, NetworkTimeout, OperationFailure, ServerSelectionTimeoutError
from pymongo.read_preferences import Nearest, PrimaryPreferred, SecondaryPreferred, _ServerMode
from pymongo.write_concern import WriteConcern

from src.infrastructure.config import Settings
from src.infrastructure.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)

T = TypeVar("T")

INDEX_OPTIONS_CONFLICT_CODES = frozenset({85, 86})


class Collections(StrEnum):
    """Nombres de colecciones."""

    ALERTS = "alerts"
    TELEMETRY = "telemetry"
    GEO_ZONES = "geo_zones"
    CITIZEN_REPORTS = "citizen_reports"
    SAFE_POINTS = "safe_points"
    SUBSCRIBERS = "subscribers"
    STATION_STATE = "station_state"
    WATERCOURSES = "watercourses"
    NOTIFICATION_LOG = "notification_log"


class ConsistencyProfile(StrEnum):
    """Perfiles de consistencia por tipo de dato."""

    CRITICAL = "CRITICAL"
    """Alertas y reportes: ``w=1, j=True`` + ``primaryPreferred``."""
    TELEMETRY = "TELEMETRY"
    """Alto volumen: ``w=1, j=False`` + ``secondaryPreferred``."""
    REFERENCE = "REFERENCE"
    """Zonas, puntos seguros, suscriptores: ``w=1`` + ``nearest`` (≤ 120 s de desfase)."""


_PROFILES: dict[ConsistencyProfile, tuple[_ServerMode, WriteConcern]] = {
    ConsistencyProfile.CRITICAL: (PrimaryPreferred(), WriteConcern(w=1, j=True)),
    ConsistencyProfile.TELEMETRY: (SecondaryPreferred(), WriteConcern(w=1, j=False)),
    ConsistencyProfile.REFERENCE: (Nearest(max_staleness=120), WriteConcern(w=1)),
}

_COLLECTION_PROFILE: dict[Collections, ConsistencyProfile] = {
    Collections.ALERTS: ConsistencyProfile.CRITICAL,
    Collections.CITIZEN_REPORTS: ConsistencyProfile.CRITICAL,
    # El cooldown lee lo último enviado: leer de un secundario atrasado
    # podría duplicar notificaciones, por eso va con primaryPreferred.
    Collections.NOTIFICATION_LOG: ConsistencyProfile.CRITICAL,
    Collections.WATERCOURSES: ConsistencyProfile.REFERENCE,
    Collections.TELEMETRY: ConsistencyProfile.TELEMETRY,
    Collections.STATION_STATE: ConsistencyProfile.TELEMETRY,
    Collections.GEO_ZONES: ConsistencyProfile.REFERENCE,
    Collections.SAFE_POINTS: ConsistencyProfile.REFERENCE,
    Collections.SUBSCRIBERS: ConsistencyProfile.REFERENCE,
}


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    attempts: int = 3,
    base_delay_s: float = 0.2,
) -> T:
    """Ejecuta ``operation`` reintentando ante errores de red transitorios.

    Complementa a ``retryWrites``/``retryReads`` del driver (que reintentan
    una sola vez) con *backoff* exponencial, útil durante elecciones de
    primario que tardan algunos segundos.

    ``ServerSelectionTimeoutError`` **no** se reintenta: ya esperó el timeout
    completo de selección de servidor y repetirlo sólo multiplicaría la
    latencia cuando el clúster está inalcanzable.

    Raises:
        DatabaseUnavailableError: Si MongoDB no responde. Es un
            :class:`ExternalDataUnavailableError`, de modo que la aplicación
            puede degradarse y la API responder 503 sin conocer PyMongo.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except ServerSelectionTimeoutError as exc:
            raise DatabaseUnavailableError("MongoDB no está disponible.") from exc
        except (AutoReconnect, NetworkTimeout) as exc:
            if attempt == attempts:
                raise DatabaseUnavailableError("MongoDB no respondió tras reintentos.") from exc
            delay = base_delay_s * 2 ** (attempt - 1)
            logger.warning("MongoDB transitorio (%s); reintento %d en %.1fs", exc, attempt, delay)
            await asyncio.sleep(delay)
    raise AssertionError("inalcanzable")  # pragma: no cover


class MongoConnection:
    """Dueño único del cliente Motor; entrega colecciones ya configuradas.

    Uso típico (en el arranque de la aplicación)::

        conn = MongoConnection.from_settings(get_settings())
        await conn.ping()
        await conn.ensure_indexes()
        ...
        conn.close()
    """

    def __init__(
        self,
        uri: str,
        db_name: str,
        *,
        server_selection_timeout_ms: int = 5000,
        max_pool_size: int = 50,
        telemetry_ttl_days: int = 90,
        station_state_ttl_days: int = 7,
        notification_log_ttl_days: int = 7,
    ) -> None:
        self._client: AsyncIOMotorClient[dict[str, Any]] = AsyncIOMotorClient(
            uri,
            appname="sat-maule",
            tz_aware=True,
            uuidRepresentation="standard",
            retryWrites=True,
            retryReads=True,
            w=1,
            readPreference="primaryPreferred",
            serverSelectionTimeoutMS=server_selection_timeout_ms,
            connectTimeoutMS=server_selection_timeout_ms,
            maxPoolSize=max_pool_size,
        )
        self._db: AsyncIOMotorDatabase[dict[str, Any]] = self._client[db_name]
        self._telemetry_ttl_s = telemetry_ttl_days * 86_400
        self._station_state_ttl_s = station_state_ttl_days * 86_400
        self._notification_log_ttl_s = notification_log_ttl_days * 86_400

    @classmethod
    def from_settings(cls, settings: Settings) -> MongoConnection:
        """Construye la conexión desde :class:`Settings`."""
        return cls(
            settings.mongo_uri.get_secret_value(),
            settings.mongo_db,
            server_selection_timeout_ms=settings.mongo_server_selection_timeout_ms,
            max_pool_size=settings.mongo_max_pool_size,
            telemetry_ttl_days=settings.telemetry_ttl_days,
            station_state_ttl_days=settings.station_state_ttl_days,
            notification_log_ttl_days=settings.notification_log_ttl_days,
        )

    @property
    def database(self) -> AsyncIOMotorDatabase[dict[str, Any]]:
        """Base de datos subyacente."""
        return self._db

    def collection(self, name: Collections) -> AsyncIOMotorCollection[dict[str, Any]]:
        """Colección con el perfil de consistencia que le corresponde."""
        read_pref, write_concern = _PROFILES[_COLLECTION_PROFILE[name]]
        return self._db.get_collection(name.value, read_preference=read_pref, write_concern=write_concern)

    async def ping(self) -> bool:
        """``True`` si el clúster responde (usar en *health checks*)."""
        try:
            await self._client.admin.command("ping")
            return True
        except Exception as exc:  # noqa: BLE001 - un health check nunca debe propagar
            logger.warning("MongoDB no responde al ping: %s", type(exc).__name__)
            return False

    async def ensure_indexes(self) -> None:
        """Crea (idempotentemente) todos los índices requeridos.

        * ``2dsphere`` en campos GeoJSON para ``$nearSphere`` / ``$geoIntersects``.
        * TTL en ``telemetry.recorded_at`` y ``station_state.updated_at`` para
          expirar datos antiguos sin procesos de limpieza manuales.
        """
        specs: dict[Collections, list[IndexModel]] = {
            Collections.TELEMETRY: [
                IndexModel([("location", GEOSPHERE)], name="location_2dsphere"),
                IndexModel([("station_id", ASCENDING), ("recorded_at", DESCENDING)], name="station_time"),
            ],
            Collections.ALERTS: [
                IndexModel(
                    [("zone_id", ASCENDING), ("status", ASCENDING), ("valid_until", DESCENDING)],
                    name="zone_status_validity",
                ),
                IndexModel([("status", ASCENDING), ("risk_severity", DESCENDING)], name="status_severity"),
            ],
            Collections.GEO_ZONES: [
                IndexModel([("boundary", GEOSPHERE)], name="boundary_2dsphere"),
                IndexModel([("province", ASCENDING)], name="province"),
            ],
            Collections.CITIZEN_REPORTS: [
                IndexModel([("location", GEOSPHERE)], name="location_2dsphere"),
                IndexModel([("status", ASCENDING), ("reported_at", DESCENDING)], name="status_time"),
                IndexModel([("reporter_hash", ASCENDING), ("reported_at", DESCENDING)], name="reporter_time"),
            ],
            Collections.WATERCOURSES: [
                IndexModel([("geometry", GEOSPHERE)], name="geometry_2dsphere"),
                IndexModel([("kind", ASCENDING)], name="kind"),
            ],
            Collections.NOTIFICATION_LOG: [
                IndexModel([("zone_id", ASCENDING)], name="zone"),
            ],
            Collections.SAFE_POINTS: [
                IndexModel([("location", GEOSPHERE)], name="location_2dsphere"),
                IndexModel([("commune", ASCENDING)], name="commune"),
            ],
            Collections.SUBSCRIBERS: [
                IndexModel([("location", GEOSPHERE)], name="location_2dsphere", sparse=True),
                IndexModel([("zone_ids", ASCENDING), ("active", ASCENDING)], name="zones_active"),
                IndexModel([("subscription.endpoint", ASCENDING)], name="endpoint", unique=True),
            ],
            Collections.STATION_STATE: [
                IndexModel([("location", GEOSPHERE)], name="location_2dsphere"),
                IndexModel([("station_id", ASCENDING), ("window_end", DESCENDING)], name="station_window"),
            ],
        }
        for name, indexes in specs.items():
            await self.collection(name).create_indexes(indexes)

        await self._ensure_ttl(Collections.TELEMETRY, "recorded_at", self._telemetry_ttl_s)
        await self._ensure_ttl(Collections.STATION_STATE, "updated_at", self._station_state_ttl_s)
        await self._ensure_ttl(Collections.NOTIFICATION_LOG, "sent_at", self._notification_log_ttl_s)

    async def _ensure_ttl(self, name: Collections, field: str, seconds: int) -> None:
        """Crea el índice TTL o, si ya existe con otro plazo, lo actualiza vía ``collMod``."""
        index_name = f"{field}_ttl"
        try:
            await self.collection(name).create_index(
                [(field, ASCENDING)], name=index_name, expireAfterSeconds=seconds
            )
        except OperationFailure as exc:
            if exc.code not in INDEX_OPTIONS_CONFLICT_CODES:
                raise
            await self._db.command(
                "collMod", name.value, index={"name": index_name, "expireAfterSeconds": seconds}
            )

    def close(self) -> None:
        """Cierra el pool de conexiones."""
        self._client.close()

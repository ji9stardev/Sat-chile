"""Datos semilla: zonas piloto del Maule y sus cauces principales.

⚠ GEOMETRÍAS REFERENCIALES. Los polígonos son cuadrados de ~13 km alrededor
de cada localidad y los cauces son trazos simplificados de 2-3 vértices.
Sirven para desarrollo y demostración; **no** deben usarse para decisiones
reales. En producción reemplazarlos por:

* Cauces: red hidrográfica de la DGA / Banco Nacional de Aguas, o IDE Chile.
* Zonas: polígonos de áreas inundables de SENAPRED / planes reguladores.
* Puntos seguros: catastro oficial municipal / SENAPRED (aquí sólo [DEMO]).

Ejecutar (idempotente, IDs determinísticos)::

    python -m src.infrastructure.database.seed
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from src.domain.entities import GeoZone, MauleProvince, SafePoint, Watercourse, WatercourseKind
from src.domain.value_objects import Coordinates, GeoLineString, GeoPolygon, ThreatType
from src.infrastructure.config import get_settings
from src.infrastructure.database.mongo_client import Collections, MongoConnection
from src.infrastructure.external_apis.overpass_client import load_feature_collection
from src.infrastructure.geodata import load_chile_waterways, waterways_file
from src.infrastructure.database.repositories import (
    MongoGeoZoneRepository,
    MongoSafePointRepository,
    MongoWatercourseRepository,
)

logger = logging.getLogger(__name__)

_NS = uuid5(NAMESPACE_URL, "https://sat-maule.cl/seed")
HYDRO = frozenset({ThreatType.DESBORDE_RIO, ThreatType.INUNDACION, ThreatType.REMOCION_EN_MASA})


def seed_id(kind: str, name: str) -> UUID:
    """UUID estable para que re-ejecutar la semilla actualice en vez de duplicar."""
    return uuid5(_NS, f"{kind}:{name}")


def square(lat: float, lon: float, half_lat: float = 0.06, half_lon: float = 0.07) -> GeoPolygon:
    """Polígono cuadrado referencial centrado en (lat, lon)."""
    return GeoPolygon(
        ring=(
            Coordinates(latitude=lat - half_lat, longitude=lon - half_lon),
            Coordinates(latitude=lat - half_lat, longitude=lon + half_lon),
            Coordinates(latitude=lat + half_lat, longitude=lon + half_lon),
            Coordinates(latitude=lat + half_lat, longitude=lon - half_lon),
        )
    )


def line(*points: tuple[float, float]) -> GeoLineString:
    """Polilínea desde pares (lat, lon)."""
    return GeoLineString(vertices=tuple(Coordinates(latitude=a, longitude=o) for a, o in points))


# (nombre, comuna, provincia, lat, lon, [(cauce, tipo, [(lat, lon), ...])])
PILOT_ZONES: tuple[tuple[str, str, MauleProvince, float, float, list[tuple[str, WatercourseKind, list[tuple[float, float]]]]], ...] = (
    (
        "Vilches", "San Clemente", MauleProvince.TALCA, -35.600, -71.070,
        [("Río Lircay (tramo Vilches)", WatercourseKind.RIO, [(-35.585, -70.990), (-35.600, -71.070), (-35.585, -71.140)])],
    ),
    (
        "Talca", "Talca", MauleProvince.TALCA, -35.426, -71.655,
        [
            ("Río Claro (tramo Talca)", WatercourseKind.RIO, [(-35.360, -71.700), (-35.430, -71.705), (-35.490, -71.715)]),
            ("Estero Piduco", WatercourseKind.ESTERO, [(-35.445, -71.580), (-35.440, -71.640), (-35.435, -71.700)]),
        ],
    ),
    (
        "Cumpeo", "Río Claro", MauleProvince.TALCA, -35.272, -71.267,
        [("Río Claro (tramo Cumpeo)", WatercourseKind.RIO, [(-35.230, -71.220), (-35.280, -71.270), (-35.320, -71.310)])],
    ),
    (
        "Achibueno", "Linares", MauleProvince.LINARES, -35.980, -71.380,
        [("Río Achibueno", WatercourseKind.RIO, [(-36.050, -71.300), (-35.985, -71.375), (-35.920, -71.450)])],
    ),
    (
        "Licantén", "Licantén", MauleProvince.CURICO, -34.985, -72.000,
        [("Río Mataquito (tramo Licantén)", WatercourseKind.RIO, [(-34.975, -71.930), (-34.979, -72.000), (-34.990, -72.080)])],
    ),
)


def build_seed() -> tuple[list[GeoZone], list[Watercourse], list[SafePoint]]:
    """Construye las entidades semilla (sin I/O)."""
    zones: list[GeoZone] = []
    courses: list[Watercourse] = []
    points: list[SafePoint] = []
    for name, commune, province, lat, lon, rivers in PILOT_ZONES:
        zone = GeoZone(
            id=seed_id("zone", name),
            name=name,
            commune=commune,
            province=province,
            boundary=square(lat, lon),
            exposed_threats=HYDRO,
        )
        zones.append(zone)
        for river_name, kind, vertices in rivers:
            courses.append(
                Watercourse(
                    id=seed_id("watercourse", river_name),
                    name=river_name,
                    kind=kind,
                    commune=commune,
                    geometry=line(*vertices),
                    zone_id=zone.id,
                    geometry_source="referencial",
                )
            )
        # Punto seguro DEMO ~2,5 km al norte del centro (no es un lugar oficial).
        points.append(
            SafePoint(
                id=seed_id("safe_point", name),
                name=f"[DEMO] Punto de encuentro {name}",
                commune=commune,
                location=Coordinates(latitude=lat + 0.022, longitude=lon),
                capacity=300,
                safe_from=HYDRO,
            )
        )
    return zones, courses, points


OSM_WATERWAYS = Path(__file__).resolve().parents[3] / "data" / "maule_waterways.geojson"


def load_watercourses(referential: list[Watercourse]) -> list[Watercourse]:
    """Red hidrográfica real de OpenStreetMap si fue importada; si no, los trazados referenciales.

    Prioridad: todo Chile (``python scripts/import_chile.py``), luego sólo el
    Maule (``python scripts/import_osm_waterways.py``).
    """
    chile = load_chile_waterways()
    if chile:
        if not waterways_file("CL-ML").exists() and OSM_WATERWAYS.exists():
            chile += load_feature_collection(OSM_WATERWAYS)  # importación nacional aún sin el Maule
        logger.info("Cauces: %d tramos de OpenStreetMap (Chile)", len(chile))
        return chile
    if OSM_WATERWAYS.exists():
        courses = load_feature_collection(OSM_WATERWAYS)
        logger.info("Cauces: %d tramos de OpenStreetMap (%s)", len(courses), OSM_WATERWAYS.name)
        return courses
    logger.warning("Sin %s: se usan trazados referenciales", OSM_WATERWAYS.name)
    return referential


async def seed(connection: MongoConnection) -> None:
    """Inserta/actualiza las entidades semilla."""
    zones, referential_courses, points = build_seed()
    courses = load_watercourses(referential_courses)
    zone_repo = MongoGeoZoneRepository(connection.collection(Collections.GEO_ZONES))
    course_repo = MongoWatercourseRepository(connection.collection(Collections.WATERCOURSES))
    point_repo = MongoSafePointRepository(connection.collection(Collections.SAFE_POINTS))
    for zone in zones:
        await zone_repo.save(zone)
    await course_repo.upsert_many(courses)
    for point in points:
        await point_repo.save(point)
    logger.info("Semilla: %d zonas, %d cauces, %d puntos DEMO", len(zones), len(courses), len(points))


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    connection = MongoConnection.from_settings(get_settings())
    try:
        await connection.ensure_indexes()
        await seed(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    asyncio.run(_main())

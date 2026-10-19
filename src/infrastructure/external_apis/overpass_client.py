"""Importación de la red hidrográfica y de las comunas de Chile desde OpenStreetMap (Overpass API).

OpenStreetMap es la fuente abierta más completa de cauces menores (esteros,
quebradas, canales de riego y acequias). Los datos se publican bajo licencia
ODbL: hay que citar «© OpenStreetMap contributors».

La importación se hace **una vez** (no en cada consulta de usuario): la
política de uso de Overpass no admite tráfico de producción. El resultado se
guarda como GeoJSON y se carga en la base de datos con la semilla.

Clasificación:

* El nombre manda: "Quebrada …" → QUEBRADA, "Estero …" → ESTERO,
  "Canal …"/"Acequia …" → CANAL, "Río …" → RIO.
* Sin nombre reconocible, la etiqueta OSM: river → RIO, canal/ditch → CANAL,
  stream → ESTERO.

Para todo Chile se descarga **región por región** (código ISO 3166-2, p. ej.
``CL-ML``) y los trazados se simplifican (Douglas-Peucker) para que la red
completa quepa en la base de datos. Las comunas (``admin_level=8``) llegan
como relaciones de tramos sueltos que hay que unir en anillos cerrados.
"""

from __future__ import annotations


import json
import re
import logging
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx

from src.domain.entities.watercourse import Watercourse, WatercourseKind
from src.domain.value_objects.admin_area import AdminArea
from src.domain.value_objects.locality import Locality, PlaceKind
from src.domain.value_objects.emergency_service import EmergencyService, ServiceKind
from src.domain.value_objects.coordinates import Coordinates, GeoLineString, GeoPolygon, simplify_vertices
from src.infrastructure.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
MAULE_AREA = 'area["boundary"="administrative"]["admin_level"="4"]["name"~"Maule"]->.a;'
WATERWAY_TAGS = ("river", "stream", "canal", "ditch")
SOURCE = "OpenStreetMap (ODbL)"
_OSM_NS = uuid5(NAMESPACE_URL, "https://www.openstreetmap.org/way")

_NAME_PREFIX_KIND: tuple[tuple[str, WatercourseKind], ...] = (
    ("quebrada", WatercourseKind.QUEBRADA),
    ("estero", WatercourseKind.ESTERO),
    ("canal", WatercourseKind.CANAL),
    ("acequia", WatercourseKind.CANAL),
    ("río", WatercourseKind.RIO),
    ("rio", WatercourseKind.RIO),
)
_TAG_KIND: Mapping[str, WatercourseKind] = {
    "river": WatercourseKind.RIO,
    "canal": WatercourseKind.CANAL,
    "ditch": WatercourseKind.CANAL,
    "stream": WatercourseKind.ESTERO,
}
_UNNAMED: Mapping[WatercourseKind, str] = {
    WatercourseKind.RIO: "Río sin nombre",
    WatercourseKind.ESTERO: "Estero sin nombre",
    WatercourseKind.QUEBRADA: "Quebrada sin nombre",
    WatercourseKind.CANAL: "Canal sin nombre",
}


def region_area(iso_code: str) -> str:
    """Selector Overpass de una región de Chile por su código ISO 3166-2."""
    return f'area["ISO3166-2"="{iso_code}"]->.a;'


def build_query(tags: Sequence[str] = WATERWAY_TAGS, timeout_s: int = 300, area: str = MAULE_AREA) -> str:
    """Consulta Overpass QL: cauces dentro del área (por defecto, el Maule) con su geometría."""
    selector = "|".join(tags)
    return f'[out:json][timeout:{timeout_s}];{area}(way["waterway"~"^({selector})$"](area.a););out tags geom;'


def classify(name: str | None, waterway: str) -> WatercourseKind:
    """Tipo de cauce según el nombre (prioritario) o la etiqueta OSM."""
    if name:
        lowered = name.strip().lower()
        for prefix, kind in _NAME_PREFIX_KIND:
            if lowered.startswith(prefix):
                return kind
    return _TAG_KIND.get(waterway, WatercourseKind.ESTERO)


def elements_to_watercourses(
    elements: Iterable[Mapping[str, Any]], *, commune: str = "Maule", tolerance_m: float = 0.0
) -> list[Watercourse]:
    """Convierte elementos ``way`` de Overpass (``out geom``) en entidades.

    Args:
        commune: Texto guardado en ``Watercourse.commune`` (la región, en la importación nacional).
        tolerance_m: Si es positivo, simplifica cada trazado con esa tolerancia.
    """
    result: list[Watercourse] = []
    for element in elements:
        if element.get("type") != "way":
            continue
        tags = element.get("tags") or {}
        geometry = element.get("geometry") or []
        vertices = tuple(
            Coordinates(latitude=round(p["lat"], 5), longitude=round(p["lon"], 5)) for p in geometry
        )
        # Vértices consecutivos repetidos tras redondear no aportan y pueden dejar tramos degenerados.
        deduped = tuple(v for i, v in enumerate(vertices) if i == 0 or v != vertices[i - 1])
        if tolerance_m > 0:
            deduped = simplify_vertices(deduped, tolerance_m)
        if len(deduped) < 2:
            continue
        name = (tags.get("name") or "").strip() or None
        kind = classify(name, tags.get("waterway", ""))
        result.append(
            Watercourse(
                id=uuid5(_OSM_NS, str(element["id"])),
                name=(name or _UNNAMED[kind])[:120],
                kind=kind,
                commune=commune,
                geometry=GeoLineString(vertices=deduped),
                geometry_source=SOURCE,
            )
        )
    return result


async def _overpass(http: httpx.AsyncClient, query: str, url: str) -> list[Mapping[str, Any]]:
    response = await http.post(
        url,
        data={"data": query},
        # Cabeceras HTTP sólo ASCII.
        headers={"User-Agent": "SAT-Maule/1.0 (sistema de alerta temprana; importacion unica)"},
        timeout=httpx.Timeout(900.0),
    )
    if response.status_code != 200:
        raise ExternalServiceError(f"Overpass respondió {response.status_code}: {response.text[:200]}")
    elements: list[Mapping[str, Any]] = response.json().get("elements", [])
    return elements


async def fetch_maule_waterways(http: httpx.AsyncClient, url: str = OVERPASS_URL) -> list[Watercourse]:
    """Descarga y convierte la red hidrográfica completa del Maule (puede tardar 1-3 minutos)."""
    return elements_to_watercourses(await _overpass(http, build_query(), url))


async def fetch_region_waterways(
    http: httpx.AsyncClient, iso_code: str, region_name: str, *, tolerance_m: float = 15.0, url: str = OVERPASS_URL
) -> list[Watercourse]:
    """Red hidrográfica simplificada de una región de Chile."""
    elements = await _overpass(http, build_query(timeout_s=900, area=region_area(iso_code)), url)
    return elements_to_watercourses(elements, commune=region_name, tolerance_m=tolerance_m)


# ---------------------------------------------------------------- comunas


def build_comunas_query(iso_code: str, timeout_s: int = 600) -> str:
    """Relaciones de comunas (``admin_level=8``) de una región, con la geometría de sus tramos."""
    return (
        f"[out:json][timeout:{timeout_s}];{region_area(iso_code)}"
        # ``out geom`` (no ``out tags geom``): las relaciones necesitan sus miembros con geometría.
        'rel["boundary"="administrative"]["admin_level"="8"](area.a);out geom;'
    )


def stitch_rings(ways: Sequence[Sequence[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Une tramos (lon, lat) que comparten extremos en anillos cerrados.

    Los límites de OSM llegan partidos en muchos tramos, en cualquier
    sentido. Se encadenan por extremos coincidentes (invirtiendo si hace
    falta); un tramo que no cierra se descarta.
    """
    pending = [list(w) for w in ways if len(w) >= 2]
    rings: list[list[tuple[float, float]]] = []
    while pending:
        ring = pending.pop()
        while ring[0] != ring[-1]:
            for i, way in enumerate(pending):
                if way[0] == ring[-1]:
                    ring.extend(way[1:])
                elif way[-1] == ring[-1]:
                    ring.extend(reversed(way[:-1]))
                else:
                    continue
                pending.pop(i)
                break
            else:
                break  # no cierra
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
    return rings


def elements_to_admin_areas(
    elements: Iterable[Mapping[str, Any]], *, region_code: str, region_name: str, tolerance_m: float = 50.0
) -> list[AdminArea]:
    """Convierte relaciones de comunas (``out geom``) en :class:`AdminArea`."""
    areas: list[AdminArea] = []
    for element in elements:
        if element.get("type") != "relation":
            continue
        name = ((element.get("tags") or {}).get("name") or "").strip()
        outer = [
            [(p["lon"], p["lat"]) for p in m.get("geometry") or []]
            for m in element.get("members") or []
            if m.get("type") == "way" and m.get("role") in ("outer", "")
        ]
        parts: list[GeoPolygon] = []
        for ring in stitch_rings(outer):
            vertices = simplify_vertices(
                [Coordinates(longitude=round(x, 5), latitude=round(y, 5)) for x, y in ring], tolerance_m
            )
            if len(set(vertices)) >= 3:
                parts.append(GeoPolygon(ring=vertices))
        if len(name) >= 2 and parts:
            areas.append(AdminArea(comuna=name[:80], region=region_name, region_code=region_code, parts=tuple(parts)))
        else:
            logger.warning("Comuna %r (%s) sin anillos cerrados; se omite", name, region_code)
    return areas


async def fetch_region_comunas(
    http: httpx.AsyncClient, iso_code: str, region_name: str, url: str = OVERPASS_URL
) -> list[AdminArea]:
    """Comunas de una región de Chile con su límite simplificado."""
    elements = await _overpass(http, build_comunas_query(iso_code), url)
    return elements_to_admin_areas(elements, region_code=iso_code, region_name=region_name)


# ---------------------------------------------------------------- GeoJSON en disco


def to_feature_collection(courses: Sequence[Watercourse]) -> dict[str, Any]:
    """Serializa a GeoJSON (``FeatureCollection`` de ``LineString``)."""
    return {
        "type": "FeatureCollection",
        "attribution": "© OpenStreetMap contributors, ODbL 1.0",
        "features": [
            {
                "type": "Feature",
                "id": str(c.id),
                "properties": {"name": c.name, "kind": c.kind.value, "source": c.geometry_source, "commune": c.commune},
                "geometry": c.geometry.to_geojson(),
            }
            for c in courses
        ],
    }


def load_feature_collection(path: Path) -> list[Watercourse]:
    """Lee el GeoJSON generado por :func:`to_feature_collection`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Watercourse.model_validate(
            {
                "id": f["id"],
                "name": f["properties"]["name"],
                "kind": f["properties"]["kind"],
                "commune": f["properties"].get("commune", "Maule"),
                "geometry": GeoLineString.from_geojson(f["geometry"]),
                "geometry_source": f["properties"].get("source", SOURCE),
            }
        )
        for f in data["features"]
    ]


# ---------------------------------------------------------------- servicios de emergencia

_HEALTH_NAME = re.compile(r"\b(hospital|cesfam|sapu|sar|posta|cecosf|urgencia|consultorio)\b", re.IGNORECASE)


def build_services_query(iso_code: str, timeout_s: int = 300) -> str:
    """Carabineros/PDI, bomberos, hospitales y centros de salud públicos, municipalidades."""
    return (
        f"[out:json][timeout:{timeout_s}];{region_area(iso_code)}"
        'nwr(area.a)["amenity"~"^(police|fire_station|townhall|hospital|clinic|doctors)$"];'
        "out center tags;"
    )


def _service_kind(tags: Mapping[str, Any]) -> ServiceKind | None:
    amenity = tags.get("amenity")
    name = str(tags.get("name") or "")
    if amenity == "police":
        return ServiceKind.CARABINEROS
    if amenity == "fire_station":
        return ServiceKind.BOMBEROS
    if amenity == "townhall":
        return ServiceKind.MUNICIPALIDAD
    if amenity == "hospital" or (amenity in ("clinic", "doctors") and _HEALTH_NAME.search(name)):
        return ServiceKind.SALUD  # clínicas dentales o privadas sin urgencia quedan fuera
    return None


def _service_name(kind: ServiceKind, tags: Mapping[str, Any]) -> str:
    name = str(tags.get("name") or tags.get("official_name") or "").strip()
    if name:
        return name[:160]
    return {
        ServiceKind.CARABINEROS: "Carabineros",
        ServiceKind.BOMBEROS: "Bomberos",
        ServiceKind.SALUD: "Centro de salud",
        ServiceKind.MUNICIPALIDAD: "Municipalidad",
    }[kind]


def elements_to_services(elements: Sequence[Mapping[str, Any]]) -> list[EmergencyService]:
    """Convierte nodos/áreas de Overpass (``out center``) en servicios."""
    services: list[EmergencyService] = []
    for element in elements:
        tags: Mapping[str, Any] = element.get("tags") or {}
        kind = _service_kind(tags)
        point = element.get("center") or element
        if kind is None or "lat" not in point or "lon" not in point:
            continue
        raw_phone = tags.get("phone") or tags.get("contact:phone")
        phone = str(raw_phone).split(";")[0].strip() if raw_phone else None
        services.append(
            EmergencyService(
                kind=kind,
                name=_service_name(kind, tags),
                phone=phone or None,
                location=Coordinates(latitude=float(point["lat"]), longitude=float(point["lon"])),
            )
        )
    return services


async def fetch_region_services(
    http: httpx.AsyncClient, iso_code: str, url: str = OVERPASS_URL
) -> list[EmergencyService]:
    """Servicios de emergencia de una región de Chile."""
    return elements_to_services(await _overpass(http, build_services_query(iso_code), url))


# ---------------------------------------------------------------- refugios y puntos de encuentro

_NOT_REFUGE = "public_transport|picnic_shelter|lean_to|basic_hut|weather_shelter|rock_shelter|sun_shelter|field_shelter|gazebo|pergola"
_HAZARDS = {
    "tsunami": "tsunami",
    "flood": "inundación",
    "earthquake": "terremoto",
    "fire": "incendio",
    "landslide": "deslizamiento",
}


def build_refuges_query(iso_code: str, timeout_s: int = 300) -> str:
    """Puntos de encuentro, albergues y refugios techados (sin paraderos ni quinchos)."""
    return (
        f"[out:json][timeout:{timeout_s}];{region_area(iso_code)}("
        'nwr(area.a)["emergency"~"^(assembly_point|shelter)$"];'
        'nwr(area.a)["social_facility"="shelter"]["social_facility:for"!~"homeless|drug_addicted"];'
        f'nwr(area.a)["amenity"="shelter"]["shelter_type"!~"{_NOT_REFUGE}"]["name"];'
        ");out center tags;"
    )


def elements_to_refuges(elements: Sequence[Mapping[str, Any]]) -> list[EmergencyService]:
    refuges: list[EmergencyService] = []
    for element in elements:
        tags: Mapping[str, Any] = element.get("tags") or {}
        point = element.get("center") or element
        if "lat" not in point or "lon" not in point:
            continue
        is_point = tags.get("emergency") == "assembly_point"
        kind = ServiceKind.PUNTO_ENCUENTRO if is_point else ServiceKind.ALBERGUE
        hazards = [label for key, label in _HAZARDS.items() if tags.get(f"assembly_point:{key}") == "yes"]
        name = str(tags.get("name") or "").strip() or ("Punto de encuentro" if is_point else "Albergue")
        raw_phone = tags.get("phone") or tags.get("contact:phone")
        refuges.append(
            EmergencyService(
                kind=kind,
                name=name[:160],
                phone=str(raw_phone).split(";")[0].strip() if raw_phone else None,
                location=Coordinates(latitude=float(point["lat"]), longitude=float(point["lon"])),
                detail=", ".join(hazards) or None,
            )
        )
    return refuges


async def fetch_region_refuges(
    http: httpx.AsyncClient, iso_code: str, url: str = OVERPASS_URL
) -> list[EmergencyService]:
    """Refugios y puntos de encuentro de una región de Chile."""
    return elements_to_refuges(await _overpass(http, build_refuges_query(iso_code), url))


# ---------------------------------------------------------------- localidades

def build_places_query(iso_code: str, timeout_s: int = 300) -> str:
    kinds = "|".join(k.value for k in PlaceKind)
    return f'[out:json][timeout:{timeout_s}];{region_area(iso_code)}node(area.a)["place"~"^({kinds})$"]["name"];out;'


def elements_to_localities(elements: Sequence[Mapping[str, Any]]) -> list[Locality]:
    localities: list[Locality] = []
    for element in elements:
        tags: Mapping[str, Any] = element.get("tags") or {}
        name = str(tags.get("name") or "").strip()
        try:
            kind = PlaceKind(str(tags.get("place")))
        except ValueError:
            continue
        if not name or "lat" not in element or "lon" not in element:
            continue
        localities.append(
            Locality(
                name=name[:120],
                kind=kind,
                location=Coordinates(latitude=float(element["lat"]), longitude=float(element["lon"])),
            )
        )
    return localities


async def fetch_region_localities(http: httpx.AsyncClient, iso_code: str, url: str = OVERPASS_URL) -> list[Locality]:
    """Pueblos, villas, sectores y caseríos de una región de Chile."""
    return elements_to_localities(await _overpass(http, build_places_query(iso_code), url))

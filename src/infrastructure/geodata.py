"""Datos geográficos de Chile guardados en ``data/chile/`` (comunas y red hidrográfica por región).

Los genera ``python scripts/import_chile.py`` (una vez, desde OpenStreetMap).
Las comunas se cargan en memoria: son ~346 polígonos simplificados y la
búsqueda punto-en-polígono con caja envolvente previa es instantánea.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.application.dto import MunicipalPhone
from src.domain.entities.watercourse import Watercourse
from src.domain.value_objects.admin_area import AdminArea
from src.domain.value_objects.locality import Locality, PlaceKind
from src.domain.value_objects.emergency_service import EMERGENCY_KINDS, EmergencyService, ServiceKind
from src.domain.value_objects.coordinates import Coordinates, GeoPolygon
from src.infrastructure.external_apis.overpass_client import load_feature_collection

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "chile"
COMUNAS_FILE = DATA_DIR / "comunas.geojson"


def waterways_file(iso_code: str) -> Path:
    return DATA_DIR / f"waterways_{iso_code}.geojson"


def comunas_to_feature_collection(areas: Sequence[AdminArea]) -> dict[str, Any]:
    """Serializa comunas como ``FeatureCollection`` de ``MultiPolygon`` (sólo anillos exteriores)."""
    return {
        "type": "FeatureCollection",
        "attribution": "© OpenStreetMap contributors, ODbL 1.0",
        "features": [
            {
                "type": "Feature",
                "properties": {"comuna": a.comuna, "region": a.region, "region_code": a.region_code},
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[[[c.longitude, c.latitude] for c in p.ring]] for p in a.parts],
                },
            }
            for a in areas
        ],
    }


def load_comunas(path: Path = COMUNAS_FILE) -> list[AdminArea]:
    """Lee el archivo de comunas; lista vacía si aún no se importó."""
    if not path.exists():
        logger.warning("Sin %s: no se identificará la comuna (ejecute scripts/import_chile.py)", path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    areas: list[AdminArea] = []
    for f in data["features"]:
        parts = tuple(
            GeoPolygon(ring=tuple(Coordinates(longitude=x, latitude=y) for x, y in polygon[0]))
            for polygon in f["geometry"]["coordinates"]
        )
        props = f["properties"]
        areas.append(
            AdminArea(comuna=props["comuna"], region=props["region"], region_code=props["region_code"], parts=parts)
        )
    return areas


def load_chile_waterways() -> list[Watercourse]:
    """Red hidrográfica de todas las regiones importadas (lista vacía si no hay ninguna)."""
    courses: list[Watercourse] = []
    for path in sorted(DATA_DIR.glob("waterways_CL-*.geojson")):
        courses.extend(load_feature_collection(path))
    return courses


@lru_cache(maxsize=1)
def chile_comunas() -> tuple[AdminArea, ...]:
    """Comunas cargadas una sola vez por proceso (búsqueda y vista regional)."""
    return tuple(load_comunas())


def _fold(text: str) -> str:
    """Minúsculas y sin tildes, para buscar «Ñuñoa» escribiendo «nunoa»."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


SEARCHABLE_KINDS = (
    PlaceKind.CITY, PlaceKind.TOWN, PlaceKind.VILLAGE, PlaceKind.SUBURB, PlaceKind.HAMLET, PlaceKind.LOCALITY,
)
"""Tipos de lugar que se pueden buscar por nombre (sin casas aisladas ni barrios pequeños)."""
_KIND_RANK = {"comuna": 0, PlaceKind.CITY: 1, PlaceKind.TOWN: 1, PlaceKind.VILLAGE: 2, PlaceKind.SUBURB: 3}


@dataclass(frozen=True, slots=True)
class PlaceMatch:
    """Resultado de búsqueda: una comuna o una localidad (pueblo, villa, sector) dentro de una comuna."""

    name: str
    kind: str
    """``comuna`` o el tipo de lugar de OpenStreetMap (``town``, ``village``...)."""
    comuna: str
    region: str
    location: Coordinates


class PlaceIndex:
    """Búsqueda de comunas, pueblos y localidades por nombre (para elegir otra ubicación en la app)."""

    def __init__(self, areas: Sequence[AdminArea], localities: Sequence[Locality] = ()) -> None:
        self._areas = sorted(areas, key=lambda a: _fold(a.comuna))
        self._keys = [(_fold(a.comuna), _fold(a.region)) for a in self._areas]
        self._localities = [(loc, _fold(loc.name)) for loc in localities if loc.kind in SEARCHABLE_KINDS]

    def search_places(self, query: str, limit: int = 10) -> list[PlaceMatch]:
        """Comunas y localidades: primero el nombre exacto, luego los que empiezan con el texto
        y al final los que lo contienen; a igual coincidencia, comunas y pueblos antes que caseríos."""
        q = _fold(query.strip())
        if len(q) < 2:
            return []

        def tier(name: str) -> int | None:
            if name == q:
                return 0
            if name.startswith(q):
                return 1
            if f" {q}" in f" {name}":
                return 2  # empieza una palabra: «manquehue» encuentra «Santa María de Manquehue»
            return 3 if len(q) >= 3 and q in name else None

        scored: list[tuple[int, int, int, AdminArea | Locality]] = []
        for a, (c, _) in zip(self._areas, self._keys, strict=True):
            if (t := tier(c)) is not None:
                scored.append((t, 0, len(c), a))
        for loc, name in self._localities:
            if (t := tier(name)) is not None:
                scored.append((t, _KIND_RANK.get(loc.kind, 4), len(name), loc))
        scored.sort(key=lambda s: s[:3])

        matches: list[PlaceMatch] = []
        seen: set[tuple[str, str]] = set()
        for *_, item in scored:
            if len(matches) >= limit:
                break
            if isinstance(item, AdminArea):
                match = PlaceMatch(item.comuna, "comuna", item.comuna, item.region, item.representative_point)
            else:
                area = next((a for a in self._areas if a.contains(item.location)), None)
                if area is None:
                    continue
                match = PlaceMatch(item.name, item.kind.value, area.comuna, area.region, item.location)
            key = (_fold(match.name), _fold(match.comuna))
            if key in seen:
                continue  # la ciudad de Talca ya aparece como comuna de Talca
            seen.add(key)
            matches.append(match)
        return matches

    def search(self, query: str, limit: int = 8) -> list[AdminArea]:
        """Primero las comunas que empiezan con el texto; luego las que lo contienen."""
        q = _fold(query.strip())
        if len(q) < 2:
            return []
        starts = [a for a, (c, _) in zip(self._areas, self._keys, strict=True) if c.startswith(q)]
        contains = [
            a for a, (c, r) in zip(self._areas, self._keys, strict=True) if not c.startswith(q) and (q in c or q in r)
        ]
        return (starts + contains)[:limit]


class InMemoryAdminAreaLocator:
    """Implementa :class:`AdminAreaLocator` sobre las comunas cargadas en memoria."""

    def __init__(self, areas: Sequence[AdminArea]) -> None:
        self._areas = tuple(areas)

    @property
    def available(self) -> bool:
        return bool(self._areas)

    async def locate(self, point: Coordinates) -> AdminArea | None:
        return next((a for a in self._areas if a.contains(point)), None)


# ---------------------------------------------------------------- servicios de emergencia


def services_file(iso_code: str) -> Path:
    return DATA_DIR / f"servicios_{iso_code}.json"


def services_to_json(services: Sequence[EmergencyService]) -> list[dict[str, Any]]:
    return [
        {
            "kind": s.kind.value,
            "name": s.name,
            "phone": s.phone,
            "lat": s.location.latitude,
            "lon": s.location.longitude,
            **({"detail": s.detail} if s.detail else {}),
        }
        for s in services
    ]


def refuges_file(iso_code: str) -> Path:
    return DATA_DIR / f"refugios_{iso_code}.json"


def load_refuges(data_dir: Path = DATA_DIR) -> list[EmergencyService]:
    """Todos los ``refugios_*.json`` importados; lista vacía si aún no hay."""
    return load_services(data_dir, pattern="refugios_*.json")


def load_services(data_dir: Path = DATA_DIR, pattern: str = "servicios_*.json") -> list[EmergencyService]:
    """Todos los ``servicios_*.json`` importados; lista vacía si aún no hay."""
    services: list[EmergencyService] = []
    for path in sorted(data_dir.glob(pattern)):
        for row in json.loads(path.read_text(encoding="utf-8")):
            services.append(
                EmergencyService(
                    kind=ServiceKind(row["kind"]),
                    name=row["name"],
                    phone=row.get("phone"),
                    location=Coordinates(latitude=row["lat"], longitude=row["lon"]),
                    detail=row.get("detail"),
                )
            )
    return services


class InMemoryEmergencyServiceFinder:
    """El servicio más cercano de cada tipo (búsqueda lineal: ~5.000 puntos en todo Chile)."""

    def __init__(self, services: Sequence[EmergencyService]) -> None:
        self._services = tuple(services)

    async def find_nearest(
        self, point: Coordinates, max_km: float = 40.0
    ) -> Sequence[tuple[EmergencyService, float]]:
        best: dict[ServiceKind, tuple[EmergencyService, float]] = {}
        for service in self._services:
            if abs(service.location.latitude - point.latitude) > max_km / 111.0 + 0.01:
                continue  # descarte rápido por latitud
            distance = point.distance_km(service.location)
            if distance > max_km:
                continue
            current = best.get(service.kind)
            # Con distancias parecidas (menos de 0,5 km de diferencia), gana el que tiene teléfono directo.
            if current is None or _effective_km(service, distance) < _effective_km(*current):
                best[service.kind] = (service, distance)
        return [best[k] for k in EMERGENCY_KINDS if k in best]


def _effective_km(service: EmergencyService, distance_km: float) -> float:
    return distance_km - (0.5 if service.phone else 0.0)


MUNICIPAL_PHONES_FILE = DATA_DIR / "seguridad_municipal.json"


def load_municipal_phones(path: Path = MUNICIPAL_PHONES_FILE) -> dict[tuple[str, str], MunicipalPhone]:
    """Líneas de seguridad municipal por (código de región, comuna); vacío si no hay archivo."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        (row["region_code"], row["comuna"]): MunicipalPhone(
            comuna=row["comuna"], phone=row["phone"], verified=row.get("verified", False), note=row.get("note")
        )
        for row in data["phones"]
    }


class InMemoryRefugeFinder:
    """Refugios y puntos de encuentro cercanos a un punto (todo Chile en memoria)."""

    def __init__(self, refuges: Sequence[EmergencyService]) -> None:
        self._refuges = tuple(refuges)

    @property
    def available(self) -> bool:
        return bool(self._refuges)

    def find_near(self, point: Coordinates, radius_km: float = 10.0, limit: int = 60) -> list[tuple[EmergencyService, float]]:
        lat_margin = radius_km / 111.0 + 0.01
        found = [
            (refuge, distance)
            for refuge in self._refuges
            if abs(refuge.location.latitude - point.latitude) <= lat_margin
            and (distance := point.distance_km(refuge.location)) <= radius_km
        ]
        return sorted(found, key=lambda item: item[1])[:limit]


@lru_cache(maxsize=1)
def chile_refuges() -> InMemoryRefugeFinder:
    return InMemoryRefugeFinder(load_refuges())


# ---------------------------------------------------------------- localidades


def localities_file(iso_code: str) -> Path:
    return DATA_DIR / f"localidades_{iso_code}.json"


def localities_to_json(localities: Sequence[Locality]) -> list[list[Any]]:
    """Formato compacto ``[nombre, tipo, lat, lon]``: hay decenas de miles en Chile."""
    return [[x.name, x.kind.value, round(x.location.latitude, 5), round(x.location.longitude, 5)] for x in localities]


def load_localities(data_dir: Path = DATA_DIR) -> list[Locality]:
    localities: list[Locality] = []
    for path in sorted(data_dir.glob("localidades_*.json")):
        for name, kind, lat, lon in json.loads(path.read_text(encoding="utf-8")):
            localities.append(
                Locality(name=name, kind=PlaceKind(kind), location=Coordinates(latitude=lat, longitude=lon))
            )
    return localities


class InMemoryLocalityFinder:
    """Localidad que mejor nombra un punto: la más cercana, con ventaja para pueblos y villas."""

    def __init__(self, localities: Sequence[Locality], within_km: float = 3.0, near_km: float = 12.0) -> None:
        self._localities = tuple(localities)
        self._within = within_km
        self._near = near_km

    async def nearest(self, point: Coordinates) -> tuple[Locality, float] | None:
        margin = self._near / 111.0 + 0.01
        candidates = [
            (loc, d)
            for loc in self._localities
            if abs(loc.location.latitude - point.latitude) <= margin
            and (d := point.distance_km(loc.location)) <= self._near
        ]
        if not candidates:
            return None
        close = [c for c in candidates if c[1] <= self._within]
        if close:
            return min(close, key=lambda c: c[1] - c[0].kind.preference_km)
        return min(candidates, key=lambda c: c[1])

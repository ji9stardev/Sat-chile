"""Value Objects geoespaciales con soporte GeoJSON (RFC 7946).

GeoJSON usa el orden ``[longitud, latitud]``; estos objetos exponen
``latitude``/``longitude`` con nombre explícito y se encargan del orden al
serializar, evitando el error clásico de invertir ejes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

EARTH_RADIUS_KM: float = 6371.0088
"""Radio medio terrestre (IUGG) usado en la fórmula de Haversine."""

# Caja envolvente de Chile continental e insular cercano (sin Rapa Nui ni Antártica).
CHILE_LAT_RANGE: tuple[float, float] = (-56.0, -17.4)
CHILE_LON_RANGE: tuple[float, float] = (-76.0, -66.3)

# Caja envolvente aproximada de la Región del Maule (con margen costero/cordillerano).
MAULE_LAT_RANGE: tuple[float, float] = (-36.6, -34.6)
MAULE_LON_RANGE: tuple[float, float] = (-72.9, -70.2)


class Coordinates(BaseModel):
    """Punto geográfico inmutable en WGS84 (EPSG:4326).

    Attributes:
        latitude: Latitud en grados decimales, rango [-90, 90].
        longitude: Longitud en grados decimales, rango [-180, 180].
        altitude_m: Altitud opcional en metros sobre el nivel del mar.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    altitude_m: float | None = None

    def to_geojson(self) -> dict[str, Any]:
        """Serializa como ``GeoJSON Point`` (``[lon, lat]`` o ``[lon, lat, alt]``)."""
        coords: list[float] = [self.longitude, self.latitude]
        if self.altitude_m is not None:
            coords.append(self.altitude_m)
        return {"type": "Point", "coordinates": coords}

    @classmethod
    def from_geojson(cls, geometry: dict[str, Any]) -> Self:
        """Construye desde un ``GeoJSON Point``.

        Raises:
            ValueError: Si la geometría no es un ``Point`` válido.
        """
        if geometry.get("type") != "Point":
            raise ValueError("La geometría GeoJSON debe ser de tipo 'Point'.")
        coords = geometry.get("coordinates")
        if not isinstance(coords, (list, tuple)) or len(coords) not in (2, 3):
            raise ValueError("'coordinates' debe contener [lon, lat] o [lon, lat, alt].")
        altitude = float(coords[2]) if len(coords) == 3 else None
        return cls(longitude=float(coords[0]), latitude=float(coords[1]), altitude_m=altitude)

    def distance_km(self, other: Coordinates) -> float:
        """Distancia ortodrómica (Haversine) en kilómetros, ignorando altitud."""
        phi1, phi2 = math.radians(self.latitude), math.radians(other.latitude)
        d_phi = phi2 - phi1
        d_lambda = math.radians(other.longitude - self.longitude)
        h = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))

    @property
    def is_within_chile(self) -> bool:
        """Chequeo rápido (bounding box) de Chile continental."""
        return (
            CHILE_LAT_RANGE[0] <= self.latitude <= CHILE_LAT_RANGE[1]
            and CHILE_LON_RANGE[0] <= self.longitude <= CHILE_LON_RANGE[1]
        )

    @property
    def is_within_maule(self) -> bool:
        """Chequeo rápido (bounding box) de pertenencia a la Región del Maule."""
        return (
            MAULE_LAT_RANGE[0] <= self.latitude <= MAULE_LAT_RANGE[1]
            and MAULE_LON_RANGE[0] <= self.longitude <= MAULE_LON_RANGE[1]
        )


class GeoPolygon(BaseModel):
    """Polígono simple inmutable (un anillo exterior) compatible con GeoJSON.

    El anillo se almacena cerrado (primer vértice == último), como exige
    RFC 7946. Si se entrega abierto, se cierra automáticamente.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["Polygon"] = "Polygon"
    ring: tuple[Coordinates, ...] = Field(min_length=3)

    @model_validator(mode="before")
    @classmethod
    def _close_ring(cls, data: Any) -> Any:
        if isinstance(data, dict) and "ring" in data:
            ring = list(data["ring"])
            if len(ring) >= 3 and ring[0] != ring[-1]:
                ring.append(ring[0])
            data = {**data, "ring": tuple(ring)}
        return data

    @model_validator(mode="after")
    def _validate_vertices(self) -> Self:
        if len(set(self.ring)) < 3:
            raise ValueError("Un polígono requiere al menos 3 vértices distintos.")
        return self

    def to_geojson(self) -> dict[str, Any]:
        """Serializa como ``GeoJSON Polygon``."""
        return {
            "type": "Polygon",
            "coordinates": [[[c.longitude, c.latitude] for c in self.ring]],
        }

    @classmethod
    def from_geojson(cls, geometry: dict[str, Any]) -> Self:
        """Construye desde un ``GeoJSON Polygon`` (solo anillo exterior)."""
        if geometry.get("type") != "Polygon":
            raise ValueError("La geometría GeoJSON debe ser de tipo 'Polygon'.")
        rings = geometry.get("coordinates") or []
        if not rings:
            raise ValueError("El polígono no contiene anillos.")
        exterior = [Coordinates(longitude=float(p[0]), latitude=float(p[1])) for p in rings[0]]
        return cls(ring=tuple(exterior))

    def contains(self, point: Coordinates) -> bool:
        """Point-in-polygon por *ray casting* (válido para zonas comunales pequeñas)."""
        x, y = point.longitude, point.latitude
        inside = False
        vertices = self.ring
        for i in range(len(vertices) - 1):
            x1, y1 = vertices[i].longitude, vertices[i].latitude
            x2, y2 = vertices[i + 1].longitude, vertices[i + 1].latitude
            if (y1 > y) != (y2 > y):
                x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if x < x_cross:
                    inside = not inside
        return inside

    @property
    def centroid(self) -> Coordinates:
        """Centroide aritmético de los vértices (aproximación suficiente para zonas locales)."""
        return _vertex_mean(self.ring[:-1])


class GeoLineString(BaseModel):
    """Polilínea inmutable (eje de un río, estero o quebrada) compatible con GeoJSON."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["LineString"] = "LineString"
    vertices: tuple[Coordinates, ...] = Field(min_length=2)

    def to_geojson(self) -> dict[str, Any]:
        """Serializa como ``GeoJSON LineString``."""
        return {"type": "LineString", "coordinates": [[c.longitude, c.latitude] for c in self.vertices]}

    @classmethod
    def from_geojson(cls, geometry: dict[str, Any]) -> Self:
        """Construye desde un ``GeoJSON LineString``."""
        if geometry.get("type") != "LineString":
            raise ValueError("La geometría GeoJSON debe ser de tipo 'LineString'.")
        points = geometry.get("coordinates") or []
        return cls(vertices=tuple(Coordinates(longitude=float(p[0]), latitude=float(p[1])) for p in points))

    def simplified(self, tolerance_m: float) -> GeoLineString:
        """Copia con menos vértices (ver :func:`simplify_vertices`)."""
        return GeoLineString(vertices=simplify_vertices(self.vertices, tolerance_m))

    def nearest_point(self, point: Coordinates) -> tuple[Coordinates, float]:
        """Punto de la polilínea más cercano a ``point`` y su distancia en km.

        Proyecta cada segmento en un plano equirectangular local centrado en
        ``point`` (error despreciable a escala de pocos km) y mide la
        distancia final con Haversine.
        """
        kx = math.cos(math.radians(point.latitude))
        best = (0.0, 0.0)
        best_d2 = math.inf
        for a, b in pairwise(self.vertices):
            ax, ay = (a.longitude - point.longitude) * kx, a.latitude - point.latitude
            bx, by = (b.longitude - point.longitude) * kx, b.latitude - point.latitude
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
            px, py = ax + t * dx, ay + t * dy
            d2 = px * px + py * py
            if d2 < best_d2:
                best_d2, best = d2, (px, py)
        nearest = Coordinates(latitude=point.latitude + best[1], longitude=point.longitude + best[0] / kx)
        return nearest, point.distance_km(nearest)


def simplify_vertices(vertices: Sequence[Coordinates], tolerance_m: float) -> tuple[Coordinates, ...]:
    """Douglas-Peucker: quita vértices que se apartan menos de ``tolerance_m`` de la línea.

    Conserva siempre el primero y el último. Se calcula en un plano
    equirectangular local (metros), suficiente para tramos de pocos km.
    """
    pts = tuple(vertices)
    if len(pts) <= 2 or tolerance_m <= 0:
        return pts
    lat0 = math.radians(pts[0].latitude)
    m_per_deg = math.pi * EARTH_RADIUS_KM * 1000 / 180
    xy = [(p.longitude * math.cos(lat0) * m_per_deg, p.latitude * m_per_deg) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        start, end = stack.pop()
        (ax, ay), (bx, by) = xy[start], xy[end]
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        worst, worst_d = -1, tolerance_m
        for i in range(start + 1, end):
            px, py = xy[i]
            d = math.hypot(px - ax, py - ay) if seg == 0 else abs(dy * (px - ax) - dx * (py - ay)) / seg
            if d > worst_d:
                worst, worst_d = i, d
        if worst >= 0:
            keep[worst] = True
            stack.extend(((start, worst), (worst, end)))
    return tuple(p for p, k in zip(pts, keep, strict=True) if k)


def _vertex_mean(vertices: tuple[Coordinates, ...]) -> Coordinates:
    """Centroide aritmético de un conjunto de vértices."""
    if not vertices:
        raise ValueError("Se requiere al menos un vértice.")
    return Coordinates(
        latitude=sum(v.latitude for v in vertices) / len(vertices),
        longitude=sum(v.longitude for v in vertices) / len(vertices),
    )

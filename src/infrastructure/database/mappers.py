"""Traducción entre entidades de dominio y documentos BSON.

Las entidades no saben que existen en MongoDB: este módulo se encarga de

* usar ``_id`` en lugar de ``id``;
* guardar ``Coordinates``/``GeoPolygon`` como GeoJSON 2D (lo que exige el
  índice ``2dsphere``), con la altitud en un campo aparte;
* convertir enums, conjuntos y URLs a tipos BSON;
* agregar campos derivados sólo para consultas (p. ej. ``risk_severity``)
  y retirarlos al reconstruir la entidad.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import AnyUrl

from src.domain.entities import (
    Alert,
    CitizenReport,
    Entity,
    GeoZone,
    SafePoint,
    Subscriber,
    Telemetry,
    Watercourse,
)
from src.domain.value_objects.coordinates import Coordinates, GeoLineString, GeoPolygon

E = TypeVar("E", bound=Entity)
Document = dict[str, Any]

_ALTITUDE_SUFFIX = "_altitude_m"


def to_bson_value(value: Any) -> Any:
    """Convierte recursivamente un valor de ``model_dump`` a un tipo BSON nativo."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        # BSON guarda milisegundos: se trunca explícitamente para que lo
        # escrito sea exactamente lo que se leerá.
        return value.replace(microsecond=value.microsecond // 1000 * 1000)
    if value is None or isinstance(value, (bool, int, float, str, UUID)):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, AnyUrl):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): to_bson_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_bson_value(v) for v in value]
    raise TypeError(f"Tipo no serializable a BSON: {type(value).__name__}")


class EntityMapper(Generic[E]):
    """Mapper configurable entidad ⇄ documento.

    Args:
        entity_cls: Clase de la entidad.
        point_fields: Campos de tipo ``Coordinates`` (o ``None``).
        polygon_fields: Campos de tipo ``GeoPolygon``.
        line_fields: Campos de tipo ``GeoLineString``.
        derived: Función que calcula campos extra sólo para indexar/consultar.
    """

    def __init__(
        self,
        entity_cls: type[E],
        *,
        point_fields: tuple[str, ...] = (),
        polygon_fields: tuple[str, ...] = (),
        line_fields: tuple[str, ...] = (),
        derived: Callable[[E], Document] | None = None,
    ) -> None:
        self._cls = entity_cls
        self._points = point_fields
        self._polygons = polygon_fields
        self._lines = line_fields
        self._derived = derived

    def to_document(self, entity: E) -> Document:
        """Entidad → documento BSON."""
        geo = set(self._points) | set(self._polygons) | set(self._lines)
        raw = entity.model_dump(mode="python", exclude={"id", *geo})
        doc: Document = {"_id": entity.id, **{k: to_bson_value(v) for k, v in raw.items()}}

        for field in self._points:
            point: Coordinates | None = getattr(entity, field)
            if point is None:
                continue
            doc[field] = {"type": "Point", "coordinates": [point.longitude, point.latitude]}
            if point.altitude_m is not None:
                doc[field + _ALTITUDE_SUFFIX] = point.altitude_m
        for field in self._polygons:
            polygon: GeoPolygon = getattr(entity, field)
            doc[field] = polygon.to_geojson()
        for field in self._lines:
            line: GeoLineString = getattr(entity, field)
            doc[field] = line.to_geojson()

        if self._derived is not None:
            doc.update(to_bson_value(self._derived(entity)))
        return doc

    def from_document(self, doc: Mapping[str, Any]) -> E:
        """Documento BSON → entidad (validada por Pydantic).

        Se descartan los campos que la entidad no declara (derivados o
        heredados de versiones anteriores del esquema) para tolerar la
        evolución del modelo sin migraciones bloqueantes.
        """
        fields = self._cls.model_fields
        data: Document = {"id": doc["_id"]}
        for key, value in doc.items():
            if key in fields:
                data[key] = value
        for field in self._points:
            altitude = doc.get(field + _ALTITUDE_SUFFIX)
            geometry = data.get(field)
            if geometry is not None:
                lon, lat = geometry["coordinates"][:2]
                data[field] = Coordinates(latitude=lat, longitude=lon, altitude_m=altitude)
        for field in self._polygons:
            data[field] = GeoPolygon.from_geojson(data[field])
        for field in self._lines:
            data[field] = GeoLineString.from_geojson(data[field])
        return self._cls.model_validate(data)


def _alert_derived(alert: Alert) -> Document:
    return {"risk_severity": alert.risk_level.severity}


def _safe_point_derived(point: SafePoint) -> Document:
    return {"available_capacity": point.available_capacity}


ALERT_MAPPER = EntityMapper(Alert, derived=_alert_derived)
TELEMETRY_MAPPER = EntityMapper(Telemetry, point_fields=("location",))
GEO_ZONE_MAPPER = EntityMapper(GeoZone, polygon_fields=("boundary",))
CITIZEN_REPORT_MAPPER = EntityMapper(CitizenReport, point_fields=("location",))
SAFE_POINT_MAPPER = EntityMapper(SafePoint, point_fields=("location",), derived=_safe_point_derived)
SUBSCRIBER_MAPPER = EntityMapper(Subscriber, point_fields=("location",))
WATERCOURSE_MAPPER = EntityMapper(Watercourse, line_fields=("geometry",))


def point_geometry(point: Coordinates) -> Document:
    """GeoJSON 2D para usar en filtros ``$geometry``."""
    return {"type": "Point", "coordinates": [point.longitude, point.latitude]}

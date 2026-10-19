"""Búsqueda de comunas para «Cambiar ubicación»."""

from __future__ import annotations

from src.domain.value_objects.admin_area import AdminArea
from src.domain.value_objects.coordinates import Coordinates, GeoPolygon
from src.infrastructure.geodata import PlaceIndex


def square(lat: float, lon: float, size: float = 0.1) -> GeoPolygon:
    return GeoPolygon(
        ring=(
            Coordinates(latitude=lat, longitude=lon),
            Coordinates(latitude=lat + size, longitude=lon),
            Coordinates(latitude=lat + size, longitude=lon + size),
            Coordinates(latitude=lat, longitude=lon + size),
        )
    )


def area(comuna: str, region: str = "Maule", code: str = "CL-ML", lat: float = -35.5) -> AdminArea:
    return AdminArea(comuna=comuna, region=region, region_code=code, parts=(square(lat, -71.7),))


INDEX = PlaceIndex(
    [
        area("Talca"),
        area("Talcahuano", "Biobío", "CL-BI", -36.7),
        area("Ñuñoa", "Metropolitana de Santiago", "CL-RM", -33.5),
        area("San Clemente"),
    ]
)


def test_prefix_matches_come_first() -> None:
    assert [a.comuna for a in INDEX.search("talca")] == ["Talca", "Talcahuano"]


def test_search_ignores_accents_and_case() -> None:
    assert [a.comuna for a in INDEX.search("NUNOA")] == ["Ñuñoa"]


def test_search_matches_inside_name_and_region() -> None:
    assert [a.comuna for a in INDEX.search("clemente")] == ["San Clemente"]
    assert {a.comuna for a in INDEX.search("biobio")} == {"Talcahuano"}


def test_too_short_query_returns_nothing() -> None:
    assert INDEX.search(" t ") == []


def test_representative_point_is_inside_c_shaped_area() -> None:
    # Una «C»: el promedio de sus vértices cae en el hueco, fuera de la comuna.
    c_shape = GeoPolygon(
        ring=(
            Coordinates(latitude=0.0, longitude=0.0),
            Coordinates(latitude=0.0, longitude=3.0),
            Coordinates(latitude=1.0, longitude=3.0),
            Coordinates(latitude=1.0, longitude=1.0),
            Coordinates(latitude=2.0, longitude=1.0),
            Coordinates(latitude=2.0, longitude=3.0),
            Coordinates(latitude=3.0, longitude=3.0),
            Coordinates(latitude=3.0, longitude=0.0),
        )
    )
    shaped = AdminArea(comuna="Forma C", region="Maule", region_code="CL-ML", parts=(c_shape,))
    assert shaped.contains(shaped.representative_point)


def test_localities_are_searchable_with_their_comuna() -> None:
    from src.domain.value_objects.locality import Locality, PlaceKind

    index = PlaceIndex(
        [area("Talca"), area("San Clemente", lat=-35.7)],
        [
            Locality(name="Vilches", kind=PlaceKind.VILLAGE, location=Coordinates(latitude=-35.65, longitude=-71.65)),
            Locality(name="Talca", kind=PlaceKind.CITY, location=Coordinates(latitude=-35.45, longitude=-71.65)),
            Locality(name="Casa Vilches", kind=PlaceKind.ISOLATED_DWELLING, location=Coordinates(latitude=-35.65, longitude=-71.65)),
        ],
    )
    [vilches] = index.search_places("vilches")
    assert (vilches.name, vilches.kind, vilches.comuna) == ("Vilches", "village", "San Clemente")
    # la ciudad de Talca no se repite: ya aparece como comuna
    assert [(m.name, m.kind) for m in index.search_places("talca")] == [("Talca", "comuna")]

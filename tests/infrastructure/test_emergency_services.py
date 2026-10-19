"""Servicios de emergencia desde OpenStreetMap y búsqueda del más cercano."""

from __future__ import annotations

from src.domain.value_objects.coordinates import Coordinates
from src.domain.value_objects.emergency_service import EmergencyService, ServiceKind
from src.infrastructure.external_apis.overpass_client import elements_to_services
from src.infrastructure.geodata import InMemoryEmergencyServiceFinder

TALCA = Coordinates(latitude=-35.4264, longitude=-71.6554)


def test_classifies_and_reads_phone_and_center() -> None:
    elements = [
        {"type": "node", "lat": -35.42, "lon": -71.66, "tags": {"amenity": "police", "name": "3 Comisaría Talca", "phone": "712571146; 712571100"}},
        {"type": "way", "center": {"lat": -35.43, "lon": -71.65}, "tags": {"amenity": "fire_station"}},
        {"type": "node", "lat": -35.44, "lon": -71.64, "tags": {"amenity": "clinic", "name": "CESFAM Las Américas"}},
        {"type": "node", "lat": -35.44, "lon": -71.64, "tags": {"amenity": "clinic", "name": "Clínica Dental Sonrisa"}},
        {"type": "node", "lat": -35.45, "lon": -71.63, "tags": {"amenity": "townhall", "name": "Municipalidad de Talca", "contact:phone": "+56 71 2203 000"}},
    ]
    services = elements_to_services(elements)
    assert [(s.kind, s.name, s.phone) for s in services] == [
        (ServiceKind.CARABINEROS, "3 Comisaría Talca", "712571146"),
        (ServiceKind.BOMBEROS, "Bomberos", None),
        (ServiceKind.SALUD, "CESFAM Las Américas", None),
        (ServiceKind.MUNICIPALIDAD, "Municipalidad de Talca", "+56 71 2203 000"),
    ]


def service(kind: ServiceKind, km_north: float, phone: str | None = None) -> EmergencyService:
    return EmergencyService(
        kind=kind,
        name=f"{kind.value} {km_north}",
        phone=phone,
        location=Coordinates(latitude=TALCA.latitude + km_north / 111.0, longitude=TALCA.longitude),
    )


async def test_nearest_of_each_kind_within_radius() -> None:
    finder = InMemoryEmergencyServiceFinder(
        [
            service(ServiceKind.CARABINEROS, 5.0),
            service(ServiceKind.CARABINEROS, 2.0),
            service(ServiceKind.BOMBEROS, 60.0),  # fuera del radio
            service(ServiceKind.SALUD, 3.0),
        ]
    )
    found = await finder.find_nearest(TALCA, max_km=40.0)
    assert [(s.kind, round(d)) for s, d in found] == [(ServiceKind.CARABINEROS, 2), (ServiceKind.SALUD, 3)]


async def test_prefers_a_close_one_with_phone() -> None:
    finder = InMemoryEmergencyServiceFinder(
        [service(ServiceKind.CARABINEROS, 2.0), service(ServiceKind.CARABINEROS, 2.3, phone="712571146")]
    )
    (best, _), = await finder.find_nearest(TALCA)
    assert best.phone == "712571146"


def test_refuges_mark_tsunami_points_and_name_shelters() -> None:
    from src.infrastructure.external_apis.overpass_client import elements_to_refuges

    refuges = elements_to_refuges(
        [
            {"type": "node", "lat": -33.04, "lon": -71.62, "tags": {"emergency": "assembly_point", "assembly_point:tsunami": "yes"}},
            {"type": "way", "center": {"lat": -33.05, "lon": -71.61}, "tags": {"social_facility": "shelter", "name": "Albergue Municipal"}},
        ]
    )
    assert [(r.kind, r.name, r.detail) for r in refuges] == [
        (ServiceKind.PUNTO_ENCUENTRO, "Punto de encuentro", "tsunami"),
        (ServiceKind.ALBERGUE, "Albergue Municipal", None),
    ]


def test_refuge_finder_sorts_by_distance_within_radius() -> None:
    from src.infrastructure.geodata import InMemoryRefugeFinder

    finder = InMemoryRefugeFinder(
        [service(ServiceKind.ALBERGUE, 6.0), service(ServiceKind.PUNTO_ENCUENTRO, 1.0), service(ServiceKind.ALBERGUE, 25.0)]
    )
    assert [round(d) for _, d in finder.find_near(TALCA, radius_km=10)] == [1, 6]

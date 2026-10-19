"""Descarga desde OpenStreetMap las comunas y la red hidrográfica de las 16 regiones de Chile.

Guarda todo en ``data/chile/``: ``comunas.geojson`` y un
``waterways_<código>.geojson`` por región, con trazados simplificados, y
``servicios_<código>.json`` (Carabineros, Bomberos, salud y municipalidades).
Se puede interrumpir y volver a ejecutar: salta lo que ya está descargado.

Uso (una vez; tarda de 30 a 90 minutos según la carga de Overpass)::

    python scripts/import_chile.py              # todas las regiones
    python scripts/import_chile.py CL-ML CL-AT  # sólo algunas
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.domain.value_objects.admin_area import CHILE_REGIONS, AdminArea  # noqa: E402
from src.infrastructure.exceptions import ExternalServiceError  # noqa: E402
from src.infrastructure.external_apis.overpass_client import (  # noqa: E402
    fetch_region_comunas,
    fetch_region_localities,
    fetch_region_refuges,
    fetch_region_services,
    fetch_region_waterways,
    to_feature_collection,
)
from src.infrastructure.geodata import (  # noqa: E402
    COMUNAS_FILE,
    DATA_DIR,
    comunas_to_feature_collection,
    load_comunas,
    localities_file,
    localities_to_json,
    refuges_file,
    services_file,
    services_to_json,
    waterways_file,
)

PAUSE_S = 20.0
"""Pausa entre consultas: Overpass es un servicio comunitario."""
RETRIES = 3


async def with_retries(label: str, factory):  # type: ignore[no-untyped-def]
    for attempt in range(1, RETRIES + 1):
        try:
            return await factory()
        except (ExternalServiceError, httpx.HTTPError, ValueError) as exc:
            wait = 60.0 * attempt
            print(f"  {label}: falló ({exc!s:.120}); reintento {attempt}/{RETRIES} en {wait:.0f}s", flush=True)
            await asyncio.sleep(wait)
    raise SystemExit(f"{label}: Overpass no respondió tras {RETRIES} intentos; vuelva a ejecutar más tarde.")


def write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


async def main(codes: list[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    comunas: dict[str, list[AdminArea]] = {}
    for area in load_comunas():
        comunas.setdefault(area.region_code, []).append(area)

    async with httpx.AsyncClient() as http:
        for code in codes:
            name = CHILE_REGIONS[code][0]
            print(f"== {code} {name}", flush=True)
            if code not in comunas:
                areas = await with_retries("comunas", lambda: fetch_region_comunas(http, code, name))
                comunas[code] = areas
                write(COMUNAS_FILE, comunas_to_feature_collection([a for v in comunas.values() for a in v]))
                print(f"  {len(areas)} comunas", flush=True)
                await asyncio.sleep(PAUSE_S)
            out = waterways_file(code)
            if not out.exists():
                courses = await with_retries("cauces", lambda: fetch_region_waterways(http, code, name))
                write(out, to_feature_collection(courses))
                kinds = Counter(c.kind.value for c in courses)
                print(f"  {len(courses)} cauces, {out.stat().st_size / 1e6:.1f} MB: {dict(kinds)}", flush=True)
                await asyncio.sleep(PAUSE_S)
            out = services_file(code)
            if not out.exists():
                services = await with_retries("servicios", lambda: fetch_region_services(http, code))
                write(out, services_to_json(services))
                kinds = Counter(s.kind.value for s in services)
                phones = sum(1 for s in services if s.phone)
                print(f"  {len(services)} servicios de emergencia ({phones} con teléfono): {dict(kinds)}", flush=True)
                await asyncio.sleep(PAUSE_S / 2)
            out = refuges_file(code)
            if not out.exists():
                refuges = await with_retries("refugios", lambda: fetch_region_refuges(http, code))
                write(out, services_to_json(refuges))
                kinds = Counter(r.kind.value for r in refuges)
                print(f"  {len(refuges)} refugios y puntos de encuentro: {dict(kinds)}", flush=True)
                await asyncio.sleep(PAUSE_S / 2)
            out = localities_file(code)
            if not out.exists():
                localities = await with_retries("localidades", lambda: fetch_region_localities(http, code))
                write(out, localities_to_json(localities))
                print(f"  {len(localities)} localidades", flush=True)
                await asyncio.sleep(PAUSE_S / 2)

    total_mb = sum(p.stat().st_size for p in DATA_DIR.glob("waterways_*.geojson")) / 1e6
    print(f"Listo: {sum(len(v) for v in comunas.values())} comunas; red hidrográfica {total_mb:.0f} MB")


if __name__ == "__main__":
    selected = sys.argv[1:] or list(CHILE_REGIONS)
    unknown = [c for c in selected if c not in CHILE_REGIONS]
    if unknown:
        raise SystemExit(f"Códigos desconocidos: {unknown}. Válidos: {', '.join(CHILE_REGIONS)}")
    asyncio.run(main(selected))

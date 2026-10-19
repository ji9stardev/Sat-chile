"""Descarga la red hidrográfica del Maule desde OpenStreetMap y la guarda en ``data/``.

Uso (una vez; luego ``python -m src.infrastructure.database.seed`` la carga en MongoDB)::

    python scripts/import_osm_waterways.py
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

from src.infrastructure.external_apis.overpass_client import (  # noqa: E402
    fetch_maule_waterways,
    to_feature_collection,
)

OUT = ROOT / "data" / "maule_waterways.geojson"


async def main() -> None:
    print("Descargando cauces del Maule desde OpenStreetMap (1-3 minutos)…")
    async with httpx.AsyncClient() as http:
        courses = await fetch_maule_waterways(http)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(to_feature_collection(courses), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    kinds = Counter(c.kind.value for c in courses)
    print(f"{len(courses)} cauces guardados en {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB)")
    for kind, n in kinds.most_common():
        print(f"  {kind:9} {n}")


if __name__ == "__main__":
    asyncio.run(main())

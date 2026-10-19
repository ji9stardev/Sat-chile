"""Genera los íconos PNG de la PWA (semáforo sobre fondo negro) sin dependencias externas.

Uso::

    python scripts/generate_icons.py

Dibuja con funciones de distancia con signo (SDF) y antialiasing analítico,
y escribe PNG con ``zlib`` de la biblioteca estándar.
"""

from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Callable
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "presentation" / "pwa" / "icons"

RGBA = tuple[int, int, int, int]
BLACK: RGBA = (0, 0, 0, 255)
BODY: RGBA = (31, 35, 43, 255)
RED: RGBA = (239, 68, 68, 255)
AMBER: RGBA = (250, 204, 21, 255)
GREEN: RGBA = (34, 197, 94, 255)
WHITE: RGBA = (255, 255, 255, 255)
CLEAR: RGBA = (0, 0, 0, 0)


def sd_round_rect(x: float, y: float, cx: float, cy: float, hw: float, hh: float, r: float) -> float:
    qx, qy = abs(x - cx) - hw + r, abs(y - cy) - hh + r
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def sd_circle(x: float, y: float, cx: float, cy: float, r: float) -> float:
    return math.hypot(x - cx, y - cy) - r


def coverage(d: float, px: float) -> float:
    """Fracción del píxel cubierta por la forma (antialiasing lineal de 1 px)."""
    return max(0.0, min(1.0, 0.5 - d / px))


def over(dst: RGBA, src: RGBA, a: float) -> RGBA:
    sa = src[3] / 255 * a
    da = dst[3] / 255
    out_a = sa + da * (1 - sa)
    if out_a == 0:
        return CLEAR
    ch = [round((src[i] * sa + dst[i] * da * (1 - sa)) / out_a) for i in range(3)]
    return (ch[0], ch[1], ch[2], round(out_a * 255))


Shape = tuple[Callable[[float, float], float], RGBA]


def traffic_light(scale: float) -> list[Shape]:
    """Semáforo en coordenadas normalizadas [0,1]; ``scale`` lo encoge hacia el centro."""

    def s(v: float) -> float:
        return 0.5 + (v - 0.5) * scale

    body_hw, body_hh = 0.20 * scale, 0.40 * scale
    lamp_r = 0.115 * scale
    return [
        (lambda x, y: sd_round_rect(x, y, 0.5, 0.5, body_hw, body_hh, 0.12 * scale), BODY),
        (lambda x, y: sd_circle(x, y, 0.5, s(0.25), lamp_r), RED),
        (lambda x, y: sd_circle(x, y, 0.5, s(0.50), lamp_r), AMBER),
        (lambda x, y: sd_circle(x, y, 0.5, s(0.75), lamp_r), GREEN),
    ]


def render(size: int, shapes: list[Shape], background: Shape | None) -> list[list[RGBA]]:
    px = 1.0 / size
    rows: list[list[RGBA]] = []
    layers = ([background] if background else []) + shapes
    for j in range(size):
        y = (j + 0.5) * px
        row: list[RGBA] = []
        for i in range(size):
            x = (i + 0.5) * px
            color = CLEAR
            for sdf, fill in layers:
                a = coverage(sdf(x, y), px)
                if a > 0:
                    color = over(color, fill, a)
            row.append(color)
        rows.append(row)
    return rows


def write_png(path: Path, rows: list[list[RGBA]]) -> None:
    height, width = len(rows), len(rows[0])
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    print(f"{path.relative_to(OUT.parent.parent.parent.parent)}  {width}x{height}  {len(png)} B")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rounded_bg: Shape = (lambda x, y: sd_round_rect(x, y, 0.5, 0.5, 0.5, 0.5, 0.18), BLACK)
    full_bg: Shape = (lambda x, y: -1.0, BLACK)

    for size in (192, 512):
        write_png(OUT / f"icon-{size}.png", render(size, traffic_light(1.0), rounded_bg))
    # Maskable: fondo completo y contenido dentro de la zona segura (círculo del 80 %).
    write_png(OUT / "icon-maskable-512.png", render(512, traffic_light(0.72), full_bg))
    # iOS no admite transparencia en el ícono de inicio.
    write_png(OUT / "apple-touch-icon.png", render(180, traffic_light(0.86), full_bg))
    # Badge Android: sólo se usa el canal alfa (silueta blanca con luces caladas).
    badge = [(traffic_light(1.0)[0][0], WHITE)] + [(sdf, CLEAR) for sdf, _ in traffic_light(1.0)[1:]]
    rows = render(72, badge[:1], None)
    holes = render(72, [(sdf, WHITE) for sdf, _ in badge[1:]], None)
    rows = [
        [(255, 255, 255, max(0, b[3] - h[3])) for b, h in zip(brow, hrow, strict=True)]
        for brow, hrow in zip(rows, holes, strict=True)
    ]
    write_png(OUT / "badge-72.png", rows)


if __name__ == "__main__":
    main()

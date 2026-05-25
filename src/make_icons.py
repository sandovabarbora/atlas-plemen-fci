"""Generate PWA app icons as PNGs with no third-party dependencies.

Draws the brand olive background with a cream paw-print motif at 192 and 512 px.
Pure stdlib (zlib + struct) PNG writer, so CI needs nothing extra. The icons are
intentionally simple placeholders matching the Czech-editorial palette; replace
with artwork later if desired.

Usage:
    python src/make_icons.py
"""
from __future__ import annotations

import logging
import struct
import zlib
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("make-icons")

OLIVE = (107, 125, 58)   # #6b7d3a
CREAM = (244, 237, 224)  # #f4ede0


def _write_png(path: Path, pixels: list[list[tuple[int, int, int]]]) -> None:
    """Write an RGB PNG from a 2D list of (r, g, b) pixels."""
    height = len(pixels)
    width = len(pixels[0])
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter type 0 (None) per scanline
        for r, g, b in row:
            raw.extend((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


def _render(size: int) -> list[list[tuple[int, int, int]]]:
    """Render the paw motif: cream pad + four toes on an olive field."""
    cx, cy = size / 2, size * 0.58
    pad_r = size * 0.20
    toe_r = size * 0.085
    toes = [
        (size * 0.30, size * 0.34),
        (size * 0.44, size * 0.26),
        (size * 0.58, size * 0.28),
        (size * 0.70, size * 0.38),
    ]
    pixels: list[list[tuple[int, int, int]]] = []
    for y in range(size):
        row: list[tuple[int, int, int]] = []
        for x in range(size):
            cream = (x - cx) ** 2 + (y - cy) ** 2 <= pad_r ** 2
            if not cream:
                for tx, ty in toes:
                    if (x - tx) ** 2 + (y - ty) ** 2 <= toe_r ** 2:
                        cream = True
                        break
            row.append(CREAM if cream else OLIVE)
        pixels.append(row)
    return pixels


def main() -> int:
    out_dir = Path("web/icons")
    out_dir.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        path = out_dir / f"icon-{size}.png"
        _write_png(path, _render(size))
        logger.info("Zapsáno %s (%d×%d)", path, size, size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

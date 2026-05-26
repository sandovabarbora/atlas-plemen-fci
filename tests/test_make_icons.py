"""Tests for the pure-stdlib PNG icon generator."""
from __future__ import annotations

from pathlib import Path

from src import make_icons as mi


def test_render_dimensions_and_palette() -> None:
    px = mi._render(16)
    assert len(px) == 16
    assert all(len(row) == 16 for row in px)
    colors = {c for row in px for c in row}
    # Both brand colours must appear (olive field + cream paw).
    assert mi.OLIVE in colors
    assert mi.CREAM in colors


def test_write_png_signature(tmp_path: Path) -> None:
    out = tmp_path / "icon.png"
    mi._write_png(out, mi._render(8))
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic number
    assert b"IHDR" in data[:32]
    assert data[-8:-4] == b"IEND" or b"IEND" in data[-12:]

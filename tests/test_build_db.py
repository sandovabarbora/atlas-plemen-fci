"""Tests for the HTML build step."""
from __future__ import annotations

import json
from pathlib import Path

from src import build_db
from tests.conftest import FIXTURES


def _write_template(tmp_path: Path, body: str) -> Path:
    template = tmp_path / "template.html"
    template.write_text(body, encoding="utf-8")
    return template


def test_build_injects_database(tmp_path: Path) -> None:
    template = _write_template(tmp_path, "const BREED_DB = __BREED_DATABASE__;\n")
    out = tmp_path / "index.html"
    rc = build_db.build(template, FIXTURES / "sample_breeds.json", [out])
    assert rc == 0
    rendered = out.read_text(encoding="utf-8")
    assert "__BREED_DATABASE__" not in rendered
    # The injected literal must be valid JSON.
    literal = rendered.split("const BREED_DB = ", 1)[1].rsplit(";", 1)[0]
    data = json.loads(literal)
    assert len(data["breeds"]) == 3


def test_build_rejects_missing_placeholder(tmp_path: Path) -> None:
    template = _write_template(tmp_path, "no placeholder here\n")
    rc = build_db.build(template, FIXTURES / "sample_breeds.json", [tmp_path / "o.html"])
    assert rc == 1


def test_build_rejects_duplicate_placeholder(tmp_path: Path) -> None:
    template = _write_template(tmp_path, "__BREED_DATABASE__ __BREED_DATABASE__")
    rc = build_db.build(template, FIXTURES / "sample_breeds.json", [tmp_path / "o.html"])
    assert rc == 1


def test_build_missing_db(tmp_path: Path) -> None:
    template = _write_template(tmp_path, "__BREED_DATABASE__")
    rc = build_db.build(template, tmp_path / "nope.json", [tmp_path / "o.html"])
    assert rc == 1

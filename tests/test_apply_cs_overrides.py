"""Tests for applying Czech-name overrides."""
from __future__ import annotations

import json
from pathlib import Path

from src import apply_cs_overrides as aco
from tests.conftest import FIXTURES


def test_load_overrides(tmp_path: Path) -> None:
    csv_path = tmp_path / "ov.csv"
    csv_path.write_text("id,cs_corrected,reason\nbreed_000,Nový název,oprava\n,,skip\n",
                        encoding="utf-8")
    overrides = aco.load_overrides(csv_path)
    assert overrides == {"breed_000": "Nový název"}


def test_load_overrides_missing_file(tmp_path: Path) -> None:
    assert aco.load_overrides(tmp_path / "nope.csv") == {}


def test_apply_overrides_changes_name() -> None:
    db = json.loads((FIXTURES / "sample_breeds.json").read_text(encoding="utf-8"))
    changes = aco.apply_overrides(db, {"breed_000": "Briard (opraveno)"})
    assert changes == [("breed_000", "Briard", "Briard (opraveno)")]
    assert db["breeds"][0]["cs"] == "Briard (opraveno)"


def test_apply_overrides_noop_when_same() -> None:
    db = json.loads((FIXTURES / "sample_breeds.json").read_text(encoding="utf-8"))
    changes = aco.apply_overrides(db, {"breed_000": "Briard"})
    assert changes == []


def test_apply_overrides_unknown_id_ignored() -> None:
    db = json.loads((FIXTURES / "sample_breeds.json").read_text(encoding="utf-8"))
    changes = aco.apply_overrides(db, {"breed_999": "X"})
    assert changes == []

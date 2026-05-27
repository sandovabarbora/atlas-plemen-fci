"""Tests for ČMKU link enrichment + Czech-name comparison (no network)."""
from __future__ import annotations

import csv
import json

from src import enrich_cmku as ec
from tests.conftest import FIXTURES


def _rows() -> list[ec.CmkuFull]:
    return [
        ec.CmkuFull(113, "Briard", "Briard", 10,
                    "https://cmku/10", "https://cmku/10-briard.pdf"),
        ec.CmkuFull(148, "Jezevčík", "Dachshund", 20,
                    "https://cmku/20", "https://cmku/20-jezevcik.pdf"),
    ]


def _indexed(rows):
    return [(r, ec._row_keys(r)) for r in rows]


def test_match_exact() -> None:
    breed = {"id": "b", "cs": "Briard", "en": "Berger de Brie", "group": 1}
    row = ec.match_row(breed, _indexed(_rows()))
    assert row is not None and row.fci_number == 113


def test_match_variant_to_base() -> None:
    breed = {"id": "b", "cs": "Jezevčík standardní krátkosrstý",
             "en": "Dachshund Standard Smooth", "group": 4}
    row = ec.match_row(breed, _indexed(_rows()))
    assert row is not None and row.fci_number == 148


def test_match_none_when_unrelated() -> None:
    breed = {"id": "b", "cs": "Úplně jiné", "en": "Totally Different", "group": 1}
    assert ec.match_row(breed, _indexed(_rows())) is None


def test_enrich_attaches_links_and_flags_name_diff() -> None:
    db = json.loads((FIXTURES / "sample_breeds.json").read_text(encoding="utf-8"))
    # sample breed_000 cs is "Briard" -> matches exactly (no name diff)
    summary = ec.enrich(db, _rows(), verify=False)
    briard = next(b for b in db["breeds"] if b["id"] == "breed_000")
    assert briard["cmku_popis_pdf"] == "https://cmku/10-briard.pdf"
    assert briard["cmku_url"] == "https://cmku/10"

    # breed_001 cs "Jezevčík standardní krátkosrstý" differs from ČMKU "Jezevčík"
    diffs = {d[0] for d in summary.name_diffs}
    assert "breed_001" in diffs
    assert summary.matched >= 2


def test_load_cmku_full(tmp_path) -> None:
    p = tmp_path / "cmku.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["fci_number", "cs", "en", "list_id", "cmku_url", "cmku_popis_pdf"])
        w.writerow([113, "Briard", "Briard", 10, "u", "p.pdf"])
    rows = ec.load_cmku_full(p)
    assert len(rows) == 1 and rows[0].list_id == 10

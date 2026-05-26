"""Tests for the Czech-name verification (mocked network)."""
from __future__ import annotations

from pathlib import Path

from src import verify_cs_names as vcn


def test_nfd_lower_equality() -> None:
    assert vcn.nfd_lower("Bígl") == vcn.nfd_lower("bígl")
    assert vcn.nfd_lower("Briard") != vcn.nfd_lower("Briár")


def _patch_fetch(monkeypatch, routes: dict[tuple[str, str], dict | None]) -> None:
    def fake(lang: str, title: str, timeout: float = 15.0):
        return routes.get((lang, title))
    monkeypatch.setattr(vcn, "fetch_summary", fake)
    monkeypatch.setattr(vcn.time, "sleep", lambda *_: None)


def test_review_exact_match(monkeypatch) -> None:
    breed = {"id": "b", "cs": "Bígl", "en": "Beagle", "group": 6, "fci_number": 161}
    _patch_fetch(monkeypatch, {
        ("cs", "Bígl"): {"type": "standard", "titles": {"normalized": "Bígl"}},
    })
    r = vcn.review_breed(breed, sleep_seconds=0)
    assert r.cs_match is True
    assert r.cs_wikipedia == "Bígl"
    assert r.notes == ""


def test_review_redirect_normalized(monkeypatch) -> None:
    breed = {"id": "b", "cs": "Labrador", "en": "Labrador Retriever", "group": 8}
    _patch_fetch(monkeypatch, {
        ("cs", "Labrador"): {"type": "standard",
                             "titles": {"normalized": "Labradorský retrívr"}},
    })
    r = vcn.review_breed(breed, sleep_seconds=0)
    assert r.cs_match is False
    assert r.cs_wikipedia == "Labradorský retrívr"
    assert r.notes == "redirect/normalized"


def test_review_cs_404_falls_back_to_en(monkeypatch) -> None:
    breed = {"id": "b", "cs": "Neznámé plemeno", "en": "Some Breed", "group": 1}
    _patch_fetch(monkeypatch, {
        ("cs", "Neznámé_plemeno"): None,  # not used: fake ignores underscores
        ("cs", "Neznámé plemeno"): None,
        ("en", "Some Breed"): {"titles": {"canonical": "Some_Breed"}},
    })
    r = vcn.review_breed(breed, sleep_seconds=0)
    assert r.cs_match is False
    assert "cs 404" in r.notes
    assert "Some Breed" in r.notes


def test_write_report(tmp_path: Path) -> None:
    reviews = [
        vcn.NameReview("b0", "Bígl", "Bígl", True, "Beagle", "161", ""),
        vcn.NameReview("b1", "X", "", False, "Y", "", "cs 404"),
    ]
    out = tmp_path / "review.csv"
    vcn.write_report(reviews, out)
    text = out.read_text(encoding="utf-8")
    assert "id,cs_current,cs_wikipedia,cs_match,en,fci_number,notes" in text
    assert "b0,Bígl,Bígl,True,Beagle,161," in text

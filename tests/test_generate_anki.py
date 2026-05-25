"""Tests for the Anki deck generator (card structure, no real downloads)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tests.conftest import FIXTURES, REPO_ROOT


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "generate_anki", REPO_ROOT / "anki" / "generate_anki.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ga = _load_module()


def test_to_roman() -> None:
    assert ga.to_roman(1) == "I"
    assert ga.to_roman(8) == "VIII"
    assert ga.to_roman(11) == "XI"


def _db() -> dict:
    return json.loads((FIXTURES / "sample_breeds.json").read_text(encoding="utf-8"))


def test_build_deck_text_only_makes_three_cards_per_breed() -> None:
    db = _db()
    deck, media = ga.build_deck(db, fetcher=None, filter_groups=None, limit=None)
    assert len(deck.notes) == len(db["breeds"]) * 3
    assert media == []


def test_build_deck_filters_groups() -> None:
    db = _db()
    deck, _ = ga.build_deck(db, fetcher=None, filter_groups={4}, limit=None)
    # Only the single group-4 dachshund row -> 3 cards.
    assert len(deck.notes) == 3


def test_build_deck_limit() -> None:
    db = _db()
    deck, _ = ga.build_deck(db, fetcher=None, filter_groups=None, limit=1)
    assert len(deck.notes) == 3


def test_build_deck_with_fake_fetcher_attaches_media(tmp_path: Path) -> None:
    db = _db()
    # Minimal fake fetcher: returns a real on-disk file for every breed.
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"img")

    class FakeResult:
        local_path = img

        @property
        def ok(self) -> bool:
            return True

    class FakeFetcher:
        def fetch(self, breed: dict) -> FakeResult:
            return FakeResult()

    deck, media = ga.build_deck(db, fetcher=FakeFetcher(), filter_groups=None, limit=None)
    assert len(media) == len(db["breeds"])
    assert all(p == img for p in media)

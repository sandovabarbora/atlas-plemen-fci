"""Tests for the shared breed-photo fetcher (no real network)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src import photo_fetcher as pf


class FakeResponse:
    def __init__(self, status_code: int, *, json_data: dict | None = None,
                 content: bytes = b"", content_type: str = "image/jpeg") -> None:
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.headers = {"Content-Type": content_type}

    def json(self) -> dict:
        assert self._json is not None
        return self._json


class FakeSession:
    """Routes GETs by URL to canned responses and counts calls."""

    def __init__(self, routes: dict[str, FakeResponse]) -> None:
        self.headers: dict[str, str] = {}
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, timeout: int = 0) -> FakeResponse:
        self.calls.append(url)
        for key, resp in self.routes.items():
            if key in url:
                return resp
        return FakeResponse(404)


@pytest.mark.parametrize(
    "content_type, expected",
    [
        ("image/jpeg", "jpg"),
        ("image/png", "png"),
        ("image/webp", "webp"),
        ("image/gif", "gif"),
        ("image/svg+xml", None),
        ("", "jpg"),
    ],
)
def test_ext_from_content_type(content_type: str, expected: str | None) -> None:
    assert pf._ext_from_content_type(content_type) == expected


def _fetcher(tmp_path: Path, routes: dict[str, FakeResponse]) -> pf.BreedPhotoFetcher:
    f = pf.BreedPhotoFetcher(tmp_path / "cache", user_agent="test", sleep_seconds=0.0)
    f.session = FakeSession(routes)  # type: ignore[assignment]
    return f


def test_fetch_prefers_fci_illustration(tmp_path: Path) -> None:
    breed = {"id": "breed_000", "cs": "Labrador", "en": "Labrador Retriever",
             "fci_illustration_url": "https://fci/122g08.jpg",
             "fci_url": "https://fci/page"}
    f = _fetcher(tmp_path, {"122g08.jpg": FakeResponse(200, content=b"img")})
    result = f.fetch(breed)
    assert result.source == "fci"
    assert result.ok
    assert result.local_path.name == "breed_000_fci.jpg"
    assert result.page_url == "https://fci/page"


def test_fetch_prefers_curated_photo_over_fci(tmp_path: Path) -> None:
    breed = {"id": "breed_146", "cs": "Jezevčík králičí krátkosrstý", "en": "Dachshund",
             "photo_url": "https://commons/smooth.jpg",
             "fci_illustration_url": "https://fci/148g04.jpg"}
    f = _fetcher(tmp_path, {
        "commons/smooth.jpg": FakeResponse(200, content=b"img"),
        "fci/148g04.jpg": FakeResponse(200, content=b"drawing"),
    })
    result = f.fetch(breed)
    assert result.source == "photo"
    assert result.local_path.name == "breed_146_photo.jpg"


def test_fetch_falls_back_to_wikipedia(tmp_path: Path) -> None:
    breed = {"id": "breed_001", "cs": "Pitbull", "en": "Pit Bull"}  # no FCI url
    routes = {
        "cs.wikipedia.org": FakeResponse(200, json_data={
            "type": "standard",
            "thumbnail": {"source": "https://upload/250px-pit.jpg"},
            "content_urls": {"desktop": {"page": "https://cs.wiki/Pitbull"}},
        }),
        "upload/250px-pit.jpg": FakeResponse(200, content=b"img"),
    }
    f = _fetcher(tmp_path, routes)
    result = f.fetch(breed)
    assert result.source == "cs.wiki"
    assert result.ok
    assert result.local_path.name == "breed_001_cs.jpg"


def test_fci_failure_falls_back_to_wiki(tmp_path: Path) -> None:
    breed = {"id": "breed_002", "cs": "Pes", "en": "Dog",
             "fci_illustration_url": "https://fci/999.jpg"}
    routes = {
        "fci/999.jpg": FakeResponse(500),  # FCI download fails
        "wikipedia.org": FakeResponse(200, json_data={
            "type": "standard",
            "thumbnail": {"source": "https://upload/x.jpg"},
            "content_urls": {"desktop": {"page": "https://cs.wiki/Pes"}},
        }),
        "upload/x.jpg": FakeResponse(200, content=b"img"),
    }
    f = _fetcher(tmp_path, routes)
    result = f.fetch(breed)
    assert result.ok
    assert result.source in ("cs.wiki", "en.wiki")


def test_fetch_uses_cache_on_second_call(tmp_path: Path) -> None:
    breed = {"id": "breed_000", "cs": "X", "en": "X",
             "fci_illustration_url": "https://fci/1.jpg"}
    f = _fetcher(tmp_path, {"fci/1.jpg": FakeResponse(200, content=b"img")})
    f.fetch(breed)
    calls_after_first = len(f.session.calls)  # type: ignore[attr-defined]
    f.fetch(breed)
    assert len(f.session.calls) == calls_after_first  # type: ignore[attr-defined]


def test_fetch_records_error_when_nothing_found(tmp_path: Path) -> None:
    breed = {"id": "breed_003", "cs": "Nic", "en": "Nothing"}
    f = _fetcher(tmp_path, {})  # every GET 404s
    result = f.fetch(breed)
    assert not result.ok
    assert result.error
    # Cached as error: a second call returns the same error without new GETs.
    f.session.calls.clear()  # type: ignore[attr-defined]
    f.fetch(breed)
    assert f.session.calls == []  # type: ignore[attr-defined]


def test_svg_is_rejected(tmp_path: Path) -> None:
    breed = {"id": "breed_004", "cs": "Y", "en": "Y",
             "fci_illustration_url": "https://fci/2.svg"}
    f = _fetcher(tmp_path, {"fci/2.svg": FakeResponse(200, content=b"<svg/>",
                                                      content_type="image/svg+xml")})
    result = f.fetch(breed)
    # SVG from FCI is rejected; with no wiki route it ends unmatched.
    assert not result.ok

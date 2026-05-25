"""Shared breed-photo cascade for the Anki generator (Python side).

Mirrors the BreedPhotos module in the quiz template: try the official FCI
illustration first (groups 1-10, when present), then Czech Wikipedia, then
English Wikipedia. Downloaded bytes are cached on disk and keyed by source so
an FCI photo and a Wikipedia photo for the same breed never collide
(``breed_000_fci.jpg`` vs ``breed_000_cs.jpg``).

Server-side there is no CORS constraint, so the FCI illustration is downloaded
directly with requests.

References:
    - Wikipedia REST API: https://en.wikipedia.org/api/rest_v1/
    - FCI illustrations: https://www.fci.be/en/Nomenclature/
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("photo-fetcher")


@dataclass
class PhotoResult:
    """Result of a photo fetch attempt."""

    breed_id: str
    local_path: Path | None = None
    source_url: str | None = None  # original image URL
    page_url: str | None = None  # provenance page (FCI nomenclature or wiki)
    source: str | None = None  # "fci" | "cs.wiki" | "en.wiki"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.local_path is not None and self.local_path.exists()


def _ext_from_content_type(content_type: str) -> str | None:
    """Map a Content-Type header to a file extension, or None if unusable."""
    content_type = content_type.lower()
    if "png" in content_type:
        return "png"
    if "webp" in content_type:
        return "webp"
    if "gif" in content_type:
        return "gif"
    if "svg" in content_type:
        return None  # SVG renders poorly on Anki mobile
    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"
    return "jpg"


class BreedPhotoFetcher:
    """Fetch and cache breed photos with an FCI-first cascade.

    Stateful by necessity: it holds an HTTP session and an on-disk cache, which
    justifies a class over plain functions.
    """

    def __init__(self, cache_dir: Path, user_agent: str, sleep_seconds: float = 0.3) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = cache_dir / "_metadata.json"
        self.metadata: dict[str, dict[str, Any]] = self._load_metadata()
        self.sleep_seconds = sleep_seconds
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def _load_metadata(self) -> dict[str, dict[str, Any]]:
        if self.metadata_file.exists():
            try:
                return json.loads(self.metadata_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warning("Cache metadata corrupted, starting fresh")
        return {}

    def _save_metadata(self) -> None:
        self.metadata_file.write_text(
            json.dumps(self.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def fetch(self, breed: dict[str, Any]) -> PhotoResult:
        """Fetch a photo for one breed, using the cache when possible."""
        breed_id = breed["id"]

        cached = self.metadata.get(breed_id)
        if cached is not None:
            if cached.get("error"):
                return PhotoResult(breed_id=breed_id, error=cached["error"])
            local_path = self.cache_dir / cached["filename"]
            if local_path.exists():
                return PhotoResult(
                    breed_id=breed_id, local_path=local_path,
                    source_url=cached.get("source_url"), page_url=cached.get("page_url"),
                    source=cached.get("source"),
                )
            logger.info("  cache miss (file gone) for %s", breed["cs"])

        result = self._fetch_fresh(breed)

        if result.ok:
            self.metadata[breed_id] = {
                "filename": result.local_path.name,  # type: ignore[union-attr]
                "source_url": result.source_url,
                "page_url": result.page_url,
                "source": result.source,
            }
        else:
            self.metadata[breed_id] = {"error": result.error or "no image found"}
        self._save_metadata()
        return result

    def _fetch_fresh(self, breed: dict[str, Any]) -> PhotoResult:
        breed_id = breed["id"]

        # 1. Official FCI illustration (groups 1-10 that have one).
        illustration = breed.get("fci_illustration_url")
        if illustration:
            result = self._download(
                breed_id, illustration, source="fci",
                page_url=breed.get("fci_url"), name_suffix="fci",
            )
            if result.ok:
                return result
            logger.info("  FCI illustration failed for %s (%s), falling back to wiki",
                        breed["cs"], result.error)

        # 2-5. Wikipedia cascade.
        attempts: list[tuple[str, str]] = [("cs", breed["cs"])]
        simplified_cs = breed["cs"].split("(")[0].split("—")[0].split(" - ")[0].strip()
        if simplified_cs != breed["cs"]:
            attempts.append(("cs", simplified_cs))
        attempts.append(("en", breed["en"]))
        simplified_en = breed["en"].split("(")[0].split("—")[0].split(" - ")[0].strip()
        if simplified_en != breed["en"]:
            attempts.append(("en", simplified_en))

        for lang, title in attempts:
            result = self._fetch_wiki(breed_id, lang, title)
            time.sleep(self.sleep_seconds)
            if result.ok:
                return result

        return PhotoResult(breed_id=breed_id, error="no image found")

    def _fetch_wiki(self, breed_id: str, lang: str, title: str) -> PhotoResult:
        try:
            encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
            summary_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"
            resp = self.session.get(summary_url, timeout=15)
            if resp.status_code != 200:
                return PhotoResult(breed_id=breed_id, error=f"HTTP {resp.status_code}")
            data = resp.json()
            if data.get("type") in ("disambiguation", "no-extract"):
                return PhotoResult(breed_id=breed_id, error="disambiguation")
            image_info = data.get("originalimage") or data.get("thumbnail")
            if not image_info or not image_info.get("source"):
                return PhotoResult(breed_id=breed_id, error="no image")
            page_url = data.get("content_urls", {}).get("desktop", {}).get("page")
            return self._download(
                breed_id, image_info["source"],
                source=f"{lang}.wiki", page_url=page_url, name_suffix=lang,
            )
        except requests.RequestException as e:
            return PhotoResult(breed_id=breed_id, error=str(e))

    def _download(
        self, breed_id: str, url: str, source: str, page_url: str | None, name_suffix: str
    ) -> PhotoResult:
        """Download image bytes to a source-keyed cache file."""
        try:
            resp = self.session.get(url, timeout=20)
            if resp.status_code != 200:
                return PhotoResult(breed_id=breed_id, error=f"image HTTP {resp.status_code}")
            ext = _ext_from_content_type(resp.headers.get("Content-Type", ""))
            if ext is None:
                return PhotoResult(breed_id=breed_id, error="SVG not supported")
            filename = f"{breed_id}_{name_suffix}.{ext}"
            local_path = self.cache_dir / filename
            local_path.write_bytes(resp.content)
            return PhotoResult(
                breed_id=breed_id, local_path=local_path, source_url=url,
                page_url=page_url, source=source,
            )
        except requests.RequestException as e:
            return PhotoResult(breed_id=breed_id, error=str(e))

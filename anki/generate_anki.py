"""Generate an Anki deck from the FCI breed database.

Builds three card types per breed:
1. Photo → název plemene
2. Photo → FCI skupina + sekce
3. Název → vše (skupina, sekce, varianty, země původu)

Photos are downloaded from Czech Wikipedia (fallback: English Wikipedia),
cached locally between runs, and embedded as Anki media.

Usage:
    pip install genanki requests
    python generate_anki.py

Optional flags:
    --no-images          Skip photo download — text-only cards (faster, smaller deck)
    --groups 1,2,3       Only include specific FCI groups (comma-separated)
    --limit N            Limit to first N breeds (for testing)
    --db PATH            Path to breeds.json (default: breeds.json in current dir)
    --output PATH        Output .apkg path (default: atlas-plemen-fci.apkg)
    --photo-dir PATH     Cache dir for downloaded photos (default: .photo_cache)
    --user-agent STR     User-Agent for Wikipedia requests
    --sleep FLOAT        Sleep between Wikipedia requests in seconds (default: 0.3)

References:
- genanki docs: https://github.com/kerrickstaley/genanki
- Wikipedia REST API: https://en.wikipedia.org/api/rest_v1/
- FCI nomenclature: https://www.fci.be/en/Nomenclature/
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import genanki  # type: ignore
except ImportError:
    print("Chybí balíček 'genanki'. Nainstaluj přes: pip install genanki requests", file=sys.stderr)
    sys.exit(1)

try:
    import requests  # type: ignore
except ImportError:
    print("Chybí balíček 'requests'. Nainstaluj přes: pip install requests", file=sys.stderr)
    sys.exit(1)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("anki-gen")


# Deterministic IDs — keep these stable across rebuilds so Anki updates cards
# instead of duplicating them. Generate once with random.randrange(1<<30, 1<<31).
DECK_ID = 1857394021
MODEL_PHOTO_TO_NAME_ID = 1857394022
MODEL_PHOTO_TO_GROUP_ID = 1857394023
MODEL_NAME_TO_ALL_ID = 1857394024


# ============================================================
# WIKIPEDIA IMAGE FETCHER (with on-disk cache)
# ============================================================

def to_roman(n: int) -> str:
    """Convert 1-11 to Roman numerals (used for FCI group labels)."""
    romans = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI"]
    return romans[n] if 0 <= n < len(romans) else str(n)


@dataclass
class PhotoResult:
    """Result of a photo fetch attempt."""
    breed_id: str
    local_path: Path | None  # None if not found
    source_url: str | None
    wiki_url: str | None
    lang: str | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.local_path is not None and self.local_path.exists()


class WikiPhotoFetcher:
    """Fetch and cache breed photos from Wikipedia REST API.

    Strategy: try Czech Wikipedia (Czech name) first, then simplified Czech name,
    then English Wikipedia (English name), then simplified English name.

    References:
        - REST API summary endpoint: https://en.wikipedia.org/api/rest_v1/page/summary/{title}
        - CORS for browsers: enabled with `access-control-allow-origin: *`
    """

    def __init__(
        self,
        cache_dir: Path,
        user_agent: str,
        sleep_seconds: float = 0.3,
    ):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = cache_dir / "_metadata.json"
        self.metadata: dict[str, dict[str, Any]] = self._load_metadata()
        self.user_agent = user_agent
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
            json.dumps(self.metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def fetch(self, breed: dict[str, Any]) -> PhotoResult:
        """Fetch photo for a single breed. Returns PhotoResult."""
        breed_id = breed["id"]

        # Check cache first
        if breed_id in self.metadata:
            meta = self.metadata[breed_id]
            if meta.get("error"):
                return PhotoResult(breed_id=breed_id, local_path=None, source_url=None,
                                   wiki_url=None, lang=None, error=meta["error"])
            local_path = self.cache_dir / meta["filename"]
            if local_path.exists():
                return PhotoResult(
                    breed_id=breed_id,
                    local_path=local_path,
                    source_url=meta.get("source_url"),
                    wiki_url=meta.get("wiki_url"),
                    lang=meta.get("lang"),
                )
            # File missing — re-fetch
            logger.info("  cache miss (file gone) for %s", breed["cs"])

        # Build list of (lang, title) attempts
        attempts: list[tuple[str, str]] = []
        attempts.append(("cs", breed["cs"]))
        # Simplified Czech: strip parenthetical, take part before dash/em-dash
        simplified_cs = breed["cs"].split("(")[0].split("—")[0].split(" - ")[0].strip()
        if simplified_cs != breed["cs"]:
            attempts.append(("cs", simplified_cs))
        attempts.append(("en", breed["en"]))
        simplified_en = breed["en"].split("(")[0].split("—")[0].split(" - ")[0].strip()
        if simplified_en != breed["en"]:
            attempts.append(("en", simplified_en))

        for lang, title in attempts:
            result = self._try_fetch(breed_id, lang, title)
            time.sleep(self.sleep_seconds)
            if result.ok:
                self.metadata[breed_id] = {
                    "filename": result.local_path.name,  # type: ignore[union-attr]
                    "source_url": result.source_url,
                    "wiki_url": result.wiki_url,
                    "lang": result.lang,
                }
                self._save_metadata()
                return result

        # All attempts failed
        self.metadata[breed_id] = {"error": "no image found"}
        self._save_metadata()
        return PhotoResult(breed_id=breed_id, local_path=None, source_url=None,
                           wiki_url=None, lang=None, error="no image found")

    def _try_fetch(self, breed_id: str, lang: str, title: str) -> PhotoResult:
        """Try fetching a single (lang, title) combination."""
        try:
            encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
            summary_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"
            resp = self.session.get(summary_url, timeout=15)
            if resp.status_code != 200:
                return PhotoResult(breed_id, None, None, None, None,
                                   error=f"HTTP {resp.status_code}")
            data = resp.json()
            if data.get("type") in ("disambiguation", "no-extract"):
                return PhotoResult(breed_id, None, None, None, None, error="disambiguation")

            # Prefer originalimage for better Anki photos
            image_info = data.get("originalimage") or data.get("thumbnail")
            if not image_info or not image_info.get("source"):
                return PhotoResult(breed_id, None, None, None, None, error="no image")

            source_url = image_info["source"]
            wiki_url = data.get("content_urls", {}).get("desktop", {}).get("page")

            # Download the actual image bytes
            img_resp = self.session.get(source_url, timeout=20)
            if img_resp.status_code != 200:
                return PhotoResult(breed_id, None, None, None, None,
                                   error=f"image HTTP {img_resp.status_code}")

            # Determine file extension from Content-Type or URL
            content_type = img_resp.headers.get("Content-Type", "").lower()
            ext = "jpg"
            if "png" in content_type:
                ext = "png"
            elif "svg" in content_type:
                ext = "svg"
            elif "webp" in content_type:
                ext = "webp"
            elif "gif" in content_type:
                ext = "gif"

            # SVG is not great for Anki (mobile rendering issues)
            if ext == "svg":
                return PhotoResult(breed_id, None, None, None, None, error="SVG not supported")

            # Stable filename based on breed_id (so re-runs don't duplicate)
            filename = f"{breed_id}.{ext}"
            local_path = self.cache_dir / filename
            local_path.write_bytes(img_resp.content)

            return PhotoResult(
                breed_id=breed_id,
                local_path=local_path,
                source_url=source_url,
                wiki_url=wiki_url,
                lang=lang,
            )
        except requests.RequestException as e:
            return PhotoResult(breed_id, None, None, None, None, error=str(e))
        except Exception as e:
            logger.exception("Unexpected error fetching %s (%s, %s)", breed_id, lang, title)
            return PhotoResult(breed_id, None, None, None, None, error=str(e))


# ============================================================
# ANKI MODELS
# ============================================================

CARD_STYLE = """
.card {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 18px;
  text-align: center;
  color: #1f1b14;
  background-color: #f4ede0;
  padding: 16px;
}
.cs-name {
  font-family: Georgia, serif;
  font-size: 1.6em;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin-bottom: 6px;
  color: #1f1b14;
}
.en-name {
  font-style: italic;
  color: #6e6a55;
  font-size: 0.85em;
  margin-bottom: 18px;
}
.photo img {
  max-width: 100%;
  max-height: 360px;
  border-radius: 4px;
  border: 1px solid #cdc1a3;
  box-shadow: 0 2px 8px rgba(31,27,20,0.08);
}
.label {
  font-size: 0.7em;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: #6e6a55;
  margin: 14px 0 4px;
}
.group {
  font-family: Georgia, serif;
  font-size: 1.2em;
  font-weight: 600;
  color: #4f5d23;
}
.section, .variants, .country {
  font-size: 0.95em;
  color: #1f1b14;
  margin-top: 2px;
}
.variants .pill {
  display: inline-block;
  background: #e1d6bc;
  padding: 2px 8px;
  border-radius: 99px;
  font-size: 0.78em;
  margin: 2px;
  color: #4d4636;
}
hr {
  border: none;
  border-top: 1px solid #cdc1a3;
  margin: 16px 0;
}
.question {
  font-family: Georgia, serif;
  font-size: 1.15em;
  font-weight: 500;
  margin-bottom: 14px;
  color: #1f1b14;
}
.placeholder {
  color: #8a7f64;
  font-style: italic;
  padding: 40px 20px;
  border: 1px dashed #cdc1a3;
  border-radius: 4px;
}
"""

MODEL_PHOTO_TO_NAME = genanki.Model(
    model_id=MODEL_PHOTO_TO_NAME_ID,
    name="FCI · Foto → název",
    fields=[
        {"name": "breed_id"},
        {"name": "cs_name"},
        {"name": "en_name"},
        {"name": "photo"},
        {"name": "group_roman"},
        {"name": "group_name"},
        {"name": "section_code"},
        {"name": "section_name"},
        {"name": "country"},
        {"name": "variants"},
    ],
    templates=[
        {
            "name": "Foto → název",
            "qfmt": """
                <div class="question">Které plemeno je na fotce?</div>
                <div class="photo">{{photo}}</div>
            """,
            "afmt": """
                {{FrontSide}}
                <hr>
                <div class="cs-name">{{cs_name}}</div>
                <div class="en-name">{{en_name}}</div>
                <div class="label">FCI skupina</div>
                <div class="group">{{group_roman}}. {{group_name}}</div>
                <div class="label">Sekce</div>
                <div class="section">{{section_code}} — {{section_name}}</div>
                <div class="label">Země původu</div>
                <div class="country">{{country}}</div>
                {{#variants}}
                <div class="label">Varianty</div>
                <div class="variants">{{variants}}</div>
                {{/variants}}
            """,
        }
    ],
    css=CARD_STYLE,
)


MODEL_PHOTO_TO_GROUP = genanki.Model(
    model_id=MODEL_PHOTO_TO_GROUP_ID,
    name="FCI · Foto → skupina + sekce",
    fields=[
        {"name": "breed_id"},
        {"name": "cs_name"},
        {"name": "en_name"},
        {"name": "photo"},
        {"name": "group_roman"},
        {"name": "group_name"},
        {"name": "section_code"},
        {"name": "section_name"},
        {"name": "country"},
    ],
    templates=[
        {
            "name": "Foto → skupina+sekce",
            "qfmt": """
                <div class="question">Která FCI skupina a sekce?</div>
                <div class="photo">{{photo}}</div>
                <div class="label" style="margin-top:14px">(nápověda: {{cs_name}})</div>
            """,
            "afmt": """
                {{FrontSide}}
                <hr>
                <div class="group">{{group_roman}}. {{group_name}}</div>
                <div class="label">Sekce</div>
                <div class="section">{{section_code}} — {{section_name}}</div>
                <div class="label">Země</div>
                <div class="country">{{country}}</div>
            """,
        }
    ],
    css=CARD_STYLE,
)


MODEL_NAME_TO_ALL = genanki.Model(
    model_id=MODEL_NAME_TO_ALL_ID,
    name="FCI · Název → vše",
    fields=[
        {"name": "breed_id"},
        {"name": "cs_name"},
        {"name": "en_name"},
        {"name": "photo"},
        {"name": "group_roman"},
        {"name": "group_name"},
        {"name": "section_code"},
        {"name": "section_name"},
        {"name": "country"},
        {"name": "variants"},
    ],
    templates=[
        {
            "name": "Název → vše",
            "qfmt": """
                <div class="question">Kam patří toto plemeno?</div>
                <div class="cs-name">{{cs_name}}</div>
                <div class="en-name">{{en_name}}</div>
            """,
            "afmt": """
                <div class="cs-name">{{cs_name}}</div>
                <div class="en-name">{{en_name}}</div>
                <div class="photo">{{photo}}</div>
                <hr>
                <div class="label">FCI skupina</div>
                <div class="group">{{group_roman}}. {{group_name}}</div>
                <div class="label">Sekce</div>
                <div class="section">{{section_code}} — {{section_name}}</div>
                <div class="label">Země původu</div>
                <div class="country">{{country}}</div>
                {{#variants}}
                <div class="label">Varianty</div>
                <div class="variants">{{variants}}</div>
                {{/variants}}
            """,
        }
    ],
    css=CARD_STYLE,
)


# ============================================================
# DECK BUILDER
# ============================================================

def build_deck(
    db: dict[str, Any],
    fetcher: WikiPhotoFetcher | None,
    filter_groups: set[int] | None,
    limit: int | None,
) -> tuple[genanki.Deck, list[Path]]:
    """Build the Anki deck. Returns (deck, list of media file paths)."""
    deck = genanki.Deck(deck_id=DECK_ID, name="Atlas plemen — FCI")
    media_files: list[Path] = []

    breeds = db["breeds"]
    if filter_groups:
        breeds = [b for b in breeds if b["group"] in filter_groups]
    if limit:
        breeds = breeds[:limit]

    logger.info("Generuji karty pro %d plemen…", len(breeds))

    successes = 0
    no_image = 0
    for idx, breed in enumerate(breeds, 1):
        logger.info("[%d/%d] %s", idx, len(breeds), breed["cs"])

        # Resolve photo
        photo_field = ""
        if fetcher:
            result = fetcher.fetch(breed)
            if result.ok:
                media_files.append(result.local_path)  # type: ignore[arg-type]
                photo_field = f'<img src="{result.local_path.name}">'  # type: ignore[union-attr]
                successes += 1
            else:
                no_image += 1
                photo_field = '<div class="placeholder">[fotka nedostupná]</div>'
        else:
            photo_field = '<div class="placeholder">(text-only režim)</div>'

        # Resolve section name
        section_name = db["sections"][str(breed["group"])][breed["section"]]
        group_name = db["group_names"][str(breed["group"])]
        group_roman = to_roman(breed["group"])

        # Variants as HTML pills
        variants_html = ""
        if breed.get("varieties"):
            variants_html = "".join(f'<span class="pill">{v}</span>' for v in breed["varieties"])

        # Common fields for all 3 card types
        common_fields = [
            breed["id"],
            breed["cs"],
            breed["en"],
            photo_field,
            group_roman,
            group_name,
            breed["section"],
            section_name,
            breed["country"] or "—",
        ]

        tags = [
            f"FCI_skupina_{breed['group']}",
            f"sekce_{breed['section']}",
            f"zeme_{(breed['country'] or 'neznama').replace(' ', '_').replace('/', '_')}",
        ]

        # Card 1: Photo → name
        deck.add_note(genanki.Note(
            model=MODEL_PHOTO_TO_NAME,
            fields=[*common_fields, variants_html],
            tags=tags,
        ))
        # Card 2: Photo → group + section
        deck.add_note(genanki.Note(
            model=MODEL_PHOTO_TO_GROUP,
            fields=common_fields,
            tags=tags,
        ))
        # Card 3: Name → everything
        deck.add_note(genanki.Note(
            model=MODEL_NAME_TO_ALL,
            fields=[*common_fields, variants_html],
            tags=tags,
        ))

    if fetcher:
        logger.info("Fotky: %d staženo, %d nedostupných", successes, no_image)
    return deck, media_files


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generuje Anki deck z databáze FCI plemen",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--db", type=Path, default=Path("data/breeds.json"),
                        help="Cesta k breeds.json (default: data/breeds.json od kořene repa)")
    parser.add_argument("--output", type=Path, default=Path("dist/atlas-plemen-fci.apkg"),
                        help="Výstupní .apkg soubor")
    parser.add_argument("--photo-dir", type=Path, default=Path(".photo_cache"),
                        help="Adresář pro cachování fotek")
    parser.add_argument("--no-images", action="store_true",
                        help="Bez fotek (jen text — rychlejší, menší)")
    parser.add_argument("--groups", type=str, default=None,
                        help="Filtr FCI skupin, např. '1,3,5'")
    parser.add_argument("--limit", type=int, default=None,
                        help="Omezit na prvních N plemen (pro testování)")
    parser.add_argument("--user-agent", type=str,
                        default="AtlasPlemen/1.0 (educational; contact via cmku.cz)",
                        help="User-Agent pro Wikipedia API")
    parser.add_argument("--sleep", type=float, default=0.3,
                        help="Pauza mezi voláními Wikipedia API (s)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.db.exists():
        logger.error("Soubor s databází nenalezen: %s", args.db)
        logger.error("Stáhni 'breeds.json' (přiložený k tomuto skriptu) do stejného adresáře.")
        return 1

    db = json.loads(args.db.read_text(encoding="utf-8"))
    logger.info("Načteno %d plemen z %s", len(db["breeds"]), args.db)

    filter_groups: set[int] | None = None
    if args.groups:
        try:
            filter_groups = {int(g.strip()) for g in args.groups.split(",")}
            logger.info("Filtr skupin: %s", sorted(filter_groups))
        except ValueError:
            logger.error("Neplatný formát --groups: %s", args.groups)
            return 1

    fetcher: WikiPhotoFetcher | None = None
    if not args.no_images:
        fetcher = WikiPhotoFetcher(
            cache_dir=args.photo_dir,
            user_agent=args.user_agent,
            sleep_seconds=args.sleep,
        )
        logger.info("Cache fotek: %s", args.photo_dir.resolve())

    deck, media_files = build_deck(db, fetcher, filter_groups, args.limit)

    logger.info("Balím .apkg…")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pkg = genanki.Package(deck)
    pkg.media_files = [str(p) for p in media_files]
    pkg.write_to_file(str(args.output))

    size_kb = args.output.stat().st_size / 1024
    logger.info("✓ Hotovo: %s (%.1f KB, %d karet, %d médií)",
                args.output, size_kb, len(deck.notes), len(media_files))
    logger.info("")
    logger.info("Import do Anki: otevři aplikaci → File → Import → vyber %s", args.output.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())

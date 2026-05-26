"""Verify Czech breed names against Czech Wikipedia (report only, no auto-apply).

For each breed it fetches the cs.wikipedia summary for the current Czech name
and compares the page's canonical title with ours. Mismatches and 404s are
flagged for manual review; nothing is rewritten here (apply corrections via
data/cs_overrides.csv + apply_cs_overrides.py).

Output: reports/cs_name_review.csv with columns
    id, cs_current, cs_wikipedia, cs_match, en, fci_number, notes

Rate limited to ~1 request/second per the Wikipedia API guideline.

Usage:
    python src/verify_cs_names.py
    python src/verify_cs_names.py --limit 20      # quick sample
    python src/verify_cs_names.py --groups 1,2    # only some FCI groups

References:
    - Wikipedia REST summary: https://cs.wikipedia.org/api/rest_v1/page/summary/{title}
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("verify-cs")

USER_AGENT = "AtlasPlemen/2.0 (educational; cs-name verification)"


@dataclass
class NameReview:
    """One breed's Czech-name verification outcome."""

    id: str
    cs_current: str
    cs_wikipedia: str
    cs_match: bool
    en: str
    fci_number: str
    notes: str


def nfd_lower(text: str) -> str:
    """Unicode NFD + lowercase for tolerant equality (keeps diacritics)."""
    return unicodedata.normalize("NFD", text).lower()


def fetch_summary(lang: str, title: str, timeout: float = 15.0) -> dict[str, Any] | None:
    """Fetch a Wikipedia REST summary. Returns parsed JSON, or None on 404.

    Args:
        lang: Wikipedia language code ("cs" or "en").
        title: Page title (spaces are converted to underscores).
        timeout: Socket timeout in seconds.

    Returns:
        Parsed summary dict, or None if the page does not exist (404).

    Raises:
        urllib.error.URLError: For network errors other than 404.
    """
    encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted host)
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def review_breed(breed: dict[str, Any], sleep_seconds: float) -> NameReview:
    """Verify one breed's Czech name against Wikipedia.

    Args:
        breed: A breed record from breeds.json.
        sleep_seconds: Delay applied after each network request.

    Returns:
        A NameReview row.
    """
    cs_current = breed["cs"]
    en = breed.get("en", "")
    fci_number = str(breed.get("fci_number", ""))
    cs_wikipedia = ""
    notes = ""

    try:
        data = fetch_summary("cs", cs_current)
        time.sleep(sleep_seconds)
        if data is None:
            # Czech page missing: fall back to English to record the canonical.
            notes = "cs 404"
            en_data = fetch_summary("en", en) if en else None
            time.sleep(sleep_seconds)
            if en_data:
                canonical = en_data.get("titles", {}).get("canonical", "")
                notes += f"; en canonical: {canonical.replace('_', ' ')}"
        elif data.get("type") == "disambiguation":
            notes = "cs disambiguation"
        else:
            cs_wikipedia = data.get("titles", {}).get("normalized") or data.get("title", "")
            requested = cs_current
            if cs_wikipedia and nfd_lower(cs_wikipedia) != nfd_lower(requested):
                notes = "redirect/normalized"
    except urllib.error.URLError as e:
        notes = f"network error: {e.reason}"

    cs_match = bool(cs_wikipedia) and nfd_lower(cs_current) == nfd_lower(cs_wikipedia)
    return NameReview(
        id=breed["id"], cs_current=cs_current, cs_wikipedia=cs_wikipedia,
        cs_match=cs_match, en=en, fci_number=fci_number, notes=notes,
    )


def write_report(reviews: list[NameReview], path: Path) -> None:
    """Write the review rows to a CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "cs_current", "cs_wikipedia", "cs_match", "en",
                         "fci_number", "notes"])
        for r in reviews:
            writer.writerow([r.id, r.cs_current, r.cs_wikipedia, r.cs_match,
                             r.en, r.fci_number, r.notes])
    matched = sum(1 for r in reviews if r.cs_match)
    pct = (matched / len(reviews) * 100) if reviews else 0.0
    logger.info("Zapsano %d radku do %s (shoda %d, %.1f%%)", len(reviews), path, matched, pct)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Czech breed names vs Wikipedia")
    parser.add_argument("--db", type=Path, default=Path("data/breeds.json"))
    parser.add_argument("--out", type=Path, default=Path("reports/cs_name_review.csv"))
    parser.add_argument("--groups", type=str, default=None, help="napr. '1,2,3'")
    parser.add_argument("--limit", type=int, default=None, help="jen prvnich N plemen")
    parser.add_argument("--sleep", type=float, default=1.0, help="pauza mezi dotazy (s)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.db.exists():
        logger.error("Databaze nenalezena: %s", args.db)
        return 1
    db = json.loads(args.db.read_text(encoding="utf-8"))
    breeds = db["breeds"]
    if args.groups:
        wanted = {int(g.strip()) for g in args.groups.split(",")}
        breeds = [b for b in breeds if b["group"] in wanted]
    if args.limit:
        breeds = breeds[: args.limit]

    logger.info("Overuji %d ceskych nazvu (sleep %.1fs)...", len(breeds), args.sleep)
    reviews = [review_breed(b, args.sleep) for b in breeds]
    write_report(reviews, args.out)
    logger.info("Hotovo. Projdi report rucne a oprav v data/cs_overrides.csv.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

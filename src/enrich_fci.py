"""Enrich breeds.json with authoritative FCI metadata from the paiv CSV.

For every breed in FCI groups 1-10 this script attaches the official FCI
number, nomenclature URL, illustration URL and standard PDF URL, plus the
English section name and country of origin.

Two authoritative sources are combined:
- ČMKU breed list (data/external/cmku-breeds.csv): maps the Czech breed name
  directly to its FCI number. This is the primary bridge because our database
  is Czech-first and shares ČMKU naming conventions.
- paiv FCI CSV (data/external/fci-breeds-en.csv): keyed by FCI number, provides
  the nomenclature URL, illustration URL, standard PDF URL, English section
  name and country.

Matching strategy (no false merges), tried in order per breed:
1. Manual override (data/fci_match_overrides.csv): id -> fci_number, wins.
2. ČMKU exact: any normalized Czech/English key equals a ČMKU key.
3. ČMKU variant-stripped: drop size/coat/colour tokens, accept only when the
   result maps to a single FCI number (so distinct-numbered variants such as
   the three Schnauzer sizes, already matched exactly above, are never merged).
4. ČMKU fuzzy: token-sort Levenshtein ratio >= 0.85, unambiguous best.
5. paiv English fuzzy within the same FCI group, as a last automated fallback.

Variants share metadata: several DB rows can map to one FCI number (e.g. the
nine dachshund size/coat rows all map to FCI #148). That is expected.

Usage:
    python src/enrich_fci.py

References:
    - ČMKU breed list: https://www.cmku.cz/cz/seznam-plemen-159
    - paiv/fci-breeds CSV: https://github.com/paiv/fci-breeds
    - FCI nomenclature: https://www.fci.be/en/Nomenclature/
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("enrich-fci")

# Map the English FCI group name used in the paiv CSV to the FCI group number.
# Reference: https://www.fci.be/en/Nomenclature/
GROUP_NAME_TO_NUM: dict[str, int] = {
    "sheepdogs and cattledogs": 1,
    "pinscher and schnauzer": 2,
    "terriers": 3,
    "dachshunds": 4,
    "spitz and primitive types": 5,
    "scent hounds and related breeds": 6,
    "pointing dogs": 7,
    "retrievers": 8,
    "companion and toy dogs": 9,
    "sighthounds": 10,
}

FUZZY_THRESHOLD = 0.85

# Tokens that add no discriminating value and inflate edit distance.
# "dog"/"type" appear in CSV names ("GERMAN SHEPHERD DOG", "...CONTINENTAL TYPE")
# but rarely in our shorter names.
STOPWORD_TOKENS = {"dog", "type"}

# Size / coat / colour tokens that distinguish *varieties* of one FCI breed.
# Stripping them collapses our nine "Jezevčík ... srstý" rows onto "Jezevčík",
# but only when the stripped name maps to a single FCI number (see the matcher),
# so genuinely distinct breeds (e.g. the three Schnauzer sizes) are not merged.
VARIANT_TOKENS = {
    # Czech (ascii-folded)
    "standardni", "trpaslici", "kralici", "kaninchen", "miniaturni",
    "velky", "velka", "stredni", "maly", "mala", "toy", "vlci",
    "kratkosrsty", "kratkosrsta", "dlouhosrsty", "dlouhosrsta",
    "drsnosrsty", "drsnosrsta", "hrubosrsty", "hrubosrsta",
    "hladkosrsty", "hladkosrsta", "ostnosrsty", "ostnosrsta",
    "dratosrsty", "dratosrsta", "srsti", "srst", "obliceji",
    "bily", "bila", "cerny", "cerna", "hnedy", "hneda",
    "oranzovy", "oranzova", "sedy", "seda", "plavy", "plava",
    # English
    "standard", "miniature", "dwarf", "rabbit", "large", "medium", "small",
    "smooth", "long", "short", "wire", "rough", "haired", "hair", "coat",
    "longhaired", "shorthaired", "wirehaired", "faced", "feathered",
    "intermediate", "powderpuff", "hairless",
}


@dataclass
class FciRow:
    """One row of the paiv FCI CSV, with the parsed group number."""

    fci_number: int
    name: str
    group_num: int
    section_name: str
    country: str
    url: str
    illustration_url: str
    standard_pdf_url: str


@dataclass
class CmkuRow:
    """One row of the ČMKU breed list: Czech name to FCI number."""

    fci_number: int
    cs: str
    en: str


@dataclass
class MatchResult:
    """Outcome of matching one DB breed against the FCI sources."""

    breed_id: str
    breed_cs: str
    breed_en: str
    group: int
    fci_number: int | None = None
    # cmku_exact | cmku_variant | cmku_fuzzy | paiv_fuzzy | override | unmatched
    method: str = "unmatched"
    score: float = 0.0
    matched_name: str | None = None

    @property
    def matched(self) -> bool:
        return self.fci_number is not None


@dataclass
class EnrichmentSummary:
    """Aggregate counters for the enrichment report."""

    total_fci_breeds: int = 0
    matched: int = 0
    by_method: dict[str, int] = field(default_factory=dict)
    missing_illustration: int = 0
    unmatched_results: list[MatchResult] = field(default_factory=list)
    section_warnings: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    """Normalize a name for matching: NFKD ascii fold, lowercase, collapse punctuation.

    Args:
        text: Any breed name (Czech or English).

    Returns:
        A lowercased ascii string with runs of non-alphanumerics turned into
        single spaces and the ends stripped.
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    folded = folded.lower()
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return folded.strip()


def levenshtein(a: str, b: str) -> int:
    """Compute the Levenshtein edit distance between two strings.

    Pure-Python iterative two-row implementation (no third-party dependency).

    Args:
        a: First string.
        b: Second string.

    Returns:
        The minimum number of single-character insertions, deletions or
        substitutions to turn ``a`` into ``b``.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def ratio(a: str, b: str) -> float:
    """Normalized Levenshtein similarity in [0, 1] (1.0 == identical)."""
    if not a and not b:
        return 1.0
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0
    return 1.0 - levenshtein(a, b) / longest


def token_sort_ratio(a: str, b: str) -> float:
    """Levenshtein ratio after dropping stopword tokens and sorting the rest.

    This makes matching order-insensitive ("Smooth Fox Terrier" vs
    "Fox Terrier Smooth") and tolerant of trailing noise ("German Shepherd Dog"),
    while still refusing pure-subset matches: "miniature schnauzer" vs
    "schnauzer" stays well below 1.0, so distinct FCI numbers are not merged.

    Args:
        a: First normalized string.
        b: Second normalized string.

    Returns:
        Similarity in [0, 1].
    """
    def key(s: str) -> str:
        tokens = [t for t in s.split() if t not in STOPWORD_TOKENS]
        return " ".join(sorted(tokens)) if tokens else s

    return ratio(key(a), key(b))


def name_keys(name: str) -> list[str]:
    """Normalized match keys derived from one name.

    Includes the full name, the parenthetical removed, the parenthetical
    content on its own (CSV often hides the common alias there, e.g.
    "Atlas Mountain Dog (Aidi)"), and the segment before a dash.

    Args:
        name: A breed name (any language).

    Returns:
        Deduplicated, non-empty normalized keys.
    """
    raw: list[str] = [name]
    paren = re.findall(r"\(([^)]*)\)", name)
    raw.extend(paren)
    no_paren = re.sub(r"\s*\([^)]*\)", "", name).strip()
    raw.append(no_paren)
    raw.append(re.split(r"[-—]", no_paren)[0].strip())
    keys: list[str] = []
    for r in raw:
        n = normalize(r)
        if n and n not in keys:
            keys.append(n)
    return keys


def candidate_keys(breed: dict[str, Any]) -> list[str]:
    """Build the union of normalized match keys from a breed's en and cs names.

    Args:
        breed: A breed record from breeds.json.

    Returns:
        Deduplicated list of normalized candidate strings.
    """
    keys: list[str] = []
    for name in (breed.get("en", ""), breed.get("cs", "")):
        if not name:
            continue
        for key in name_keys(name):
            if key not in keys:
                keys.append(key)
    return keys


def strip_variant(key: str) -> str:
    """Drop size/coat/colour tokens from a normalized key.

    Args:
        key: A normalized match key.

    Returns:
        The key with VARIANT_TOKENS removed (may be empty if all tokens drop).
    """
    return " ".join(t for t in key.split() if t not in VARIANT_TOKENS).strip()


def variant_keys(breed: dict[str, Any]) -> list[str]:
    """Variant-stripped candidate keys for a breed (deduplicated, non-empty)."""
    keys: list[str] = []
    for key in candidate_keys(breed):
        stripped = strip_variant(key)
        if stripped and stripped not in keys:
            keys.append(stripped)
    return keys


def load_cmku_csv(path: Path) -> list[CmkuRow]:
    """Load the ČMKU breed list (fci_number, cs, en).

    Args:
        path: Path to cmku-breeds.csv.

    Returns:
        Parsed rows. Empty list (with a warning) if the file is missing, so the
        pipeline can still fall back to paiv English matching.
    """
    if not path.exists():
        logger.warning("ČMKU seznam nenalezen: %s (pokracuji jen s paiv CSV)", path)
        return []
    rows: list[CmkuRow] = []
    with path.open(encoding="utf-8") as fh:
        for record in csv.DictReader(fh):
            rows.append(
                CmkuRow(
                    fci_number=int(record["fci_number"]),
                    cs=record["cs"].strip(),
                    en=record["en"].strip(),
                )
            )
    logger.info("Nacteno %d ČMKU radku z %s", len(rows), path)
    return rows


def load_fci_csv(path: Path) -> list[FciRow]:
    """Load and parse the paiv FCI CSV into FciRow records (groups 1-10 only).

    Args:
        path: Path to fci-breeds-en.csv.

    Returns:
        Parsed rows whose group name is recognized.

    Raises:
        SystemExit: If the file is missing (fail fast with a Czech CLI message).
    """
    if not path.exists():
        logger.error("CSV s FCI daty nenalezen: %s", path)
        logger.error("Stahni ho: curl -sf %s -o %s",
                     "https://raw.githubusercontent.com/paiv/fci-breeds/master/fci-breeds.csv", path)
        raise SystemExit(1)

    rows: list[FciRow] = []
    with path.open(encoding="utf-8") as fh:
        for record in csv.DictReader(fh):
            group_num = GROUP_NAME_TO_NUM.get(normalize(record["group"]))
            if group_num is None:
                logger.warning("Neznama FCI skupina v CSV: %r (plemeno %s)",
                               record["group"], record["name"])
                continue
            rows.append(
                FciRow(
                    fci_number=int(record["id"]),
                    name=record["name"].strip(),
                    group_num=group_num,
                    section_name=record["section"].strip(),
                    country=record["country"].strip(),
                    url=record["url"].strip(),
                    illustration_url=record["image"].strip(),
                    standard_pdf_url=record["pdf"].strip(),
                )
            )
    logger.info("Nacteno %d FCI radku (skupiny 1-10) z %s", len(rows), path)
    return rows


@dataclass
class CmkuIndex:
    """Precomputed lookup structures over the ČMKU rows."""

    exact: dict[str, set[int]]  # normalized key -> FCI numbers
    variant: dict[str, set[int]]  # variant-stripped key -> FCI numbers
    rows: list[tuple[int, str, list[str]]]  # (fci_number, display cs, keys) for fuzzy


def build_cmku_index(cmku_rows: list[CmkuRow]) -> CmkuIndex:
    """Build exact, variant-stripped and fuzzy lookup structures from ČMKU rows."""
    exact: dict[str, set[int]] = {}
    variant: dict[str, set[int]] = {}
    rows: list[tuple[int, str, list[str]]] = []
    for row in cmku_rows:
        keys: list[str] = []
        for key in name_keys(row.cs) + name_keys(row.en):
            if key not in keys:
                keys.append(key)
        rows.append((row.fci_number, row.cs, keys))
        for key in keys:
            exact.setdefault(key, set()).add(row.fci_number)
            stripped = strip_variant(key)
            if stripped:
                variant.setdefault(stripped, set()).add(row.fci_number)
    return CmkuIndex(exact=exact, variant=variant, rows=rows)


def _unique(numbers: set[int]) -> int | None:
    """Return the single element of a set, or None if it is not a singleton."""
    return next(iter(numbers)) if len(numbers) == 1 else None


def match_breed(
    breed: dict[str, Any],
    cmku: CmkuIndex,
    paiv_group_rows: list[FciRow],
) -> MatchResult:
    """Match one breed to an FCI number through ČMKU, then paiv as fallback.

    Args:
        breed: Breed record from breeds.json.
        cmku: Precomputed ČMKU index.
        paiv_group_rows: paiv rows restricted to ``breed['group']`` (fallback).

    Returns:
        A MatchResult; ``method`` records which pass succeeded.
    """
    result = MatchResult(
        breed_id=breed["id"], breed_cs=breed["cs"], breed_en=breed["en"], group=breed["group"]
    )
    keys = candidate_keys(breed)

    # Pass 1: ČMKU exact (unambiguous).
    for key in keys:
        number = _unique(cmku.exact.get(key, set()))
        if number is not None:
            result.fci_number, result.method, result.score = number, "cmku_exact", 1.0
            result.matched_name = key
            return result

    # Pass 2: ČMKU variant-stripped (unambiguous only -> never merges distinct breeds).
    for key in variant_keys(breed):
        number = _unique(cmku.variant.get(key, set()))
        if number is not None:
            result.fci_number, result.method, result.score = number, "cmku_variant", 0.95
            result.matched_name = key
            return result

    # Pass 3: ČMKU fuzzy (token-sort), best unambiguous score.
    best_score, best_number, best_name = 0.0, None, None
    for key in keys:
        for number, display, ckeys in cmku.rows:
            for ckey in ckeys:
                score = token_sort_ratio(key, ckey)
                if score > best_score:
                    best_score, best_number, best_name = score, number, display
    if best_number is not None and best_score >= FUZZY_THRESHOLD:
        result.fci_number, result.method = best_number, "cmku_fuzzy"
        result.score, result.matched_name = round(best_score, 3), best_name
        return result

    # Pass 4: paiv English fuzzy within the same FCI group (last automated try).
    paiv_score, paiv_row = 0.0, None
    for key in keys:
        for row in paiv_group_rows:
            for rkey in name_keys(row.name):
                score = token_sort_ratio(key, rkey)
                if score > paiv_score:
                    paiv_score, paiv_row = score, row
    if paiv_row is not None and paiv_score >= FUZZY_THRESHOLD:
        result.fci_number, result.method = paiv_row.fci_number, "paiv_fuzzy"
        result.score, result.matched_name = round(paiv_score, 3), paiv_row.name
        return result

    # Unmatched: record the best near-miss seen (whichever source scored higher).
    if best_score >= paiv_score and best_name is not None:
        result.score, result.matched_name = round(best_score, 3), best_name
    elif paiv_row is not None:
        result.score, result.matched_name = round(paiv_score, 3), paiv_row.name
    return result


def load_overrides(path: Path) -> dict[str, int]:
    """Load manual id->fci_number overrides from a CSV, if present.

    Args:
        path: Path to data/fci_match_overrides.csv (columns: id, fci_number, reason).

    Returns:
        Mapping of breed id to FCI number. Empty if the file does not exist.
    """
    overrides: dict[str, int] = {}
    if not path.exists():
        return overrides
    with path.open(encoding="utf-8") as fh:
        for record in csv.DictReader(fh):
            breed_id = record["id"].strip()
            value = record["fci_number"].strip()
            if not breed_id or not value:
                continue
            overrides[breed_id] = int(value)
    logger.info("Nacteno %d manualnich override z %s", len(overrides), path)
    return overrides


def enrich(
    db: dict[str, Any],
    fci_rows: list[FciRow],
    cmku_rows: list[CmkuRow],
    overrides: dict[str, int],
) -> tuple[dict[str, Any], EnrichmentSummary]:
    """Attach FCI metadata to every group 1-10 breed in the database.

    Args:
        db: Parsed breeds.json (mutated in place and returned).
        fci_rows: Parsed paiv FCI CSV rows (keyed by number for URLs/section).
        cmku_rows: Parsed ČMKU rows (Czech name -> FCI number, primary bridge).
        overrides: Manual id->fci_number assignments (win over matching).

    Returns:
        Tuple of the enriched database and an EnrichmentSummary.
    """
    rows_by_number = {row.fci_number: row for row in fci_rows}
    rows_by_group: dict[int, list[FciRow]] = {}
    for row in fci_rows:
        rows_by_group.setdefault(row.group_num, []).append(row)
    cmku_index = build_cmku_index(cmku_rows)

    summary = EnrichmentSummary()

    for breed in db["breeds"]:
        if breed["group"] not in range(1, 11):
            continue  # group 11 (non-FCI) keeps the Wikipedia-only photo path
        summary.total_fci_breeds += 1

        if breed["id"] in overrides:
            result = MatchResult(
                breed_id=breed["id"], breed_cs=breed["cs"], breed_en=breed["en"],
                group=breed["group"], fci_number=overrides[breed["id"]],
                method="override", score=1.0,
            )
        else:
            result = match_breed(breed, cmku_index, rows_by_group.get(breed["group"], []))

        summary.by_method[result.method] = summary.by_method.get(result.method, 0) + 1

        if not result.matched:
            summary.unmatched_results.append(result)
            continue

        summary.matched += 1
        breed["fci_number"] = result.fci_number
        row = rows_by_number.get(result.fci_number)  # type: ignore[arg-type]
        if row is None:
            # ČMKU gave a number with no matching paiv row (rare). Keep the
            # number; URLs/section stay absent and the illustration counts as
            # missing so it shows up in the summary.
            summary.missing_illustration += 1
            summary.section_warnings.append(
                f"{breed['id']} ({breed['cs']}): FCI #{result.fci_number} not in paiv CSV"
            )
            continue
        breed["fci_url"] = row.url
        breed["fci_illustration_url"] = row.illustration_url
        breed["fci_standard_pdf_url"] = row.standard_pdf_url
        breed["fci_section_name_en"] = row.section_name
        breed["fci_country_en"] = row.country
        if not row.illustration_url:
            summary.missing_illustration += 1

    return db, summary


def write_unmatched_report(summary: EnrichmentSummary, path: Path) -> None:
    """Write unmatched breeds to a CSV for manual review."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "cs", "en", "group", "best_score", "best_guess"])
        for r in summary.unmatched_results:
            writer.writerow([r.breed_id, r.breed_cs, r.breed_en, r.group,
                             r.score, r.matched_name or ""])
    logger.info("Zapsano %d nesparovanych do %s", len(summary.unmatched_results), path)


def write_summary_report(summary: EnrichmentSummary, path: Path) -> None:
    """Write a human-readable enrichment summary in Markdown."""
    path.parent.mkdir(parents=True, exist_ok=True)
    total = summary.total_fci_breeds
    pct = (summary.matched / total * 100) if total else 0.0
    lines = [
        "# FCI enrichment summary",
        "",
        f"- FCI breeds (groups 1-10): **{total}**",
        f"- Matched: **{summary.matched}** ({pct:.1f}%)",
        f"- Unmatched: **{len(summary.unmatched_results)}**",
        f"- Matched but missing FCI illustration: **{summary.missing_illustration}**",
        "",
        "## By match method",
        "",
        "| method | count |",
        "| --- | --- |",
    ]
    for method, count in sorted(summary.by_method.items()):
        lines.append(f"| {method} | {count} |")
    if summary.unmatched_results:
        lines += ["", "## Unmatched (need manual override)", "",
                   "| id | cs | en | group | best score | best guess |",
                   "| --- | --- | --- | --- | --- | --- |"]
        for r in summary.unmatched_results:
            lines.append(
                f"| {r.breed_id} | {r.breed_cs} | {r.breed_en} | {r.group} | "
                f"{r.score} | {r.matched_name or ''} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Zapsan souhrn do %s", path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enrich breeds.json with FCI metadata")
    parser.add_argument("--db", type=Path, default=Path("data/breeds.json"))
    parser.add_argument("--csv", type=Path, default=Path("data/external/fci-breeds-en.csv"))
    parser.add_argument("--cmku", type=Path, default=Path("data/external/cmku-breeds.csv"))
    parser.add_argument("--overrides", type=Path, default=Path("data/fci_match_overrides.csv"))
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Nezapisovat breeds.json, jen reporty")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.db.exists():
        logger.error("Databaze nenalezena: %s", args.db)
        return 1

    db = json.loads(args.db.read_text(encoding="utf-8"))
    fci_rows = load_fci_csv(args.csv)
    cmku_rows = load_cmku_csv(args.cmku)
    overrides = load_overrides(args.overrides)

    db, summary = enrich(db, fci_rows, cmku_rows, overrides)

    write_unmatched_report(summary, args.reports_dir / "unmatched.csv")
    write_summary_report(summary, args.reports_dir / "enrichment_summary.md")

    if not args.dry_run:
        args.db.write_text(
            json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        logger.info("Zapsano %s", args.db)

    total = summary.total_fci_breeds
    pct = (summary.matched / total * 100) if total else 0.0
    logger.info("Hotovo: %d/%d (%.1f%%) sparovano, %d nesparovano",
                summary.matched, total, pct, len(summary.unmatched_results))
    return 0


if __name__ == "__main__":
    sys.exit(main())

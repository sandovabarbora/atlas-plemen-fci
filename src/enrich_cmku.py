"""Attach authoritative ČMKU links to breeds.json and verify Czech names.

ČMKU (the Czech-Moravian Kennel Union) is the authority for Czech breed names.
Each breed in their list links to an official Czech breed-description PDF
(/data/plemena_popisy/{id}-{slug}.pdf). This script:

- matches every breed to its ČMKU row (same name matching as enrich_fci),
- attaches ``cmku_url`` (detail page) and ``cmku_popis_pdf`` (description PDF),
- writes reports/cmku_name_review.csv flagging where our Czech name differs
  from ČMKU's (the most authoritative name check, done offline).

Nothing is invented: every link comes straight from the ČMKU list, and with
--verify each PDF is HEAD-checked so no dead link is stored.

Usage:
    python src/enrich_cmku.py            # match + attach + name report
    python src/enrich_cmku.py --verify   # also HEAD-check every PDF (slow, polite)

References:
    - ČMKU breed list: https://www.cmku.cz/cz/seznam-plemen-159
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Reuse the matching helpers from the FCI enrichment.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import enrich_fci as ef  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("enrich-cmku")

USER_AGENT = "AtlasPlemen/2.0 (educational; cmku enrichment)"


@dataclass
class CmkuFull:
    """A ČMKU list row with detail and description-PDF URLs."""

    fci_number: int
    cs: str
    en: str
    list_id: int
    cmku_url: str
    cmku_popis_pdf: str


@dataclass
class CmkuMatchSummary:
    total: int = 0
    matched: int = 0
    name_diffs: list[tuple[str, str, str]] = field(default_factory=list)  # id, our cs, cmku cs
    unmatched: list[tuple[str, str]] = field(default_factory=list)


def load_cmku_full(path: Path) -> list[CmkuFull]:
    rows: list[CmkuFull] = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(CmkuFull(
                fci_number=int(r["fci_number"]), cs=r["cs"].strip(), en=r["en"].strip(),
                list_id=int(r["list_id"]), cmku_url=r["cmku_url"].strip(),
                cmku_popis_pdf=r["cmku_popis_pdf"].strip(),
            ))
    return rows


def _best_by_name(keys: list[str], rows: list[tuple[CmkuFull, list[str]]]) -> tuple[float, CmkuFull | None]:
    """Highest token-sort ratio between any breed key and any row key."""
    best, best_row = 0.0, None
    for key in keys:
        for row, rkeys in rows:
            for rk in rkeys:
                score = ef.token_sort_ratio(key, rk)
                if score > best:
                    best, best_row = score, row
    return best, best_row


def match_row(breed: dict[str, Any], rows: list[tuple[CmkuFull, list[str]]]) -> CmkuFull | None:
    """Find the best ČMKU row: exact name, variant-stripped, FCI number, then fuzzy."""
    keys = ef.candidate_keys(breed)
    # Exact: any breed key equals any row key.
    for key in keys:
        for row, rkeys in rows:
            if key in rkeys:
                return row
    # Variant-stripped exact (drops size/coat tokens), accept if unambiguous.
    vkeys = ef.variant_keys(breed)
    hits = {row.fci_number: row for row, rkeys in rows
            if any(ef.strip_variant(rk) in vkeys for rk in rkeys)}
    if len(hits) == 1:
        return next(iter(hits.values()))
    # FCI-number fallback: the breed was already enriched with an FCI number, and
    # ČMKU rows carry numbers too, so the same-number ČMKU row IS the same breed.
    # When several ČMKU rows share the number (e.g. German Spitz sizes), pick the
    # one whose name best matches (so a size/colour variant lands on its row).
    num = breed.get("fci_number")
    if num:
        same = [(row, rkeys) for row, rkeys in rows if row.fci_number == num]
        if len(same) == 1:
            return same[0][0]
        if same:
            _, row = _best_by_name(keys, same)
            if row is not None:
                return row
    # Fuzzy: best token-sort ratio across all rows.
    best, best_row = _best_by_name(keys, rows)
    return best_row if best >= ef.FUZZY_THRESHOLD else None


def verify_pdf(url: str) -> bool:
    """HEAD-check a PDF URL (True if it exists). Used to avoid storing dead links."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        return urllib.request.urlopen(req, timeout=15).status == 200  # noqa: S310 (trusted host)
    except urllib.error.URLError:
        return False


def enrich(
    db: dict[str, Any],
    cmku_rows: list[CmkuFull],
    verify: bool,
) -> CmkuMatchSummary:
    """Attach ČMKU links to each breed and collect a name-discrepancy report."""
    indexed = [(row, _row_keys(row)) for row in cmku_rows]
    summary = CmkuMatchSummary()
    verified: dict[str, bool] = {}

    for breed in db["breeds"]:
        summary.total += 1
        row = match_row(breed, indexed)
        if row is None:
            summary.unmatched.append((breed["id"], breed["cs"]))
            continue

        pdf_ok = True
        if verify:
            if row.cmku_popis_pdf not in verified:
                verified[row.cmku_popis_pdf] = verify_pdf(row.cmku_popis_pdf)
                time.sleep(1.0)
            pdf_ok = verified[row.cmku_popis_pdf]

        summary.matched += 1
        breed["cmku_url"] = row.cmku_url
        if pdf_ok:
            breed["cmku_popis_pdf"] = row.cmku_popis_pdf

        if ef.normalize(breed["cs"]) != ef.normalize(row.cs):
            summary.name_diffs.append((breed["id"], breed["cs"], row.cs))

    return summary


def _row_keys(row: CmkuFull) -> list[str]:
    keys: list[str] = []
    for key in ef.name_keys(row.cs) + ef.name_keys(row.en):
        if key not in keys:
            keys.append(key)
    return keys


def write_name_review(summary: CmkuMatchSummary, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "cs_ours", "cs_cmku", "note"])
        for bid, ours, cmku in summary.name_diffs:
            w.writerow([bid, ours, cmku, "lisi se od CMKU"])
    logger.info("Zapsano %d odlisnych nazvu do %s", len(summary.name_diffs), path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Attach ČMKU links + verify Czech names")
    p.add_argument("--db", type=Path, default=Path("data/breeds.json"))
    p.add_argument("--cmku", type=Path, default=Path("data/external/cmku-breeds.csv"))
    p.add_argument("--reports-dir", type=Path, default=Path("reports"))
    p.add_argument("--verify", action="store_true", help="HEAD-check each PDF (slow)")
    p.add_argument("--dry-run", action="store_true", help="nezapisovat breeds.json")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.db.exists():
        logger.error("Databaze nenalezena: %s", args.db)
        return 1
    db = json.loads(args.db.read_text(encoding="utf-8"))
    cmku_rows = load_cmku_full(args.cmku)
    logger.info("Nacteno %d CMKU radku", len(cmku_rows))

    summary = enrich(db, cmku_rows, verify=args.verify)
    write_name_review(summary, args.reports_dir / "cmku_name_review.csv")

    if not args.dry_run:
        args.db.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("Zapsano %s", args.db)

    logger.info("Hotovo: %d/%d sparovano, %d odlisnych ceskych nazvu, %d nesparovanych",
                summary.matched, summary.total, len(summary.name_diffs), len(summary.unmatched))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Apply manual Czech-name corrections to breeds.json.

Reads data/cs_overrides.csv (columns: id, cs_corrected, reason) and rewrites
the `cs` field of the matching breeds. This is the deliberate, manual step
after reviewing reports/cs_name_review.csv: nothing about Czech names is
changed automatically.

Usage:
    python src/apply_cs_overrides.py
    python src/apply_cs_overrides.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("apply-cs")


def load_overrides(path: Path) -> dict[str, str]:
    """Load id -> corrected Czech name from the overrides CSV."""
    overrides: dict[str, str] = {}
    if not path.exists():
        return overrides
    with path.open(encoding="utf-8") as fh:
        for record in csv.DictReader(fh):
            breed_id = record["id"].strip()
            corrected = record["cs_corrected"].strip()
            if breed_id and corrected:
                overrides[breed_id] = corrected
    return overrides


def apply_overrides(db: dict, overrides: dict[str, str]) -> list[tuple[str, str, str]]:
    """Apply corrections in place. Returns a list of (id, old, new) changes."""
    by_id = {b["id"]: b for b in db["breeds"]}
    changes: list[tuple[str, str, str]] = []
    for breed_id, corrected in overrides.items():
        breed = by_id.get(breed_id)
        if breed is None:
            logger.warning("Override pro nezname id: %s", breed_id)
            continue
        if breed["cs"] != corrected:
            changes.append((breed_id, breed["cs"], corrected))
            breed["cs"] = corrected
    return changes


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply Czech-name overrides to breeds.json")
    parser.add_argument("--db", type=Path, default=Path("data/breeds.json"))
    parser.add_argument("--overrides", type=Path, default=Path("data/cs_overrides.csv"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.db.exists():
        logger.error("Databaze nenalezena: %s", args.db)
        return 1
    overrides = load_overrides(args.overrides)
    if not overrides:
        logger.info("Zadne overrides v %s (nic k aplikaci).", args.overrides)
        return 0

    db = json.loads(args.db.read_text(encoding="utf-8"))
    changes = apply_overrides(db, overrides)
    for breed_id, old, new in changes:
        logger.info("%s: %r -> %r", breed_id, old, new)

    if not changes:
        logger.info("Vsechny nazvy uz odpovidaji, nic nezmeneno.")
        return 0
    if args.dry_run:
        logger.info("dry-run: %d zmen by se zapsalo.", len(changes))
        return 0
    args.db.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("Zapsano %d zmen do %s. Nezapomen prebuildit (src/build_db.py).", len(changes), args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

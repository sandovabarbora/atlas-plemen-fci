"""Build the distributable quiz HTML from the template and breeds.json.

Replaces the ``__BREED_DATABASE__`` placeholder in web/template.html with the
current contents of data/breeds.json and writes web/index.html (the file the
service worker and GitHub Pages serve). A copy also goes to dist/ for release.

Keeping breeds.json as the single source of truth means the quiz, the Anki
deck and the schema all read the same data.

Usage:
    python src/build_db.py
    python src/build_db.py --template web/template.html --db data/breeds.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("build-db")

PLACEHOLDER = "__BREED_DATABASE__"


def build(template_path: Path, db_path: Path, outputs: list[Path]) -> int:
    """Inject breeds.json into the template and write the output files.

    Args:
        template_path: Path to web/template.html (must contain PLACEHOLDER once).
        db_path: Path to breeds.json.
        outputs: Paths to write the rendered HTML to.

    Returns:
        Process exit code (0 on success).
    """
    if not template_path.exists():
        logger.error("Šablona nenalezena: %s", template_path)
        return 1
    if not db_path.exists():
        logger.error("Databáze nenalezena: %s", db_path)
        return 1

    template = template_path.read_text(encoding="utf-8")
    count = template.count(PLACEHOLDER)
    if count != 1:
        logger.error("Šablona musí obsahovat %s právě jednou (nalezeno %d).",
                     PLACEHOLDER, count)
        return 1

    # Validate JSON and re-serialize compactly (single line, no surprises).
    db = json.loads(db_path.read_text(encoding="utf-8"))
    db_literal = json.dumps(db, ensure_ascii=False, separators=(",", ":"))

    # str.replace would choke on backslashes in the data being treated as
    # nothing special here (replace does not interpret them), so it is safe.
    rendered = template.replace(PLACEHOLDER, db_literal)

    for out in outputs:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        size_kb = out.stat().st_size / 1024
        logger.info("Zapsáno %s (%.1f KB, %d plemen)", out, size_kb, len(db["breeds"]))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build quiz HTML from template + breeds.json")
    parser.add_argument("--template", type=Path, default=Path("web/template.html"))
    parser.add_argument("--db", type=Path, default=Path("data/breeds.json"))
    parser.add_argument("--out", type=Path, default=Path("web/index.html"))
    parser.add_argument("--dist", type=Path, default=Path("dist/atlas-plemen-kviz.html"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return build(args.template, args.db, [args.out, args.dist])


if __name__ == "__main__":
    sys.exit(main())

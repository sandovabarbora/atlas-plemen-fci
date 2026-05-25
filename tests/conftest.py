"""Shared pytest fixtures and path setup."""
from __future__ import annotations

import sys
from pathlib import Path

# Make `src` and the repo root importable regardless of where pytest runs.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"

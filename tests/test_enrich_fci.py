"""Tests for the FCI enrichment pipeline."""
from __future__ import annotations

import json

import pytest

from src import enrich_fci as ef
from tests.conftest import FIXTURES

# --- normalization and string similarity ------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Briard", "briard"),
        ("Český fousek", "cesky fousek"),
        ("BELGIAN SHEPHERD DOG", "belgian shepherd dog"),
        ("Cão Fila de São Miguel", "cao fila de sao miguel"),
        ("  spaced  --  out ", "spaced out"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert ef.normalize(raw) == expected


def test_levenshtein_basic() -> None:
    assert ef.levenshtein("kitten", "sitting") == 3
    assert ef.levenshtein("abc", "abc") == 0
    assert ef.levenshtein("", "abc") == 3


def test_ratio_bounds() -> None:
    assert ef.ratio("abc", "abc") == 1.0
    assert ef.ratio("", "") == 1.0
    assert 0.0 <= ef.ratio("dobrman", "doberman") <= 1.0


def test_token_sort_ratio_handles_word_order() -> None:
    # "Smooth Fox Terrier" vs "Fox Terrier Smooth" are the same set of tokens.
    assert ef.token_sort_ratio("smooth fox terrier", "fox terrier smooth") == 1.0


def test_token_sort_ratio_refuses_subset_merge() -> None:
    # A pure subset must NOT score 1.0, so distinct FCI numbers stay separate.
    assert ef.token_sort_ratio("miniature schnauzer", "schnauzer") < 0.9


def test_token_sort_ratio_drops_stopword_dog() -> None:
    assert ef.token_sort_ratio("german shepherd", "german shepherd dog") == 1.0


# --- key generation ----------------------------------------------------------

def test_name_keys_extracts_parenthetical_alias() -> None:
    keys = ef.name_keys("Atlas Mountain Dog (Aidi)")
    assert "aidi" in keys
    assert "atlas mountain dog" in keys


def test_candidate_keys_uses_both_languages() -> None:
    breed = {"en": "Berger de Brie", "cs": "Briard"}
    keys = ef.candidate_keys(breed)
    assert "briard" in keys
    assert "berger de brie" in keys


def test_strip_variant_removes_size_coat_tokens() -> None:
    assert ef.strip_variant("jezevcik standardni kratkosrsty") == "jezevcik"
    assert ef.strip_variant("pudl velky") == "pudl"


# --- matching passes ---------------------------------------------------------

def _cmku(rows: list[tuple[int, str, str]]) -> ef.CmkuIndex:
    return ef.build_cmku_index([ef.CmkuRow(n, cs, en) for n, cs, en in rows])


def test_breed_match_exact() -> None:
    breed = {"id": "b", "cs": "Briard", "en": "Berger de Brie", "group": 1}
    cmku = _cmku([(113, "Briard", "Briard")])
    result = ef.match_breed(breed, cmku, [])
    assert result.fci_number == 113
    assert result.method == "cmku_exact"


def test_breed_match_variant_unambiguous() -> None:
    # Our row carries extra size/coat words; ČMKU has only the base "Jezevčík".
    breed = {"id": "b", "cs": "Jezevčík standardní krátkosrstý",
             "en": "Dachshund Standard Smooth", "group": 4}
    cmku = _cmku([(148, "Jezevčík", "Dachshund")])
    result = ef.match_breed(breed, cmku, [])
    assert result.fci_number == 148
    assert result.method == "cmku_variant"


def test_breed_match_variant_ambiguous_is_skipped() -> None:
    # Stripping size from "Knírač střední" would hit three numbers -> no variant
    # match; but the exact pass catches it first, so we still get the right one.
    breed = {"id": "b", "cs": "Knírač střední", "en": "Standard Schnauzer", "group": 2}
    cmku = _cmku([
        (181, "Knírač velký", "Giant Schnauzer"),
        (182, "Knírač střední", "Schnauzer"),
        (183, "Knírač malý", "Miniature Schnauzer"),
    ])
    result = ef.match_breed(breed, cmku, [])
    assert result.fci_number == 182
    assert result.method == "cmku_exact"


def test_breed_match_fuzzy_above_threshold() -> None:
    # One-character spelling differences keep the ratio above 0.85 without
    # being an exact match.
    breed = {"id": "b", "cs": "Dobrman", "en": "Doberman", "group": 2}
    cmku = _cmku([(143, "Dobrmann", "Dobermann")])
    result = ef.match_breed(breed, cmku, [])
    assert result.fci_number == 143
    assert result.method == "cmku_fuzzy"
    assert result.score >= ef.FUZZY_THRESHOLD


def test_breed_match_below_threshold_skipped() -> None:
    breed = {"id": "b", "cs": "Úplně jiné plemeno", "en": "Totally Different", "group": 1}
    cmku = _cmku([(1, "Australská kelpie", "Australian Kelpie")])
    result = ef.match_breed(breed, cmku, [])
    assert result.fci_number is None
    assert result.method == "unmatched"
    # Still records a best near-miss for the report.
    assert result.matched_name is not None


def test_breed_match_paiv_fallback() -> None:
    # Empty ČMKU forces the paiv English fuzzy fallback within the same group.
    breed = {"id": "b", "cs": "Anglický pointer", "en": "English Pointer", "group": 7}
    paiv_row = ef.FciRow(
        fci_number=1, name="ENGLISH POINTER", group_num=7, section_name="Pointing",
        country="GB", url="u", illustration_url="i", standard_pdf_url="p",
    )
    result = ef.match_breed(breed, _cmku([]), [paiv_row])
    assert result.fci_number == 1
    assert result.method == "paiv_fuzzy"


# --- end-to-end enrich -------------------------------------------------------

def _paiv_rows() -> list[ef.FciRow]:
    return [
        ef.FciRow(113, "BRIARD", 1, "Sheepdogs", "FRANCE",
                  "https://fci/113", "https://img/113g01.jpg", "https://pdf/113.pdf"),
        ef.FciRow(148, "DACHSHUND", 4, "", "GERMANY",
                  "https://fci/148", "https://img/148g04.jpg", "https://pdf/148.pdf"),
    ]


def _cmku_rows() -> list[ef.CmkuRow]:
    return [ef.CmkuRow(113, "Briard", "Briard"), ef.CmkuRow(148, "Jezevčík", "Dachshund")]


def test_enrich_attaches_metadata_and_skips_group_11() -> None:
    db = json.loads((FIXTURES / "sample_breeds.json").read_text(encoding="utf-8"))
    enriched, summary = ef.enrich(db, _paiv_rows(), _cmku_rows(), overrides={})

    briard = next(b for b in enriched["breeds"] if b["id"] == "breed_000")
    assert briard["fci_number"] == 113
    assert briard["fci_illustration_url"] == "https://img/113g01.jpg"
    assert briard["fci_country_en"] == "FRANCE"

    dachshund = next(b for b in enriched["breeds"] if b["id"] == "breed_001")
    assert dachshund["fci_number"] == 148  # variant collapsed onto base

    pitbull = next(b for b in enriched["breeds"] if b["id"] == "breed_002")
    assert "fci_number" not in pitbull  # group 11 untouched

    assert summary.total_fci_breeds == 2
    assert summary.matched == 2


def test_enrich_override_wins() -> None:
    db = json.loads((FIXTURES / "sample_breeds.json").read_text(encoding="utf-8"))
    # Force the Briard onto a different number via override.
    enriched, summary = ef.enrich(db, _paiv_rows(), _cmku_rows(),
                                  overrides={"breed_000": 148})
    briard = next(b for b in enriched["breeds"] if b["id"] == "breed_000")
    assert briard["fci_number"] == 148
    assert summary.by_method.get("override") == 1


def test_real_breeds_json_is_highly_enriched() -> None:
    """Integration: the committed breeds.json should be >=95% enriched."""
    db = json.loads((ef.Path("data/breeds.json")).read_text(encoding="utf-8"))
    fci = [b for b in db["breeds"] if 1 <= b["group"] <= 10]
    with_number = [b for b in fci if "fci_number" in b]
    assert len(with_number) / len(fci) >= 0.95


def test_main_dry_run_writes_reports_without_touching_db(tmp_path, capsys) -> None:
    """End-to-end CLI run on the real data; --dry-run must not rewrite the DB."""
    before = ef.Path("data/breeds.json").read_text(encoding="utf-8")
    rc = ef.main(["--dry-run", "--reports-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "unmatched.csv").exists()
    summary = (tmp_path / "enrichment_summary.md").read_text(encoding="utf-8")
    assert "FCI enrichment summary" in summary
    assert ef.Path("data/breeds.json").read_text(encoding="utf-8") == before


def test_load_helpers_on_real_files() -> None:
    fci_rows = ef.load_fci_csv(ef.Path("data/external/fci-breeds-en.csv"))
    cmku_rows = ef.load_cmku_csv(ef.Path("data/external/cmku-breeds.csv"))
    overrides = ef.load_overrides(ef.Path("data/fci_match_overrides.csv"))
    assert len(fci_rows) > 300
    assert len(cmku_rows) > 300
    assert overrides["breed_097"] == 346


def test_load_cmku_missing_returns_empty(tmp_path) -> None:
    assert ef.load_cmku_csv(tmp_path / "nope.csv") == []

# Changelog

Formát: [Keep a Changelog](https://keepachangelog.com/). Datumy YYYY-MM-DD.

## [2.0.0] - 2026-05-25

Enrichment iterace: z funkčního prototypu na produkční datový základ.

### Added
- `src/enrich_fci.py`: doplnění FCI metadat (`fci_number`, `fci_url`,
  `fci_illustration_url`, `fci_standard_pdf_url`, `fci_section_name_en`,
  `fci_country_en`) do `breeds.json`. ČMKU seznam je primární most
  (český název -> FCI číslo), paiv CSV doplňuje URL/sekce/zemi.
  **396/397 plemen skupin 1-10 obohaceno (99,7 %).**
- `data/external/cmku-breeds.csv`, `fci-breeds-en.csv`, `fci-breeds-fr.csv`.
- `data/fci_match_overrides.csv`: ruční řešení 20 tvrdých případů.
- `src/build_db.py`: vkládá `breeds.json` do šablony -> `web/index.html`.
- `src/photo_fetcher.py`: sdílená FCI-first foto-cascade pro Anki.
- `schemas/breeds.schema.json`: JSON Schema (Draft 2020-12).
- Testy (`tests/`, 46 testů), CI, pre-commit, `pyproject.toml`.

### Changed
- Kvíz: `WikiImages` -> `BreedPhotos` (FCI-first cascade). Foto-credit ukazuje
  badge "oficiální FCI" + odkaz na standard, jinak skromné cs/en wiki.
- Anki: `WikiPhotoFetcher` -> sdílený `BreedPhotoFetcher`; cache klíčovaná
  podle zdroje (`breed_000_fci.jpg` vs `breed_000_cs.jpg`).
- `web/template.html` nyní obsahuje placeholder `__BREED_DATABASE__`; databáze
  se vkládá při buildu (jeden zdroj pravdy).
- Reorganizace z flat rootu do `data/ src/ web/ anki/ schemas/ tests/`.

### Fixed / poznámky
- FCI ilustrace neposílají CORS hlavičku: cascade je nastaví přímo jako
  `img.src` (zobrazení obrázku CORS nevyžaduje).
- 1 plemeno (`breed_102`, Jihovýchodoevropský ovčák) zůstává bez FCI metadat,
  viz `reports/open_questions.md`.

### Známé chybějící (TODO další iterace)
- P1: `verify_cs_names.py` (ověření českých názvů proti Wikipedii).
- P1: UI vylepšení (FCI číslo v feedbacku, variant pills, drill mode).
- P1: PWA (manifest, service worker, offline, add-to-home-screen).
- P3: GitHub Pages deploy workflow.

## [1.0.0] - výchozí stav

- Single-file kvíz (`atlas-plemen-kviz.html`) s Leitner SRS.
- Anki generátor (`generate_anki.py`).
- `breeds.json`: 421 plemen z ČMKU rozdělení.

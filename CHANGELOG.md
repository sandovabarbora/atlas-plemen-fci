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

### Added (interaktivní doladění)
- Profil plemene má sekci **„O plemeni"**: krátký popis z Wikipedie (cs, fallback
  en; zdrojovaný text CC-BY-SA, nic generovaného) vedle FCI faktů z naší DB,
  plus odkazy na článek a FCI standard.
- **Coat-specific fotky** pro 5 plemen se srstí (jezevčík, čivava, výmar, ruský
  toy, podengo): srst se pozná, velikost ne, tak velikost slučujeme.
- **Drill mode** (jen box 1), kombinovaný režim „Plemeno + zařazení",
  otáčecí kartičky a výběr „Projet vše" (náhodně, ale pokryje vše).
- Nasazeno na GitHub Pages (auto-deploy z `main`).

### Fixed (interaktivní doladění)
- „Fotka není k dispozici" se objevovala často: dočasná selhání (Wikipedia 429,
  výpadky) se ukládala natrvalo. Nově se persistují jen úspěchy, selhání se
  zkouší znovu při dalším načtení; service worker cachuje jen obrázky.
- Poznávací otázka u variantních plemen byla neřešitelná (9 jezevčíků = jedna
  sdílená ilustrace). Distraktory teď nesdílí ilustraci; srst zůstává jako
  rozlišení se správnou fotkou.
- Když FCI ilustrace selže, padá to na Wikipedii (a přepíše cache).

### Added (pokračování iterace)
- Kvíz: režim **Plemeno + zařazení** (vícestupňový: plemeno → skupina → sekce,
  správně jen když vše) a **otáčecí kartička** (odhalení + sebehodnocení).
- Výběr plemen **Projet vše**: náhodně, ale pokryje každé plemeno než se opakuje
  (persistovaná zamíchaná fronta), vedle Leitner SRS.
- **PWA**: manifest, service worker (cache-first shell, network-first fotky),
  ikony 192/512, instalovatelné a offline.
- `src/verify_cs_names.py`: ověření českých názvů proti cs.wikipedii
  (report-only, `reports/cs_name_review.csv`).
- `src/apply_cs_overrides.py`: ruční aplikace oprav z `data/cs_overrides.csv`.
- GitHub Pages deploy workflow (`.github/workflows/deploy.yml`).

### Známé chybějící (TODO další iterace)
- Variant pills jako filtr kvízu, drill mode (box 1).
- Audio, export statistik jako txt (P3 nice-to-have).

## [1.0.0] - výchozí stav

- Single-file kvíz (`atlas-plemen-kviz.html`) s Leitner SRS.
- Anki generátor (`generate_anki.py`).
- `breeds.json`: 421 plemen z ČMKU rozdělení.

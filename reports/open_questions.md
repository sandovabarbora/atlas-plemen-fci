# Otevřené otázky pro uživatelku

Generováno během enrichment iterace. Default chování zvoleno, čeká na rozhodnutí.

## P0 — FCI enrichment

### breed_102 — Jihovýchodoevropský ovčák (South-East European Shepherd) — VYŘEŠENO
Nenalezeno v ČMKU ani v paiv FCI CSV; "South-East European Shepherd" není
oficiální FCI plemeno a nešlo o duplikát. **Rozhodnutí (2026-05-26): smazáno**
z breeds.json. Po smazání je enrichment 396/396 (100 %), 0 nesparovaných.

## Korekce oproti zadání (potvrzeno měřením)

1. **FCI ilustrace neposílají `access-control-allow-origin`.** Zadání tvrdilo
   opak. Pro zobrazení v `<img src>` to nevadí (img není pod CORS); jen se
   nesmí použít `fetch()` na binárku ilustrace. Cascade proto u FCI nastaví
   `img.src` přímo.
2. **Schéma paiv CSV se liší od zadání.** Skutečné sloupce:
   `id,name,group,section,provisional,country,url,image,pdf` (žádný
   `subsection_name`; `group`/`section` jsou anglické názvy, ne kódy).
3. **Primárním mostem k FCI číslům je ČMKU seznam, ne anglický fuzzy match.**
   ČMKU dává `české jméno -> FCI číslo` přímo; anglické názvy v `breeds.json`
   jsou nekonzistentní (občas francouzské, např. "Berger de Brie" = Briard).

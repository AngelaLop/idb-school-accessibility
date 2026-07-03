# Pipeline History — Accessibility Platform

Historical log of bugs fixed, audits completed, deliverables produced. Moved out of `CLAUDE.md` on 2026-04-26 to keep that file focused on current contracts. The fixes documented here are already in the code.

---

## Bugs Found and Fixed in `pipeline/01_build_cima.py`

### 1. GTM — Preprimaria incorrectly included
**Bug:** `'PRIMARIA' in nivel_val` matched `'PREPRIMARIA'`, including 13,633 preschool schools.
**Fix:** Changed to `nivel_val == 'PRIMARIA'` (exact match).
**Impact:** GTM 35,350 → **21,717** schools.

### 2. HND — Pre-Básica incorrectly included
**Bug:** `str.contains('Básica')` matched `'Pre-Básica-Jardines'` and `'Pre-Básica-CCPREB'`, including 5,283 preschool schools.
**Fix:** Strip `Pre-Básica` before checking for `Básica`:
```python
niv_clean = df[niv_col].fillna('').str.replace(r'Pre-?[Bb][áa]sica', 'PREBAS', regex=True)
df['nivel_primaria'] = niv_clean.str.contains('Básica|Basica|Primaria', case=False).astype(int)
```
**Impact:** HND 16,926 → **11,643** schools.

### 3. CHL — Sector classification error
**Bug:** `COD_DEPE=3` (Particular Subvencionado) was labeled `Private`.
**Fix:** Only `COD_DEPE=4` (Particular Pagado, full-tuition) is `Private`; everything else is `Public`.
**Impact:** CHL public count corrected ~5,190 → **7,944**.

### 4. BRA — Missing coordinates (initial fix)
Raw microdata has no lat/lon. Joined `BRA_total.geojson` (89,691 coords) + `BRA_coord_EDU.csv` (13,382 more). BRA went from 0% → **79.3% georeferenced**. `BRA_coord_EDU.csv` uses comma decimal, `;` delimiter, `utf-8-sig` encoding.

### 5. BRA — Coordinate recovery from INEP GeoPackage (2026-04-06)
Added 3-source coordinate cascade in `process_BRA()`:
1. INEP `schools_2023.gpkg` from `ipea.gov.br/geobr/` → 109,311 coords
2. `BRA_coord_EDU.csv` (MEC/Educação Conectada) → 13,079 more
3. `BRA_total.geojson` (R pipeline) → 0 additional
**Result:** 79.3% → **94.2% georeferenced** (122,390/129,976). Cached at `data/schools/AR/BRA/raw/schools_2023.gpkg` (~101 MB).

### 6. MEX — Duplicate rows from school shifts (turnos) (2026-04-06)
Raw `siged_total.csv` has one row per shift. Schools with multiple shifts produced duplicate rows after column projection.
**Fix:** `df.drop_duplicates(subset='id_centro', keep='first')` before `save_cima()`.
**Impact:** 156,830 → **152,860** schools.

### 7. CHL — 15 technical schools missing K-12 filter (pending)
`cod_ense` 710, 810, 910 are valid K-12 codes not yet included.

### 8. ECU — Column match bug (2026-04-19)
`process_ECU()` searched `'tipo_educ'` but raw columns are `'tipo educación'` (space + accent). Fix: space-agnostic `'tipo educ' in c or 'tipo_educ' in c`. Regex changed from `'General B'` to `r'B[áa]sica|EGB'`.
**Impact:** 16,215 (all primaria) → **14,938** correctly distributed.

### 9. PAN — Operator precedence in `marco_col` matcher (2026-04-19)
`'marco' in c and 'sistem' in c or 'subsist' in c` parsed as `('marco' in c and 'sistem' in c) or ('subsist' in c)`. Fixed with parentheses. No functional change today.

### 10. BRA — ISO_total CSV structurally broken (upstream R pipeline)
`BRA_total.csv` exports `geometry` as `c(-63.88..., -8.79...)` without quotes. Internal comma breaks CSV parse. **Fix:** use `BRA_total.geojson` instead. Add BRA to the `{'ARG','ECU','BRA'}` set in verification scripts.

### 11. DOM — Raw ambiguity documented (NOT a code bug)
Raw MINERD field `nivel` reports only "SECUNDARIO" without distinguishing ciclo 1 from ciclo 2. `process_DOM()` is maximalist (sets both `nivel_secbaja=1` and `nivel_secalta=1`). Inflates `secalta` count vs ISO_total.

### 12. PER — secbaja/secalta distinction via Matricula join (2026-04-20)
Padrón only has `NIV_MOD=F0` (Secundaria) without ciclo distinction. **Fix:** Join `Matricula_01.xlsx` on `COD_MOD`. For F0 rows: `nivel_secbaja=1` if any of D01-D04 > 0, `nivel_secalta=1` if any of D05-D10 > 0. Encoding switched to `utf-8-sig`.
**Impact:** secbaja 10,175 → **15,122**; secalta 10,175 → **14,908**.

### 13. DOM — ADM1 granularity mismatch fixed via ADM2 override (2026-04-20)
BID's `lac-level-1.shp` groups DOM into 10 Regiones; raw MINERD reports 32 Provincias.
**Fix:** Added ADM2 support to `pipeline/02_qc_coordinates.py`. DOM config carries `"adm_level": 2`. Default level remains 1 for other countries.
**Impact:** 0% match → **99.2% match** (6,195/6,244 MATCH).

### 14. PER — Secbaja/Secalta verification (VERIFIED 2026-04-22)
Changes from #12 confirmed flowing through steps 02 and 03 with no errors. QC match rate 99.4% ADM1 (unchanged). Coverage 105.9% (unchanged — "Pública gestión privada" variants).

---

## Known Issues in `ISO_total` Files (audit Mar 2026)

Documented in `results/reporte_irregularidades_ISO_total.md`:

| # | Issue | Severity | Countries |
|---|-------|----------|-----------|
| 1 | No sector column in any ISO_total file | 🔴 | All 21 |
| 2 | 18 countries are public-only but don't declare it | 🟠 | ARG, BLZ, BOL, BRA, CHL, COL, CRI, DOM, ECU, GTM, GUY, HND, MEX, PAN, PER, PRY, SUR, URY |
| 3 | SLV may mix up to 883 private schools silently | 🔴 | SLV |
| 4 | BRB, JAM, BHS: sector completely unknown | 🟠 | BRB, JAM, BHS |
| 5 | ARG CSV corrupt: 2,221 rows lost | 🔴 | ARG |
| 6 | ECU CSV corrupt: 6,385 rows lost (50%) | 🔴 | ECU |
| 7 | PER has 3,879 duplicate `id_centro` rows | 🔴 | PER |
| 8 | Multiple countries include preschool in K-12 | 🟠 | MEX, PER, ARG, BRA, GTM, PRY, GUY, HND, BLZ |
| 9 | DOM uses 2022-2023 data despite 2023-2024 available | 🟡 | DOM |
| 10 | JAM pipeline undocumented, not reproducible | 🟠 | JAM |

For CSV corruption: use `{ISO}_total.geojson` as the source of truth (ARG, ECU, BRA).

---

## Corrected Aggregate Numbers (March 2026)

From `results/presentacion_BID/school_coverage_assessment_final.xlsx`:

| Metric | Value |
|--------|-------|
| ISO_total (public, all levels, 21 countries) | 604,089 |
| ISO_total georeferenced | 582,870 (96%) |
| K-12 public schools (CIMA) | 447,061 |
| K-12 public + private schools (CIMA) | 532,650 |
| K-12 georeferenced | 499,325 (94%) |
| Official universe (marco total) | 564,108 |
| Coverage vs universe | 94% |
| Countries with 100% georef | 7 (MEX, PER, GTM, URY, SUR, BLZ, BRB) |
| Countries < 85% georef | 3 (BRA 79.3%, PAN 84.9%, DOM 69.5%) |

Excel HND row had a stale value (16,336) from pre-fix CIMA. Correct from current CIMA: 11,213/11,643 = 96.3%.

---

## K-12 Validation: CIMA vs ISO_total (April 2026)

Cross-validation per country, per level. Report: `results/QC/nivel_k12_iso_vs_cima.md`.

Scope: CIMA filtered to `sector=Public` (ISO is public-only except BRA). K-12 filter on both sides.

Result distribution (21 countries):
- **Match perfecto / cuasi-perfecto (Δ ≤ ±30)** — 8: BOL, BRB, ECU (after fix #8), MEX, PAN, SLV, ARG, CRI.
- **Bug R corregido (visible en datos)** — 3: BLZ, CHL, HND. R-pipeline pattern where `nivel_secbaja` uses same condition as `nivel_primaria`. HND additionally has the regex bug (matches Pre-Básica).
- **Diferencias menores a auditar** — 6: COL (−743), GTM (−324), PRY (−1,040), URY (+216), SUR (+54 secbaja), PER (+161 secalta).
- **Limitaciones metodológicas** — 2: DOM, JAM.
- **Fuente rota** — 1: BRA (CSV broken upstream).
- **Sin ISO_total** — 3: GUY, BHS, HTI.

HND R regex bug verified against `data/schools/AR/HND/HND_script.qmd`:
```r
nivel_primaria = ifelse(grepl("\\Básica\\b", nivel), 1, 0)
nivel_secbaja  = ifelse(grepl("\\Básica\\b", nivel), 1, 0)  # same regex
```

---

## Admin Granularity by Country (April 2026)

Inventory in `results/QC/admin_granularity_by_country.md`.

- **Group A — Street addresses (15)**: ARG, BLZ, BOL, BRA, CHL, COL, CRI, GTM, GUY, HND, MEX, PER, PRY, SUR, URY. Geocodable at building level.
- **Group B — Sub-municipal ADM3, no street (2)**: PAN (Corregimiento ~660), ECU (Parroquia ~1,024). Fallback: ADM3 centroid (~1-3 km precision).
- **Group C — ADM2 only (2)**: DOM (Municipio ~155), SLV (Municipio ~262). Fallback: ADM2 centroid (~5-10 km).
- **Group D — No admin / inaccessible (4)**: BHS, BRB, HTI, JAM.

Used by Panama pilot: 547 schools without coords placed at ADM3 corregimiento centroid.

---

## MEX Centroide Bias Analysis (April 2026)

Full analysis in `results/QC/MEX_geocoding_report.md`.

Detection: 8,901 MEX schools identified as centroides (≥5 schools sharing exact coord, addresses differ).

Geographic concentration:
- **Baja California**: 97.6% centroides. Tijuana: **949 schools at one point** (32.497, -117.080).
- **Chihuahua**: 70.9%. Juárez: 717 at one point.
- **Coahuila**: 72.9%. **Distrito Federal**: 57.9%.
- Southern rural states cleaner: Puebla 13.6%, Chiapas 19%, Guerrero 24%.

Error magnitude (`geocode_distance_km`):
- Median 3.03 km (adjusted ~2.6 km after subtracting geocoder's own ~1.5 km error)
- P90 17.01 km; only 13.2% within 500m; 63.8% within 5 km
- BC median 9.86 km, P90 31.2 km

Implication: 51.7% of `KEEP_ORIGINAL` (7,639/14,774) are at ≥5 clusters — they are centroides of their own declared municipality, not precise GPS. The dashboard Stage3Summary reclassifies these.

---

## Panama Pilot (Phase 1 — Proof of Concept)

Full analysis in `docs/Final_Project_Lopez_Sanchez_Draft.pdf` (GISC 28200, UChicago Winter 2026).

### Research question
How many school-age individuals (ages 6–17) live within 15, 30, and 60 minutes of the nearest school in each of Panama's 83 second-level admin units, by walking and motorized?

### Data sources
- **Schools:** MEDUCA 2024, 3,617 schools (3,092 public + 525 private), all geocoded
- **Population A:** Panama Census 2023 (INEC), 744,274 georeferenced school-age individuals
- **Population B:** WorldPop 2023, ~1 km gridded
- **Friction A:** Malaria Atlas Project (Weiss et al. 2020), motorized + walking, ~1 km
- **Friction B:** Custom OSM-derived from Geofabrik, rasterized onto MAP template
- **Boundaries:** OCHA/UNICEF (provinces, districts, corregimientos)

### Methodology
- **Fast Marching Method (FMM)** via `skfmm.travel_time`
- Schools rasterized as wavefront sources on friction grid
- Travel time classified: ≤15 min (optimal), 15–30 (adequate), 30–60 (significant), >60 (severe exclusion)
- 32 scenarios: 2 population × 4 friction × 4 age groups

### Key results
- **National (Census × MAP motorized):** 96.7% within 15 min, **98.1% within 30 min**
- **Walking:** 84.1% within 15 min, 96.3% within 30 min
- **Education gradient:** Primary universal (98.1%); high school drops to **96.0% motorized / 67.6% walking** within 30 min
- **Worst districts:** Donoso 29.4% walking exclusion, Renacimiento 27.0%, Barú 20.3%
- **WorldPop validation:** r = 0.984 vs Census at corregimiento level, but −48% to −83% underestimation in indigenous comarcas
- 9 of 83 districts excluded (Darién Gap no-data, <50 households, no Census georef)

### Friction surface construction (OSM-derived)
- OSM road features rasterized onto MAP 1 km template
- Maximum-speed-wins per pixel
- Speed by class: highway > primary > secondary > tertiary > residential > track > path
- Off-road: 5.0 km/h motorized, 2.5 km/h walking
- Conversion: `friction = 60 / (speed_kmh × 1000)` min/m

### Comparison with Castro et al. (2024)
Castro/Giambruno/Ortega analyzed 5 Amazonian countries using OSRM/UrbanPy:

| Aspect | Castro et al. | This project |
|--------|--------------|-------------|
| Schools | Public only | Public + Private |
| Transport | Walking only (OSRM) | Walking + Motorized (FMM) |
| Population | WorldPop only | Census + WorldPop |
| Method | OSRM network routing | FMM on friction raster |
| Sensitivity | None | 4 combinations |

Both find the same education gradient pattern.

---

## BID Progress Presentation (March 2026)

`results/BID_Accesibilidad_Escolar_LAC_v2.pptx` — 8 slides:

1. Title
2. Executive summary: 3-tier overview (604k → 447k → 533k)
3. Methodology
4. Coverage by country vs official universe (94% total)
5. Sectoral composition
6. Georeferencing quality (3 levels)
7. Panama proof of concept
8. Next steps

---

## Files Generated/Modified During the Audit

| File | Action | Purpose |
|------|--------|---------|
| `pipeline/01_build_cima.py` | Modified | Fixed GTM, HND, CHL, MEX, ECU, PAN, PER, BRA bugs |
| `data/schools/AR/{ISO}/processed/{ISO}_total_cima.csv` | Regenerated | All 23 countries after each fix |
| `results/reporte_irregularidades_ISO_total.md` | Created | Full audit of ISO_total issues |
| `results/bitacora_errores_pipeline.md` | Created | Per-country error log |
| `results/summary_table_final.csv` | Created | Cross-country summary |
| `results/presentacion_BID/school_coverage_assessment_final.xlsx` | Created | Master coverage Excel |
| `results/BID_Accesibilidad_Escolar_LAC_v2.pptx` | Created | BID progress presentation |
| `Final_Project_Lopez_Sanchez_Draft.pdf` | Created | Panama pilot paper |

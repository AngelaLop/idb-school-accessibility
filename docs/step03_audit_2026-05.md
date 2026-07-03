# Step 03 audit — 2026-05 (PR-1)

**Scope:** read-only audit of the dashboard step-03 ("QC Coordinates") to identify gaps before PR-2..5. No code changes in this PR. This document is the deliverable.

**Inputs verified against:**
- `pipeline/02_qc_coordinates.py` (prepass — legacy side reports)
- `pipeline/qc_core.py` (canonical resolver + finalize orchestrator + `compute_geocode_targets`)
- `pipeline/03_coverage_assessment.py` (coverage backend, NOT step-03 backend)
- `pipeline/export_dashboard_data.py` (dashboard payload builder)
- `pipeline/constants.py` (schema-v2 enums, COUNTRY_BBOX, ALIASES, COUNTRY_SCOPE)
- `accessibility-dashboard/content/pipeline-data.ts` (interfaces + step-03 def + `QC_STATIC` + `qcResults`)
- `accessibility-dashboard/app/page.tsx:559-1092` (CriticalCountries, QCMatrix, QCChart, QCRules)
- `results/dashboard/dashboard_country_summary.csv` (current payload)
- `results/dashboard/dashboard_coordinate_quality_rollup.csv` (rollup v2)
- `results/qc_finalize_summary.csv` (post-finalize counts, 23 countries)
- `results/qc_coordinate_summary.csv` (prepass output — currently only PAN row)
- `results/audit/step03_baseline_matrix_2026-05.csv` (computed in this PR by `scripts/step03_audit_baseline_matrix.py`)

**TL;DR (4 critical findings):**
1. The dashboard's per-country test cells in the QC matrix are a **mixture of three different sources** (POST-finalize columns, hardcoded `QC_STATIC` from a stale prepass, and `pct_georef_raw` from the exporter). Only test 5 (georef rate) is on the canonical pre-geocoding baseline. Tests 1, 2, 6, 7-dupPct come from `QC_STATIC`. Tests 3, 4, 7-dupGe5Pct come from POST-finalize counts. Test 1-WGS84 is hardcoded zero (never computed).
2. **`QC_STATIC` is a frozen snapshot from a prepass run that uses ADM1, not ADM2.** The canonical pipeline today resolves ADM-mismatch at the level dictated by `COUNTRY_SCOPE.final_match_level` (ADM2 for 14 countries). For these 14, `QC_STATIC.mismatch` overstates the match rate by a wide margin (PAN 94.6% → real 84.9%; SUR 95.4% → real 61.6%; GTM 98.4% → real 83.8%). PR-3 must regenerate this from finalize evidence.
3. **Hardcoded keyFindings strings cite figures that no longer match CIMA.** "DOM 69.5% georef raw" is now 82.2% (the raw extraction was widened during Jugada A); the bbox-fail country list ("HND 31, ECU 21, COL 13, CHL 6, ARG/BOL/MEX 2 c/u, BRA 1") is now (HND 73, ECU 21, COL 0, CHL 4, ARG/BOL/BRA/DOM 1, MEX 2). 
4. **`compute_geocode_targets` already exists in `qc_core` but is NOT exposed in the dashboard payload.** The funnel "step-03 identifies → step-04 processes" is invisible in the UI. PR-2 wires this through.

---

## 1. Mapping of the 7 step-03 tests to their actual source

The dashboard renders the 7 tests through three components:
- `qcResults` (in `pipeline-data.ts:528-548`) → the per-row data
- `QCMatrix` (in `page.tsx:630-706`) → the table cells
- `qcTests` (in `pipeline-data.ts:518-526`) → the column headers

The cell value for each test comes from one of three places, summarized below.

| # | Test | Display source | Threshold (page.tsx:635) | Pipeline backend | Filters by `coordinate_source`? |
|---|------|----------------|--------------------------|------------------|---------------------------------|
| 1 | WGS84 projection | **hardcoded `0`** in `pipeline-data.ts:539` | `failed if v > 0` | None (test never runs in pipeline) | n/a — never computed |
| 2 | (0,0) coords | `QC_STATIC[iso].zeroZero` (pipeline-data.ts:540, hardcoded) | `failed if v > 0` | Implicit in finalize: `02_qc_coordinates.py:574` and `qc_core.py:820-821` convert (0,0) to NaN BEFORE the rest of the QC. The signal is lost unless captured pre-conversion. | n/a — frozen snapshot |
| 3 | bbox / dentro del país | `q_out_of_bounds` from `dashboard_country_summary.csv` (pipeline-data.ts:541) | `failed if v > 0` | `qc_core.py:bbox_check()` driven by `COUNTRY_BBOX[iso]` in `pipeline/constants.py:233-257`. Final label set by `resolve_coordinate_quality()` step #3. | **No** — counts the `coordinate_quality == "out_of_bounds"` bucket, which by precedence only catches rows whose CURRENT coord is OOB. In practice pre/post coincide because Step 05 only writes in-bounds geocoded coords; but the semantic still counts post-finalize CIMA. |
| 4 | lat/lon swapped | `q_swapped` from CSV (pipeline-data.ts:542) | `failed if v > 0` | `qc_core.py:detect_swapped()`. Same precedence note as test 3. | **No** — same as test 3 |
| 5 | Georef rate ≥ 60 / 80 / 85 | `pct_georef_raw ?? pct_georef_current` (pipeline-data.ts:543) | `failed if v < 85` | `export_dashboard_data.py:_raw_coord_mask()` filters `coordinate_source ∈ {"", "original"}` → numerator is the pre-geocoding raw GPS count. | **YES** ✓ — this is the only test currently on the canonical pre-geocoding baseline. |
| 6 | Match ADM | `QC_STATIC[iso].mismatch` (pipeline-data.ts:544, hardcoded) | `failed if v < 95` | `02_qc_coordinates.py:validate_coordinates()` (prepass) writes `qc_coordinate_summary.csv:match_rate_pct`. **The hardcoded values mirror an old prepass run, NOT finalize.** Finalize uses `qc_core.admin_match()` at the level dictated by `final_match_level` (ADM1/ADM2/spatial_only/bbox_only). | **No** — frozen snapshot |
| 7a | Coord. duplicadas (% diff addr) | `QC_STATIC[iso].dupPct` (pipeline-data.ts:545) | `failed if v ≥ 20` | `02_qc_coordinates.py:check_duplicate_coordinates()` returns `dup_diff_addr_pct`. Finalize evidence column is `qc_cluster_diff_addr_exact`. | **No** — frozen snapshot |
| 7b | Cluster ≥ 5 | `dupGe5Pct` derived dynamically as `q_cluster_centroid / n_georef_current * 100` (pipeline-data.ts:532-535). | (no dedicated threshold; rendered alongside dupPct) | `qc_core.detect_clusters_exact()` populates `qc_cluster_size_exact`; finalize sets `coordinate_quality == "cluster_centroid"` for `cluster ≥ 5 AND coordinate_source == "original"`. | **Partially** — by construction `cluster_centroid` only fires for `coordinate_source=="original"`, so the count IS the pre-geocoding subset. But the denominator `n_georef_current` is post (includes filled coords), which dilutes the ratio. |

### Two more places that touch step-03 numbers

- `phases[0].steps[2].keyFindings` in `pipeline-data.ts:312-319` — **fully hardcoded text** with country lists and percentages. Not regenerated. Most stale of all.
- `phases[0].steps[2].stats` in `pipeline-data.ts:320-325` — also hardcoded.

### What the canonical pipeline knows that the dashboard doesn't surface

The CIMA already carries (post-finalize, schema-v2):
- `qc_in_bounds` per row (test 3 evidence)
- `qc_swapped` per row (test 4 evidence)
- `qc_adm1_status` / `qc_adm2_status` per row ∈ {MATCH, MISMATCH, NO_RAW_ADM, NO_POLYGON, NO_DATA, NOT_RUN} (test 6 evidence)
- `qc_cluster_size_exact` and `qc_cluster_diff_addr_exact` per row (test 7 evidence)
- `coordinate_source` ∈ {"original", "geocoded", "centroid_cascade", ""} → enables filtering to pre-geocoding baseline
- `coordinate_quality` ∈ schema-v2 (10 buckets)

`compute_geocode_targets()` in `qc_core.py:539-616` already aggregates the buckets that step-04 needs (missing/zeros/oob/mismatches/centroids/dup_addr) but its output is **only consumed in-process by Step 05**, not exported to the dashboard payload.

---

## 2. Per-country matrix: dashboard vs CIMA today vs pre-geocoding baseline

The `actual_dashboard` column shows what the dashboard renders today. The `actual_cima_post_finalize` column shows what `dashboard_country_summary.csv` carries (post-Jugada A / post-Phase B). The `baseline_pre_geocoding` column shows what the test would yield if we filtered `coordinate_source ∈ {"", "original"}` first (computed by `scripts/step03_audit_baseline_matrix.py` against current CIMA).

### Test 1 — WGS84 projection (failures)

All countries: dashboard hardcoded `0`, CIMA today `0`, baseline `0`. **No discrepancy** — but the dashboard never actually computes this; it asserts pass without evidence. After Step 02 finalize, an out-of-WGS84 value would have been coerced to NaN/(0,0) by `pd.to_numeric` and disappeared from the test surface anyway. **Recommendation:** keep test 1 as a documented invariant, but stop pretending it's verified per-country.

### Test 2 — (0, 0) coordinates (failures)

| ISO | actual_dashboard | actual_cima_post | baseline_pre_geocoding | Note |
|-----|------------------|------------------|------------------------|------|
| All except COL | 0 | 0 (always — finalize wipes them) | 0 (rows with lat==0 AND lon==0 in CIMA today) | OK |
| COL | 13 (hardcoded) | 0 | 0 | The original 13 (0,0) rows were converted to missing by finalize and counted under `q_missing` (1,628 today). The 13 number is non-recoverable from CIMA — only from a re-run on raw. |
| BHS | 0 (hardcoded) | 0 | 3 (!) | The BHS CIMA has 3 rows with lat==0 AND lon==0 that survive into the raw subset. Not currently flagged anywhere. |

**Discrepancies:** COL 13 hardcoded vs 0 in CIMA today (signal lost); BHS 3 zeros not surfaced.

### Test 3 — Out of bounds (failures)

| ISO | actual_dashboard (q_out_of_bounds) | actual_cima_post | baseline_pre_geocoding | Note |
|-----|------------------------------------|------------------|------------------------|------|
| ARG | 1 | 1 | 1 | match |
| BHS | 0 | 0 | 0 | match |
| BLZ | 0 | 0 | 0 | match |
| BOL | 1 | 1 | 1 | match |
| BRA | 1 | 1 | 1 | match |
| BRB | 0 | 0 | 0 | match |
| CHL | 4 | 4 | 4 | match |
| COL | 0 | 0 | 0 | match |
| CRI | 0 | 0 | 0 | match |
| DOM | 1 | 1 | 1 | match |
| ECU | 21 | 21 | 21 | match |
| GTM | 0 | 0 | 0 | match |
| GUY | 0 | 0 | 0 | match |
| HND | 73 | 73 | 73 | match |
| HTI | 0 | 0 | 0 | match (CIMA is 0% georef) |
| JAM | 0 | 0 | 0 | match |
| MEX | 2 | 2 | 2 | match |
| PAN | 0 | 0 | 0 | match (cascade rows are in-bounds by construction) |
| PER | 0 | 0 | 0 | match |
| PRY | 0 | 0 | 0 | match |
| SLV | 0 | 0 | 0 | match |
| SUR | 0 | 0 | 0 | match |
| URY | 0 | 0 | 0 | match |

**No live discrepancy** for test 3 — but the keyFindings text in `pipeline-data.ts:314` is stale: it lists "HND 31, ECU 21, COL 13, CHL 6, ARG/BOL/MEX 2 c/u, BRA 1". Today the live counts are HND 73 (more — 2x worse than reported, likely Islas de la Bahía + new), COL 0 (the 13 went to `missing` after Jugada A), CHL 4 (not 6).

### Test 4 — Swapped lat/lon (failures)

All countries: dashboard `q_swapped == 0`, CIMA today `0`, baseline `0`. **No discrepancy.** But `qc_core.detect_swapped` only flags when both lat∈lon-range AND lon∈lat-range AND not in-bounds — which after the GUY/PRY column-fix corrections in Step 01 is empirically zero everywhere. Test passes silently.

### Test 5 — Georef rate (% raw GPS coverage)

The dashboard shows `pct_georef_raw` from `dashboard_country_summary.csv`. This is on the canonical baseline (pre-geocoding) ✓. The 23 values match the baseline matrix exactly.

| ISO | actual_dashboard | baseline_pre_geocoding | Note |
|-----|------------------|------------------------|------|
| ARG | 97.3 | 97.3 | match |
| BHS | 96.1 | 96.1 | excluded from analysis |
| BLZ | 100.0 | 100.0 | match |
| BOL | 99.8 | 99.8 | match |
| BRA | 94.2 | 94.2 | match |
| BRB | 100.0 | 100.0 | bbox_only |
| CHL | 100.0 | 100.0 | match |
| COL | 96.7 | 96.7 | match |
| CRI | 86.8 | 86.8 | match (close to 85% threshold) |
| **DOM** | **82.2** | **82.2** | match — but `keyFindings` text says "DOM 69.5%" ⚠️ stale |
| ECU | 99.3 | 99.3 | match |
| GTM | 100.0 | 100.0 | match |
| GUY | 100.0 | 100.0 | match |
| HND | 96.4 | 96.4 | match |
| HTI | 0.0 | 0.0 | excluded |
| JAM | 99.8 | 99.8 | match (spatial_only) |
| MEX | 100.0 | 100.0 | match |
| **PAN** | **84.9** | **84.9** | match — `keyFindings` text correct |
| PER | 100.0 | 100.0 | match |
| PRY | 95.4 | 95.4 | match |
| SLV | 97.8 | 97.8 | match |
| SUR | 100.0 | 100.0 | match |
| URY | 100.0 | 100.0 | match |

**Discrepancy:** the static keyFindings text in `pipeline-data.ts:316` cites "DOM 69.5%" — wrong; CIMA today is 82.2%. The list of "8 países con 100% georef" is correct.

### Test 6 — ADM match rate (%; failed if < 95)

| ISO | actual_dashboard `mismatch` (QC_STATIC) | actual_cima `q_adm_mismatch` count | baseline_pre_geocoding match_rate_pct (computed at `final_match_level`) | Note |
|-----|----------------------------------------|------------------------------------|------------------------------------------------------------------------|------|
| ARG | 99.7 | 1457 | **96.1** | dashboard hardcoded uses ADM1 prepass; finalize uses ADM2 → real match rate is lower |
| BHS | — | — (bbox_only, NOT_RUN) | NaN | dashboard correctly shows "—" |
| BLZ | 90.5 | 23 | 91.5 | close |
| BOL | 99.5 | 71 | 99.5 | match |
| BRA | 100.0 | 862 | **99.4** | finalize ADM2 → 6 mismatches per 1000 schools, dashboard says 0 |
| BRB | — | — | NaN | dashboard "—" ok |
| CHL | 99.6 | 18 | 99.8 | close |
| COL | 99.6 | 1415 | **97.0** | dashboard hardcoded ADM1 → real ADM2 match rate is 2.6 pts lower |
| **CRI** | 99.6 | 279 | **93.5** | ⚠️ would now FAIL (< 95) under finalize ADM2 |
| DOM | 99.2 | 66 | 99.3 | match (DOM was already ADM2 in prepass) |
| ECU | 98.8 | 145 | 99.0 | close |
| **GTM** | 98.4 | 3558 | **83.8** | ⚠️ huge drop; ADM2 is much harder for GTM (35K schools, 4 K mismatches) |
| GUY | 89.9 | 20 | 95.8 | dashboard worse than baseline (?) — investigate |
| **HND** | 99.6 | 1038 | **90.7** | ⚠️ would FAIL under finalize ADM2 |
| HTI | — | — | NaN | dashboard "—" ok |
| JAM | 100.0 | 0 | 100.0 | match (spatial_only) |
| MEX | 99.9 | 2269 | **98.5** | small drop |
| **PAN** | 94.6 | 479 | **84.9** | ⚠️ already failing in dashboard (94.6 < 95); real value is even worse |
| PER | 99.4 | 2271 | 95.7 | small drop |
| **PRY** | 100.0 | 744 | **89.9** | ⚠️ would FAIL under finalize ADM2 |
| **SLV** | 96.6 | 886 | **84.3** | ⚠️ would FAIL |
| **SUR** | 95.4 | 208 | **61.6** | ⚠️ huge — known: CLAUDE.md notes ~38% Paramaribo ressort granularity issue |
| URY | 94.5 | 117 | 95.1 | match (already ADM1) |

**Discrepancy: 12 countries** would change pass/fail status under canonical finalize. **Root cause:** `02_qc_coordinates.py:1169` reads `cfg.get("adm_level", 1)` — defaulting to 1 — while finalize reads `scope.get("final_match_level")` from `COUNTRY_SCOPE`. The two diverge for ARG/BRA/COL/CRI/GTM/HND/MEX/PAN/PER/PRY/SLV/SUR (12 countries with `final_match_level=adm2` but no explicit `adm_level=2` in `COUNTRY_CONFIG`). `QC_STATIC` was generated from prepass output, so it inherits the ADM1 bias.

This is a real bug worth flagging in PR-3 — the prepass and finalize must agree on which level to evaluate, and the dashboard must consume finalize evidence (not prepass).

### Test 7 — Duplicate coordinates (% diff addr; failed if ≥ 20)

| ISO | actual_dashboard `dupPct` (QC_STATIC) | baseline `dup_diff_addr_n / n_georef_raw` | Note |
|-----|--------------------------------------|-------------------------------------------|------|
| ARG | 6.1 | 6.1 | match |
| BHS | 4.0 | 0.0 | drift (small N) |
| BLZ | 9.5 | 9.5 | match |
| BOL | 0.5 | 0.5 | match |
| BRA | 5.5 | 5.5 | match |
| BRB | 2.1 | 0.0 | drift |
| CHL | 0.0 | 0.0 | match |
| COL | 10.6 | 9.0 | -1.6 pts drift |
| CRI | 2.5 | 2.5 | match |
| DOM | 0.0 | 0.0 | match |
| ECU | 0.0 | 0.0 | match |
| GTM | 13.6 | 13.9 | +0.3 |
| GUY | 0.0 | 0.0 | match |
| HND | 0.5 | 0.6 | +0.1 |
| HTI | 0.0 | NaN | excluded |
| JAM | 0.7 | 0.0 | drift |
| MEX | 15.4 | 15.4 | match |
| PAN | 0.5 | 0.5 | match |
| PER | 1.2 | 1.2 | match |
| PRY | 0.7 | 0.7 | match |
| SLV | 0.0 | 0.0 | match |
| SUR | 13.8 | 13.4 | -0.4 |
| URY | 0.0 | 0.0 | match |

**Most countries match within ~1 pt drift.** No country crosses the 20% pass/fail threshold under either source. Test 7 is the most stable of the hardcoded set.

### Test 7b — Cluster ≥ 5 (rendered as `dupGe5Pct`)

The dashboard derives this dynamically as `q_cluster_centroid / n_georef_current` (pipeline-data.ts:532-535). Numerator is post-finalize but, by construction, `cluster_centroid` only fires for `coordinate_source=="original"`, so the count is effectively the pre-geocoding cluster count. Denominator is post (includes filled coords) → ratio is mildly diluted for PAN/DOM/SLV after Phase B-2 cascade, otherwise correct.

Pipeline `keyFindings` cites "MEX 8,916 (5.8%), COL 1,971 (4.1%), BRA 3,589 (2.8%)". Today's `q_cluster_centroid` from `dashboard_country_summary.csv`:
- MEX 8,805 (5.8% of 152,860) — close
- COL 1,048 (2.2% of 48,405) — different (was 1,971; possibly the consolidation since changed; investigate in PR-3)
- BRA 3,580 (2.8% of 128,933) — close

The decrease in COL counts is consistent with the Step 01 raw refresh and possibly a different cluster threshold; needs verification.

---

## 3. Changelog of boundary tests (test 3 + test 6)

This is the answer to the user's specific concern that "tests no están actualizados". Below: each known change to the boundary tests, with detection of whether the dashboard reflects it.

### Changes to test 3 (bbox / dentro del país)

| Change | When | Detected location | Reflected in dashboard? |
|--------|------|-------------------|-------------------------|
| `COUNTRY_BOUNDS` centralized into `pipeline/constants.py:COUNTRY_BBOX` | 2026-04-20 (per docstring) | `pipeline/constants.py:14-23` | **Yes** — `q_out_of_bounds` recomputed at every finalize, dashboard CSV reflects current values. |
| ECU bbox narrowed from `(-92, -75)` to `(-81.1, -75.2)` (mainland only, excludes Galápagos) | 2026-04-20 | `pipeline/constants.py:244` | **Yes** — `q_out_of_bounds` for ECU is 21 (Galápagos schools), surfaced in matrix. |
| BHS / BRB / HTI use `bbox_only` policy | (since pipeline-v2) | `pipeline/constants.py:COUNTRY_SCOPE` | **Partial** — these countries do show `q_out_of_bounds` in CIMA, but the dashboard QC matrix correctly returns "—" for test 6 (because `QC_STATIC.mismatch=null`). However, the cell uses `failed if v > 0` for OOB, which still applies even though for bbox_only countries the OOB count is the only signal. OK in practice. |

### Changes to test 6 (ADM match)

| Change | When | Detected location | Reflected in dashboard? |
|--------|------|-------------------|-------------------------|
| Code-based matching for ARG/BRA/ECU/MEX/CHL via INDEC/IBGE/MINEDUC/INEGI codes (Phase 1) | (recent — pre-Apr) | `02_qc_coordinates.py:46-58, 99-101, 169-171, 264-266`, `qc_core.py:919-922` | **No** — `QC_STATIC[ARG]=99.7`, `QC_STATIC[BRA]=100.0`, etc. were captured BEFORE code-based matching landed (or after, but using prepass not finalize). The hardcoded match rates differ from finalize-current values by 1–4 pts. |
| DOM uses `adm_level=2` because BID ADM1 is regions but raw is provinces | (recent) | `02_qc_coordinates.py:281-288`, `COUNTRY_SCOPE.DOM.final_match_level=adm2` | **Yes (coincidence)** — DOM dashboard 99.2 ≈ baseline 99.3. Both prepass and finalize evaluate at ADM2 for DOM, so they agree. |
| CHL uses `adm_level=2` (provincia_bid via COD_PRO_RBD → CL0{code}) | (recent) | `02_qc_coordinates.py:240-251` | **Yes** — CHL dashboard 99.6 ≈ baseline 99.8. Both at ADM2. |
| JAM uses `spatial_only` (no raw ADM column) | (recent) | `02_qc_coordinates.py:300-309`, `qc_core.py:929-937` | **Partial** — dashboard shows `mismatch=100.0` (correct for spatial_only) but the test header text "20/21 paises ejecutan este test; solo BRB queda fuera" in pipeline-data.ts:308 conflates BHS/BRB/HTI (3 bbox_only) with BRB alone. Wording is slightly misleading. |
| `ADM2_ALIASES` introduced for SUR (12 ressort aliases) | (recent — see CLAUDE.md SUR notes) | `pipeline/constants.py:301-318`, `qc_core.py:62-65, 246` | **No** — `QC_STATIC[SUR]=95.4`. Real finalize match rate is 61.6% (208 mismatches / 546 schools). Dashboard hides this. The aliases recovered ~83 of an originally larger pool, but the residual is real (Paramaribo granularity, ~38% per CLAUDE.md). The dashboard claims SUR passes test 6; finalize says it fails. |
| PAN aliases for Comarcas indígenas | 2026-04-20 (per constants.py docstring) | `pipeline/constants.py:280-286` | **Partial** — `QC_STATIC[PAN]=94.6` (already < 95, so flagged). Real finalize at ADM2 (Distrito) is 84.9%. Different test level (the aliases are for ADM1/Provincia which finalize doesn't run for PAN). Both versions flag PAN, but for different reasons. |
| `qc_match_level` per row written by finalize ∈ {ADM2, ADM1, SPATIAL_ONLY, NONE} | (current) | `qc_core.py:946-953` | **Not exposed** — the dashboard never tells the user which level was used for each country, so it's hard to interpret why GUY/BLZ/URY are at ADM1 (low absolute counts but lower match rates relative to threshold). |
| Prepass uses `cfg.get("adm_level", 1)` but finalize uses `scope.final_match_level` (default ADM1 vs canonical ADM2) | latent, not a deliberate change | `02_qc_coordinates.py:1169` vs `qc_core.py:786,939-941` | **Bug** — see Test 6 table. 12 countries diverge. The hardcoded `QC_STATIC.mismatch` values were captured from prepass (ADM1-biased) and don't reflect finalize. |

### Changes to test 7 (cluster + dup-diff-addr)

| Change | When | Detected location | Reflected in dashboard? |
|--------|------|-------------------|-------------------------|
| `CLUSTER_THRESHOLD = 5` codified | (current) | `qc_core.py:74` | Yes — `cluster_centroid` uses 5 |
| `cluster_diff_addr_exact` only flags when ≥ 2 distinct addresses in the cluster (excludes shared-campus colocations) | (current) | `qc_core.py:has_diff_address_in_cluster` | Yes — used in finalize and `compute_geocode_targets` |
| 50m radius cluster as "evidence-only" column (does NOT drive label) | (current) | `qc_core.py:285-311` | **Not exposed** — column exists in CIMA but dashboard never uses it |

---

## 4. Where step-03 mixes pre and post geocoding inadvertently

### 4.1 `CriticalCountries` panel (page.tsx:559-628)

Hardcoded to DOM and PAN. Reads `dom.georefPct` / `pan.georefPct` from the `Country` interface, which maps to `pct_georef_raw ?? pct_georef_current` (pipeline-data.ts:203). For DOM and PAN today the raw rate is ALSO the post-Phase-B-2-prebake rate (Phase B-2 added centroid_cascade rows but those don't lift `pct_georef_raw` — they lift `pct_georef_current`). The label says "georef raw" so the math is consistent with the label.

**However:** the body text says "DOM ... muy por debajo del resto regional" — based on a 69.5% mental model. Today DOM is 82.2% raw. The narrative is still defensible (DOM remains the lowest among ADM2 countries) but the relative magnitude is overstated.

**No live mixing**, but the prose has drifted from the data. Fix in PR-2 along with the funnel work.

### 4.2 `qcResults` (pipeline-data.ts:528-548)

Mixed sources, summarized:

| Field | Source | Pre or Post? |
|-------|--------|--------------|
| `wgs84` | hardcoded `0` | n/a |
| `zeroZero` | `QC_STATIC[iso].zeroZero` | Frozen pre |
| `bbox` | `row.q_out_of_bounds` (post-finalize column) | Post (coincides with pre because Step 05 only writes valid coords) |
| `swapped` | `row.q_swapped` (post) | Post (coincides with pre, same reason) |
| `georefPct` | `pct_georef_raw` | Pre ✓ |
| `mismatch` | `QC_STATIC[iso].mismatch` | Frozen pre (and biased ADM1) |
| `dupPct` | `QC_STATIC[iso].dupPct` | Frozen pre |
| `dupGe5Pct` | `q_cluster_centroid / n_georef_current` | Mixed: numerator is effectively pre (cluster_centroid only on `coordinate_source==original`); denominator is post (includes filled coords) → ratio is diluted for cascaded countries |

The `dupGe5Pct` mixing is the most subtle: PAN today shows `8 / 3,605 = 0.2%` but the "real" pre-geocoding rate is `8 / 3,068 = 0.3%`. Trivial at this scale, but conceptually wrong.

### 4.3 `keyFindings` hardcoded text (pipeline-data.ts:312-319)

This is the most blatant mixing. Specific stale claims:

- **"DOM 69.5%"** — was true pre-Jugada A. CIMA today is 82.2%. **Stale.**
- **"PAN 84.9%"** — still true today (raw rate). ✓
- **"HND 31 escuelas fuera de límites"** — today 73 (more than doubled). **Stale.**
- **"COL 13 fuera"** — today 0. The 13 (0,0) rows became `missing` not `out_of_bounds`. **Stale by definition shift.**
- **"CHL 6 fuera"** — today 4. **Stale.**
- **"ARG/BOL/MEX 2 c/u"** — today ARG=1, BOL=1, MEX=2. **Stale.**
- **"BRA 1"** — today 1. ✓
- **"8 países con 100% georef"** — today still 8 with the same set. ✓ Lucky coincidence.
- **"MEX lidera con 8,916 escuelas en cluster (5.8%)"** — today 8,805 (5.8%). Close. ✓
- **"COL 1,971 (4.1%)"** — today 1,048 (2.2%). **Stale** (raw refresh).
- **"BRA 3,589 (2.8%)"** — today 3,580 (2.8%). ✓

### 4.4 `stats` hardcoded (pipeline-data.ts:320-325)

- **"Test 6 Match ADM (19 paises): Promedio 97.9% (rango 89.9%-100%)"** — based on prepass ADM1 numbers. With finalize ADM2 the average is closer to 92% and the range extends down to 61.6% (SUR). **Stale.**
- **"Países críticos (georef <85%): DOM (69.5%), PAN (84.9%)"** — DOM is now 82.2% (still < 85% but different). **Stale.**

---

## 5. Recommendations for PR-2..5

The plan in `memory/project_step03_audit_plan.md` already maps PRs to themes. The findings above sharpen the scope:

### PR-2 (Funnel step-03 → step-04)
- **Add to scope:** while wiring `compute_geocode_targets()` into the payload, also expose `qc_match_level` per country so the matrix can label which level was tested. This costs nothing extra and resolves the "GUY / BLZ / URY ADM1 vs MEX / GTM ADM2" interpretive ambiguity.
- The `compute_geocode_targets` output should be exported as a new payload table (`dashboard_geocode_targets.csv`) keyed by `iso`. The frontend funnel panel can read it and show: missing + zeros + out_of_bounds + mismatches + cluster_centroid_5plus + dup_diff_addr (for the compare flow).
- **Do NOT** repurpose `qc_finalize_summary.csv` for this — that file is the post-Phase-B view; the funnel must show targets BEFORE Phase B intervention (`compute_geocode_targets` already filters correctly).

### PR-3 (Audit and update the test suite)
- **Fix the prepass / finalize ADM-level divergence.** Either:
  - (a) Make `02_qc_coordinates.py:1169` consult `COUNTRY_SCOPE[iso]["final_match_level"]` first, falling back to `cfg.get("adm_level", 1)`, OR
  - (b) Deprecate the prepass entirely and have step-03 emit only what `qc_core.finalize_cima_evidence` produces. Aligns with the CLAUDE.md note "Step 02 is the SOLE owner of `coordinate_quality`".
- Regenerate per-country test stats from finalize evidence (not prepass). For `mismatch %`: count `coordinate_quality == "adm_mismatch"` against `n_georef_raw` (the canonical pre-geocoding denominator).
- Frontend: the QCMatrix `failed if v < 95` rule should fire on `final_match_level`-aware match rate. Cells for `bbox_only` countries should render "—" for tests 6 and 7 (currently test 6 ok via QC_STATIC=null; test 7 dupPct shows BHS=4.0, BRB=2.1 which is misleading because for tiny populations duplicates are uninterpretable).
- Document the policy table per country in `docs/step03_country_test_policy.md` (the PR-3 deliverable per the plan).

### PR-4 (Replace QC_STATIC with payload-driven values)
- Step 02 prepass (or finalize, see PR-3 decision) emits a baseline-pre-geocoding test summary at `results/dashboard/dashboard_qc_baseline.csv` with one row per country and columns matching `pipeline-data.ts:QCResult`.
- `pipeline-data.ts` reads from this CSV instead of `QC_STATIC`.
- Cross-check: if the new numbers diverge from the old hardcoded ones by > 3 pts on `mismatch` for any country, surface that diff in the PR description so Ceci can validate before merge. (Per `feedback_no_invention.md` — every number traces to a verified file.)
- **Specifically validate these countries** (from §2): CRI, GTM, HND, PAN, PRY, SLV, SUR. All would change pass/fail status under finalize ADM2.

### PR-5 (Toggle Total/Pública/Privada + cleanup)
- Sector-aware tests: `compute_geocode_targets()` must accept a sector filter; the baseline matrix must be regenerated per sector.
- Cleanup additions surfaced in this audit:
  - **Rewrite `keyFindings` and `stats` strings** for step-03 (pipeline-data.ts:312-325) to be derived from the payload, not hardcoded prose. Currently 5 of the 7 quoted figures are stale.
  - **Rewrite `CriticalCountries` body text** (page.tsx:570-580) to be data-driven (the prose says "DOM has lowest georef regionally"; verify dynamically against `pct_georef_raw` ranking).
  - **Drop test 1 from the matrix** or move it to a documented invariant section. Hardcoded zero adds noise.
  - **Surface `qc_match_level` and `final_match_level` per row** in QCMatrix (small badge next to ISO) so the user can read "PAN ADM2" vs "URY ADM1" without leaving the page.
  - **Decision pending:** whether to keep BHS/HTI in the QC matrix at all (they're `analysis_included=False`). Today they appear because `qcResults` filters by `pipeline_enabled`, not `analysis_included`. Worth aligning with the rest of the dashboard.

---

## Appendix A — Reproducibility

- Baseline matrix: `uv run python scripts/step03_audit_baseline_matrix.py` → writes `results/audit/step03_baseline_matrix_2026-05.csv`. Reads only current CIMA + `pipeline/constants.py`. No side effects.
- Dashboard payload: regenerated by `pipeline/export_dashboard_data.py` on each pipeline run; do not hand-edit `dashboard_country_summary.csv`.
- This audit document covers state as of `git rev-parse HEAD` at write time (branch `main`, commit `2b48080 docs: add QC unified spec + Diccionario BID extract` based on git status).

## Appendix B — Numbers cited in this document

All numeric claims in §2-§4 are from one of:
- `results/dashboard/dashboard_country_summary.csv` (committed)
- `results/qc_finalize_summary.csv` (committed)
- `results/audit/step03_baseline_matrix_2026-05.csv` (produced by this PR)
- `accessibility-dashboard/content/pipeline-data.ts:QC_STATIC` (committed)

No estimated or interpolated values. Where a CIMA file lacked the relevant evidence column (HTI: no coords at all → match-rate undefined), the cell is marked NaN/—.

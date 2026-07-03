# Coordinate Quality Spec — 2026-05-09

**Scope.** Authoritative reference for the `coordinate_quality`, `qc_scope_class`, and `include_in_spatial_indicators` columns produced by `pipeline/02_qc_coordinates.py` (sole owner). Audience: pipeline maintainers + downstream indicator consumers.

**Inputs verified against:**
- `pipeline/qc_core.py::resolve_coordinate_quality` (label resolver, line ~490)
- `pipeline/qc_core.py::_spatial_indicator_policy` (include policy, line ~1126)
- `pipeline/qc_core.py::rescue_near_border_polygons` (near-border rescue, post 2026-05-09)
- `pipeline/constants.py` (`COUNTRY_SCOPE`, `COORDINATE_QUALITY_PRECEDENCE`, `QC_ADM_STATUSES`, `QC_SCOPE_CLASS`, `COUNTRY_BBOX`)
- `data/schools/AR/LAC_schools_k12_with_context.csv` (529,635 schools, current snapshot)
- Per-country `data/schools/AR/{ISO}/processed/{ISO}_total_cima.csv` (47-col enriched CIMA)

**TL;DR (5 findings).** Numbers reflect post §5.1+§5.2+§5.3+H1 (review-cycle 2026-05-10).
1. **Taxonomy is sound.** Eleven mutually-exclusive `coordinate_quality` values, resolved by precedence order (worst-flag-wins). The split into a separate additive `qc_scope_class` (territorial scope) is the right fix for islands/remote territories that previously got buried inside `out_of_bounds`.
2. **The include policy is conservative by default.** 92.5% of LAC k-12 (489,838) auto-include; 7.5% split between explicit-exclude (4,312 = 0.81%) and review-required (35,485 = 6.70%). The review bucket is dominated by three labels: `cluster_centroid` (13,950), `adm_mismatch` (13,539), `geocoder_disagrees` (7,772).
3. **Three anomalies / policy gaps identified — all fixed in commit ffbe8a3 + H1:**
   (a) §5.1 (FIXED) — 2 MEX Chiapas schools (07KTV0394A, 07KTV0266F) with `coordinate_quality=geocoder_disagrees` were auto-included via the near-border rescue. The new severity gate holds high-severity labels at NaN regardless of `qc_adm1_status`.
   (b) §5.3 (FIXED) — 1 PAN school (id=6895, ESC. TANGERINE, Bocas del Toro) with `geocoded_centroid` × `near_border_review` was stuck at NaN. Now auto-includes because the cascade fill is polygon-anchored.
   (c) §6.1 (DOC) — 2 schools (ARG 180089800, COL 286573004400) hold `gps_validated` AND `qc_scope_class=outside_country` simultaneously. Both correctly excluded by rule 1; the label combo is a BID polygon sliver and tracked in the backlog.
4. **`boundary_zone` auto-included with adm1-only gate (H1).** All 2,255 `boundary_zone` rows auto-include via §5.2 EXCEPT 48 (URY 46 + BOL 2) where `final_match_level=adm1` and `qc_adm1_status=MISMATCH` — these have no fall-back validation level and stay NaN. Largest country impact: GTM +5.0 pp (1,109 schools, concentrated Huehuetenango GT13=302), SUR +20.4 pp (Paramaribo ressort granularity).
5. **Missing per-country `adm{level}_code_col` configs cap the near-border rescue at 121/300 schools.** The 175 unrescued near-border schools concentrate in countries whose Step-02 config never wired up code-based admin matching: SLV (70), MEX (65 unrescued at adm2), GUY (31), PAN (25), GTM (16), URY (13), PER (7), HND (6), BLZ (3), CRI (2), SUR (2). Tracked as a backlog item.

---

## 1. Pipeline ownership of these columns

Step 02 (`pipeline/02_qc_coordinates.py --mode finalize`) is the **sole writer** of:

| Column | Producer | Notes |
|---|---|---|
| `coordinate_quality` | Step 02 (`finalize_cima_evidence`) | 11 mutually-exclusive labels, precedence-ordered |
| `coordinate_quality_reason` | Step 02 | machine-readable trace of which rule fired |
| `qc_scope_class` | Step 02 | territorial scope (6 values, additive) |
| `include_in_spatial_indicators` | Step 02 | nullable (True / False / `<NA>`) |
| `qc_centroid_bias` | Step 02 | `normal` / `high` / `unknown` for centroid fills |
| `adm1_pcode`, `adm2_pcode` | Step 02 | from spatial join + near-border rescue |
| `qc_*` evidence columns | Step 02 | distance to polygon, cluster size, ADM statuses, etc. |

Steps 03 (coverage) and 04 (geocode) read these columns — they never write them. Step 04 may write geocoder evidence (`acceptance`, `geocode_distance_km`, etc.); the final label is computed in Step 02 on the next finalize pass. Step 05 (school base) reads `coordinate_quality` + `include_in_spatial_indicators` to assemble the published 14-col `{ISO}_schools_clean.csv`.

`COORDINATE_QUALITY_PRECEDENCE` from `constants.py:388`:

```
missing → out_of_bounds → swapped → adm_mismatch → cluster_centroid →
geocoder_disagrees → boundary_zone → geocoded_centroid → geocoded_street →
gps_validated → gps_unverified
```

First match wins. The policy is "worst flag wins": even if a school is `gps_validated` at adm1 level, an adm2 mismatch at the country's `final_match_level=adm2` produces `adm_mismatch`, not `gps_validated`.

---

## 2. The 11 `coordinate_quality` values

### LAC k-12 distribution (529,635 schools, post §5.1+§5.2+§5.3+H1, 2026-05-10)

| value | n | % | include=True | include=False | include=NaN | dominant scope_class |
|---|---:|---:|---:|---:|---:|---|
| `gps_validated` | 478,044 | 90.26% | 478,042 | 2 | 0 | inside_mainland_bbox |
| `cluster_centroid` | 13,950 | 2.63% | 0 | 0 | 13,950 | inside_mainland_bbox |
| `adm_mismatch` | 13,587 | 2.57% | 0 | 0 | 13,587 | inside_mainland_bbox (incl. 48 URY+BOL reclassified by O1) |
| `geocoder_disagrees` | 7,772 | 1.47% | 0 | 0 | 7,772 | inside_mainland_bbox |
| `geocoded_street` | 6,495 | 1.23% | 6,495 | 0 | 0 | inside_mainland_bbox |
| `missing` | 4,236 | 0.80% | 0 | 4,236 | 0 | missing (4,043) + bbox (130) + outside (60) + near (3) |
| `geocoded_centroid` | 2,861 | 0.54% | 2,861 | 0 | 0 | inside_mainland_bbox |
| `boundary_zone` | 2,207 | 0.42% | 2,207 | 0 | 0 | inside_mainland_bbox (post O1: never fires at adm1-only × MISMATCH) |
| `gps_unverified` | 363 | 0.07% | 178 | 9 | 176 | mixed (BRB bbox + near-border) |
| `out_of_bounds` | 120 | 0.02% | 55 | 65 | 0 | remote_territory (55) + outside (65) |
| `swapped` | 0 | 0.00% | — | — | — | — |

Total: 489,838 True (92.49%) / 4,312 False (0.81%) / 35,485 NaN (6.70%).

`swapped` is empty in the current LAC snapshot — the bug never appeared at scale; the rule remains in the resolver as a guard.

---

### 2.1 `gps_validated` — 478,044 (90.26%)

**Definition.** Original GPS coordinate, present, in-bounds, ADM-matched at the country's `final_match_level`, and the school is **not** part of a same-coord cluster of size ≥ 5.

**Decision in current policy.** `include=True` iff `qc_scope_class ∈ {inside_mainland_bbox, remote_territory_or_island}` (478,042). Falls to `False` only when `qc_scope_class=outside_country` (2 anomalous rows, see §6.1).

**Per-country top 10 (counts):** MEX 137,252, BRA 117,197, PER 50,734, COL 44,897, ARG 32,461, GTM 17,937, BOL 15,373, ECU 14,554, HND 10,067, CHL 8,305.

**Rationale.** This is the safe-by-construction bucket. No change recommended.

---

### 2.2 `geocoded_street` — 6,495 (1.23%)

**Definition.** Coordinate filled by the Step-04 geocoder cascade with **street-level precision** (ArcGIS score ≥ 95). `coordinate_source = "geocoded"`, `geocode_precision = "street"`.

**Decision.** `include=True` always (auto-include). The combination of (high geocoder confidence + score ≥ 95 + post-fill bbox check) is at least as trustworthy as a raw GPS without admin verification.

**Per-country:** BRA 5,715, ARG 347, COL 382, PRY 51 (the only countries where ArcGIS street precision was actually achieved at scale).

---

### 2.3 `geocoded_centroid` — 2,861 (0.54%)

**Definition.** Coordinate filled by either (a) the geocoder cascade with score 90 ≤ s < 95 (treated as centroid precision), or (b) the PAN-style ADM3 centroid cascade (`coordinate_source = "centroid_cascade"`).

**Sub-classification (`qc_centroid_bias`):**
- `normal` — centroid of an admin polygon ≤ 314 km² (~ 10 km radius). 1,372 rows.
- `high` — centroid of a polygon > 314 km². 829 rows. Bias of order 5–15 km.
- `unknown` — geocoder fill at score 90-94 where polygon area wasn't measured. 660 rows.

**Per-country breakdown:**

| ISO | normal | high | unknown |
|---|---:|---:|---:|
| ARG | 0 | 0 | 104 |
| BOL | 0 | 0 | 3 |
| BRA | 0 | 0 | 276 |
| COL | 0 | 0 | 239 |
| DOM | 785 | 772 | 0 |
| ECU | 20 | 31 | 0 |
| HND | 0 | 0 | 6 |
| PAN | 499 | 20 | 0 |
| PRY | 0 | 0 | 32 |
| SLV | 68 | 6 | 0 |

DOM and PAN dominate: their cascade fills (Phase B-2) hit a mixture of `normal` and `high` polygons. ECU is mostly cascade. ARG/BRA/COL/HND/PRY are ArcGIS centroid-precision fills (`bias=unknown`).

**Decision.** `include=True` for 2,860 of 2,861. The 1 NaN is the PAN edge case in §6.2.

---

### 2.4 `cluster_centroid` — 16,379 (3.09%)

**Definition.** Original GPS that lands in a same-exact-coord cluster meeting one of two patterns (extended rule applied 2026-05-13):

| Pattern | Reason code | Trigger |
|---|---|---|
| Classical placeholder | `cluster_ge5` | cluster size ≥ 5 (any diff_addr/admin signal) |
| Sub-5 placeholder (n=3,4) | `cluster_3_4_diff_admin_locality` | cluster size ∈ {3,4} AND ≥ 2 distinct values in raw_adm1, raw_adm2 or raw_locality |
| Sub-5 placeholder (n=2) | `cluster_2_diff_admin_locality` | cluster size = 2 AND ≥ 2 distinct raw_adm/locality values AND no frontier rescue |

**Frontier rescue (n=2 only).** A n=2 cluster with diff raw_adm1/raw_adm2 is *not* labeled cluster_centroid if at least one member's coord sits within 5 km of its declared raw polygon boundary (`qc_distance_to_raw_polygon_km < 5`). Rationale: schools straddling an admin boundary legitimately disagree at the categorical level; the placeholder pattern is when same coord encodes physically distinct schools, not boundary edge cases.

**Why the sub-5 rule uses raw_adm/locality, not raw_street.** The original `qc_cluster_diff_addr_exact` signal compares free-form address strings — too noisy at n=2,3,4 because legitimate same-campus schools often have slightly different address strings (e.g. "Queen's Highway" vs "Queens Highway, Moss Town"). `raw_adm1/adm2/raw_locality` are categorical (normalized to known MoE admin units) — a clean diff is a strong placeholder signal even at n=2.

**LAC-wide impact (extension 2026-05-13).** Re-labeled 2,419 schools from gps_validated/geocoder_disagrees/etc. to cluster_centroid (0.46% of LAC k-12). Top contributors: BRA +1,368, MEX +408, ARG +222, COL +187, GTM +73, PER +69, URY +30, CRI +24, PRY +14, PAN +12, BLZ/HND/SUR small. include_in_spatial_indicators=True for LAC drops ~0.40%.

**Per-country (top 5 post-extension):** MEX 9,213 (56%), BRA 4,981, COL 1,228, ARG 301, PER 283. BHS 10 (5 Nassau MoE + 3 Abacos + 2 Black Point/Forest).

`CLUSTER_THRESHOLD = 5` (classical). Sub-5 threshold is implicit in the diff_admin_locality signal computed by `qc_core.has_diff_admin_or_locality_in_cluster`. Schools sharing the same address are excluded from the classical cluster signal (`has_diff_address_in_cluster` — preserves shared-campus / co-locations).

**Size distribution (LAC, classical `cluster_ge5` bucket only — pre-extension snapshot):**

| size bucket | n | % |
|---|---:|---:|
| 5–9 | 4,335 | 31.1% |
| 10–19 | 2,779 | 19.9% |
| 20–49 | 2,318 | 16.6% |
| 50–99 | 1,274 | 9.1% |
| 100–499 | 1,581 | 11.3% |
| 500–999 | 1,663 | 11.9% |

Median cluster size = 19, mean = 156, max = 949. Long-tailed. (Distribution does not include the 2,419 sub-5 placeholders added 2026-05-13 by the extended rule — those are all size ∈ {2,3,4} by construction.)

**Per-country × bucket:** MEX is the only country with size ≥ 100 clusters (4,407 of MEX's 8,805 are ≥ 100; 3,244 are ≥ 500). All other countries' clusters are ≤ 99 and most concentrate at size 5–19. This is consistent with MEX's known multi-level encoding: `KTV/KTU/MTU` cod_centro variants share a single GPS.

**Decision.** `include=NaN` for all 13,950 (review). **Rationale**: a cluster-of-5+ at exact lat/lon with distinct addresses is geographically equivalent to a centroid (the original recorder placed multiple distinct schools at the same point). The dashboard should flag and let the analyst decide; for automated indicators, exclude.

**Refinement candidate (deferred).** Splitting by cluster size could promote a fraction of size 5–9 to True:
- size 5–9 (4,335): plausible legitimate co-location (e.g., government compound) → arguable for adm1-level
- size 100+ (3,244 MEX): unambiguously synthetic centroid → must remain NaN/False

For now keep all 16,379 as NaN until a per-country sample audit decides whether the split is safe. See §7.

**Phase B-1 recovery loop (note, 2026-05-13).** `cluster_centroid` rows that have a populated raw address are candidates for re-geocoding via Phase B-1 (ArcGIS/Photon/Nominatim). A successful street-level fill moves a row from `cluster_centroid` (placeholder) → `geocoded_street` (real coord). This closes the loop: detect placeholder → label → re-geocode if address available → recover. Concrete BHS example: 10 cluster_centroid (5 Nassau placeholder + 3 Abacos + 2 Black Point/Forest) all have populated `Address` column; Step-04 B-1 run would likely recover most. LAC-wide there are ~14-15K cluster_centroid rows with raw_street present (mostly MEX, BRA, COL) — re-run candidate after this rule lands.

---

### 2.5 `adm_mismatch` — 13,539 (2.56%)

**Definition.** Original GPS, in-bounds, but the spatial join's resulting polygon disagrees with the school's raw-declared admin name **at the country's `final_match_level`**. `adm1_mismatch` for adm1-level countries (4 countries); `adm2_mismatch` for adm2-level countries (15 countries).

**Per-country × reason:**

| ISO | adm1_mismatch | adm2_mismatch |
|---|---:|---:|
| ARG | 68 | 1,321 |
| BLZ | 23 | 0 |
| BOL | 69 | 0 |
| BRA | 0 | 644 |
| CHL | 0 | 18 |
| COL | 0 | 1,083 |
| CRI | 0 | 264 |
| DOM | 0 | 76 |
| ECU | 0 | 256 |
| GTM | 0 | 2,449 |
| GUY | 20 | 0 |
| HND | 0 | 987 |
| MEX | 0 | 1,783 |
| PAN | 0 | 479 |
| PER | 0 | 2,209 |
| PRY | 0 | 736 |
| SLV | 3 | 883 |
| SUR | 0 | 97 |
| URY | 71 | 0 |

Top 5: GTM 2,449, PER 2,209, MEX 1,783, ARG 1,389, COL 1,083.

**Distance distribution `qc_distance_to_raw_polygon_km`:**
- NaN: 5,197 (38%) — countries / rows where the raw polygon couldn't be located (typically when raw admin name doesn't match the BID polygon table at all).
- < 1 km: 3,042 (37% of non-NaN) — schools sitting essentially on the polygon edge; many are candidates for `boundary_zone` softening that didn't qualify because the geocoder didn't run.
- 1–5 km: 2,074 (25%) — likely suburb-of-cabecera or genuine name disagreement.
- 5–10 km: 733 (9%)
- 10–25 km: 626 (8%)
- 25–100 km: 905 (11%)
- > 100 km: 962 (12%) — real mismatches (different country region, BID polygon stale, or coord error).

Median (non-NaN) = 2.47 km; p90 = 127.77 km; max = 2,923 km.

**Decision.** `include=NaN` for all 13,539. The wide distance distribution argues against an automatic include rule based on distance alone — a school 3 km from the raw polygon could be an honest neighbor or a real error. The conservative reading is that adm2-level indicators **must** exclude these (the polygon assignment by the spatial join is at a different ADM2 from the declared one), but adm1-level / adm0-level indicators could safely include the in-bounds rows.

**Per-level interpretation (informational; not yet enforced in policy):**
- **adm0 indicators** — safe to include all 13,539 (they're inside the country's bbox/territory; the mismatch is internal to the admin tree).
- **adm1 indicators** — safe iff `qc_adm1_status = MATCH`. Most adm2-level mismatches still match at adm1 (e.g., COL school in correct department, wrong municipio). The current policy emits `adm_mismatch` even when adm1 matches but adm2 doesn't.
- **adm2 indicators** — exclude.

If consumers (dashboard, report generator) want to differentiate, they should read `qc_adm1_status` directly. The current single-boolean `include_in_spatial_indicators` cannot represent the level-specific policy without tagging `adm_mismatch` rows with extra metadata. Tracked as a future refinement (see §7).

---

### 2.6 `geocoder_disagrees` — 7,772 (1.47%)

**Definition.** The geocoder ran on this school and produced strong evidence that the original GPS is wrong. All 7,772 rows have `acceptance = FLAG`. Conditions for the label fire (`_strong_geocoder_disagreement` in `qc_core.py`):
- Geocoder result has high confidence (street precision OR centroid precision with adm-match).
- Distance from original GPS to geocoded result ≥ 10 km **OR** ADM2 disagrees and the geocoded result is strong evidence.

**Per-country (acceptance=FLAG):** MEX 4,467 (57%), BRA 1,262, COL 1,020, GTM 432, ARG 406, PER 108, HND 28, BOL 15, CRI 12, SUR 11, URY 6, PRY 5.

**Decision.** `include=NaN` for 7,770 (correct). **2 rows are anomalously `True`** (MEX 07KTV0394A, MEX 07KTV0266F) — both in Chiapas, both `qc_scope_class=near_border_review`, lifted to True by the near-border rescue path because `qc_adm1_status=MATCH`. **This is a policy bug.** See §6.1.

**Recommended fix.** Extend `_spatial_indicator_policy` to never lift `geocoder_disagrees` (or other "high-severity" labels: `adm_mismatch`, `swapped`, `missing`, `out_of_bounds`) to True via the near-border rescue path, regardless of `qc_adm1_status`. The rescue should only operate on `gps_validated` and `gps_unverified` quality.

---

### 2.7 `boundary_zone` — 2,255 (0.43%)

**Definition.** A softened version of `adm_mismatch`. Fires when ALL of:
- `coordinate_source = "original"` (real GPS, not a fill)
- `qc_distance_to_raw_polygon_km < 5` (school sits within 5 km of the raw-declared polygon edge)
- `geocode_distance_km < 5` (geocoder result is within 5 km of the GPS — corroborates location)

In effect: the spatial join disagrees with raw, BUT the school is essentially on the polygon edge AND independent geocoder evidence agrees with the GPS.

**Per-country (top 6):** GTM 1,109 (49%), MEX 486, BRA 218, SUR 111, COL 78, ARG 68.

**Distance:** by construction all distances < 5 km. Median 0.39 km, p90 2.19 km, max 4.99 km.

**Decision.** `include=NaN` for all 2,255 (review).

**Recommended change.** **Promote `boundary_zone` to `include=True`** by default. The construction already gates on (a) original GPS, (b) <5km from polygon edge, (c) geocoder corroborates within 5 km. Any of these three checks alone would be insufficient; the conjunction is stronger evidence than `gps_unverified` (which we currently include for in-bbox cases). Boundary-zone schools are conceptually a coord that disagrees with the reported admin name but agrees with reality. See §5.

---

### 2.8 `gps_unverified` — 363 (0.07%)

**Definition.** Coordinate present, but no rule produced an affirmative validation:
- Country with `final_match_level = bbox_only` (BRB) cannot reach `gps_validated` by construction — best-case is `gps_unverified`.
- Country with no admin polygon coverage at the school's coord (`qc_adm1_status = NO_POLYGON` or `NO_RAW_ADM`) cannot validate the admin chain.
- Geocoder ran with `acceptance = FLAG` and uncertain precision (not strong enough for `geocoder_disagrees`).

**Per-country × scope:**

| ISO | inside_mainland_bbox | near_border_review | outside_country |
|---|---:|---:|---:|
| ARG | 2 | 1 | 0 |
| BLZ | 0 | 3 | 0 |
| BOL | 0 | 1 | 2 |
| BRA | 0 | 7 | 0 |
| BRB | 94 | 0 | 0 |
| CHL | 0 | 5 | 0 |
| COL | 0 | 0 | 1 |
| CRI | 1 | 2 | 0 |
| GTM | 0 | 16 | 0 |
| GUY | 0 | 31 | 0 |
| HND | 0 | 6 | 0 |
| MEX | 0 | 65 | 0 |
| PAN | 0 | 25 | 0 |
| PER | 0 | 7 | 4 |
| SLV | 1 | 70 | 1 |
| SUR | 2 | 2 | 1 |
| URY | 0 | 13 | 0 |

**Decision.**
- `inside_mainland_bbox` (100): include=True (these are coords that just couldn't be validated, but they're in the country bbox and don't trigger any negative flag).
- `near_border_review`: include=True iff the rescue lifted them (78 of 254 rescued via `qc_adm1_status=MATCH`); the rest stay NaN.
- `outside_country`: include=False (9 schools, mostly PER/BOL/SUR data anomalies).

**BRB-specific.** All 94 BRB schools land here by design. BRB is `bbox_only` — no ADM1/ADM2 polygons exist in the BID layer for Barbados, so the resolver can never reach `gps_validated`. They auto-include because `qc_scope_class=inside_mainland_bbox`. This is correct: country-level (adm0) indicators on Barbados use these schools. See §5 BRB note.

---

### 2.9 `missing` — 4,236 (0.80%)

**Definition.** No usable coordinate. Either `lat/lon` are NaN, or one/both is exactly `0.0` (treated as placeholder per the 2026-05-09 universal LAC rule).

**Per-country (top 7):** COL 1,266, BRA 1,043, CRI 650, ARG 455, HND 423, PRY 258, ECU 56.

**Decision.** `include=False` for all 4,236 (auto-exclude).

The scope class for `missing` is itself heterogeneous because `qc_scope_class` runs first and may classify the row by other signals before falling through:
- `missing` (scope) + `missing` (quality): 4,043
- `inside_mainland_bbox` + `missing`: 130 (rows where lat=0 only — coordinate had partial info)
- `outside_country` + `missing`: 60
- `near_border_review` + `missing`: 3

---

### 2.10 `out_of_bounds` — 120 (0.02%)

**Definition.** Coordinate is numerically valid (not impossible per EPSG:4326) but falls outside `COUNTRY_BBOX`. The resolver does not look at `qc_scope_class` here — that's an additive scope check.

**Per-country × scope:**

| ISO | outside_country | remote_territory_or_island |
|---|---:|---:|
| ARG | 1 | 0 |
| BOL | 1 | 0 |
| BRA | 0 | 1 |
| CHL | 0 | 4 |
| COL | 0 | 26 |
| ECU | 0 | 21 |
| HND | 63 | 1 |
| MEX | 0 | 2 |

**Decision.**
- `remote_territory_or_island` (55): include=True. These are San Andrés (COL 26), Galápagos (ECU 21), Easter Island/Juan Fernández (CHL 4), Fernando de Noronha (BRA 1), Isla Guadalupe / Revillagigedo (MEX 2), Islas de la Bahía (HND 1). All are inside national ADM0 territory but outside the mainland bbox; the qc_scope_class field correctly flags them and overrides the bbox-only signal for include policy.
- `outside_country` (65): include=False. The HND 63 cluster is the dominant case — likely a data error (raw geocoded outside Honduras). Investigate as a backlog item (see §7).

---

### 2.11 `swapped` — 0

**Definition.** Heuristic flags a likely lat/lon swap (e.g., lat in a typical lon range and vice-versa). The detection rule is in `qc_core.py::detect_swap_candidates`.

**Decision in policy.** `include=False` (would be auto-excluded if any row landed here).

**Empty in current data.** Either real swaps are caught earlier in Step 01's text normalization, or they're not present in any of the 21 ministry sources.

---

## 3. `qc_scope_class` — territorial scope (additive)

Six values, computed independently from `coordinate_quality`. The **purpose** is to distinguish a valid national-territory point that happens to be outside the mainland bbox (e.g., Galápagos) from a truly bad/outside-country coordinate. Without `qc_scope_class`, the resolver would label both as `out_of_bounds` and downstream consumers would have no way to distinguish.

| value | meaning | LAC count |
|---|---|---:|
| `inside_mainland_bbox` | Inside `COUNTRY_BBOX` and inside ADM0 territory | 525,154 (99.16%) |
| `near_border_review` | Outside ADM0 territory but within 5 km of the boundary | 300 (0.057%) |
| `remote_territory_or_island` | Outside `COUNTRY_BBOX` but still inside ADM0 territory | 55 (0.010%) |
| `outside_country` | Outside ADM0 territory and not near border | 134 (0.025%) |
| `missing` | No usable coordinate | 4,043 (0.764%) |
| `invalid_numeric` | Impossible EPSG:4326 value | 0 |

The 2,255 `boundary_zone` and 13,539 `adm_mismatch` rows are all `inside_mainland_bbox` — they're inside the country, just disagreeing with the declared internal admin.

---

## 4. `include_in_spatial_indicators` — current policy

From `pipeline/qc_core.py::_spatial_indicator_policy` (post §5.1–§5.3 + H1, 2026-05-10):

```python
1. scope_class ∈ {missing, invalid_numeric, outside_country}      → False
2. quality   ∈ {missing, swapped}                                  → False
3. quality   ∈ {geocoded_street, geocoded_centroid}                → True   (§5.3 — fill is polygon-anchored)
4. quality == boundary_zone                                        → True   (§5.2 — at adm1-only × MISMATCH the resolver
                                                                              already emits adm_mismatch instead per O1)
5. scope_class == near_border_review:
     a. quality ∈ {geocoder_disagrees, adm_mismatch, swapped,      → NaN    (§5.1 severity gate)
                    missing, out_of_bounds, cluster_centroid}
     b. quality == gps_validated                                   → True   (rescue both adm1+adm2)
     c. qc_adm1_status == MATCH AND quality == gps_unverified      → True   (adm1-only rescue)
     d. else                                                       → NaN
6. quality   ∈ {adm_mismatch, geocoder_disagrees, cluster_centroid} → NaN
7. scope_class ∈ {inside_mainland_bbox, remote_territory_or_island} → True
8. else                                                            → False
```

**Walkthrough (top to bottom).** A row is checked against rules 1–8 in order. The first rule that fires sets the include value. Rule 5c is the only conditional branch that depends on `qc_adm1_status`; the rest are determined by `qc_scope_class` × `coordinate_quality`. The adm1-only safety property for boundary_zone is now enforced upstream in `resolve_coordinate_quality` (O1) rather than at the policy.

**Resulting matrix (LAC k-12, post §5.1+§5.2+§5.3+H1, 2026-05-10):**

```
                              True    False    NaN
inside_mainland_bbox        489,664      130  35,307
remote_territory_or_island       55        0       0
near_border_review              119        3     178
outside_country                   0      136       0
missing                           0    4,043       0
TOTAL                       489,838    4,312  35,485
```

Total include=True: 489,838 (92.49%). NaN: 35,485 (6.70%). False: 4,312 (0.81%).

---

## 5. Proposed policy changes (3 surgical fixes)

### 5.1 Block high-severity labels from near-border rescue

**Implemented (commit ffbe8a3, 2026-05-10).** The rescue branch (rule 5 in the updated order) previously lifted ANY school in `near_border_review` to True if `qc_adm1_status=MATCH`, regardless of label severity. Two MEX schools with `coordinate_quality=geocoder_disagrees` were silently auto-included. A label-severity gate now blocks high-severity labels from being rescued:

```python
if scope_class == "near_border_review":
    if quality in {"geocoder_disagrees", "adm_mismatch", "cluster_centroid",
                   "boundary_zone", "swapped", "missing", "out_of_bounds"}:
        return pd.NA  # don't rescue high-severity labels
    if quality == "gps_validated":
        return True
    adm1_status = ...
    if adm1_status == "MATCH" and quality == "gps_unverified":
        return True
    return pd.NA
```

The rescue is then explicit about which labels it can promote: only `gps_validated` (full rescue) or `gps_unverified` with adm1 MATCH (partial rescue). All other near-border labels stay NaN.

**Effect.** 2 MEX rows flip from True → NaN. No other rows affected (verified: only 2 rows of `geocoder_disagrees × near_border_review × True` exist in the snapshot).

### 5.2 Auto-include `boundary_zone`

**Implemented (commit ffbe8a3, 2026-05-10).** Promoted to `True`. The label is by construction (a) original GPS, (b) <5 km from raw polygon edge, (c) independent geocoder agrees within 5 km of the GPS. The conjunction is stronger evidence than `gps_unverified` (which we already auto-include for `inside_mainland_bbox`).

**Effect (post §5.2 + H1).** 2,207 of 2,255 rows include=True; 48 rows held at NaN by the H1 gate below.

**Caveat.** The promotion implicitly says: "the BID polygon is wrong by ≤ 5 km along the edge, but the school's coord and admin assignment via the spatial join are both reasonable." For adm2-level indicators specifically, the school's `adm2_pcode` may sit one polygon over from the declared one. This is a real data nuance; consumers who require strict adm-name agreement should filter on `coordinate_quality != boundary_zone`. The default `include_in_spatial_indicators` flag accepts this trade-off.

### 5.2-O1 Resolver invariant: no `boundary_zone` at adm1-only countries with MISMATCH (review-cycle 2026-05-10)

**Issue.** For countries with `final_match_level=adm1` (URY, BOL, GUY, BLZ), the boundary_zone label was firing on rows where `qc_adm1_status=MISMATCH` — semantically meaningless because there is no fall-back validation level. The H1 patch in `_spatial_indicator_policy` (initial fix) held them at NaN downstream but left the *label* wrong: a row labeled `boundary_zone` whose admin chain is in fact unvalidated.

**Structural fix (commit d1f0d97 + O1 follow-up).** Push the gate into `resolve_coordinate_quality` itself. At branch B (`final_level in ("adm2", "adm1") and adm1_status == "MISMATCH"`), if `final_level == "adm1"` the resolver short-circuits to `adm_mismatch` and does not test the boundary_zone qualifier. Result: the boundary_zone label is now a structural impossibility at adm1-only countries × adm1=MISMATCH. The H1 patch in `_spatial_indicator_policy` becomes redundant and was removed; an invariant test in `tests/test_qc_finalize.py::TestPolicyGates::test_resolver_invariant_adm1_country_never_emits_boundary_zone` locks the contract.

**Effect.** 46 URY + 2 BOL rows reclassify from `boundary_zone` to `adm_mismatch` (correct label). `include_in_spatial_indicators` remains NaN (now via rule 6 instead of the deleted H1 patch). LAC `boundary_zone` count: 2,255 → 2,207. LAC `adm_mismatch` count: 13,539 → 13,587. No change in include rates.

### 5.3 Resolve the PAN edge case (1 row)

**Implemented (commit ffbe8a3, 2026-05-10).** PAN 6895 (ESC. TANGERINE, Private, Bocas del Toro) had `coordinate_quality=geocoded_centroid`, `coordinate_source=centroid_cascade`, `qc_scope_class=near_border_review`, `adm1_pcode=NaN`, `adm2_pcode=NaN`, `include=NaN`.

**Diagnosis.** The cascade fill placed the school's coord at the centroid of an ADM3 polygon assigned during Step 04. The centroid happened to land near (≤ 5 km from) the country boundary. After Step 02 reclassifies scope, the row has no admin pcode because the spatial join didn't match the centroid back to ADM1/ADM2 polygons (the centroid is barely inside the ADM3 polygon but borderline relative to ADM1/ADM2 coverage).

**Proposed change.** Treat any `coordinate_source ∈ {geocoded, centroid_cascade}` row as include=True regardless of scope class as long as quality ∈ {geocoded_street, geocoded_centroid} and scope_class ≠ outside_country. The rationale: a centroid fill is a deliberate placement at a known polygon's centroid; if the resulting coord drifts into near-border, the polygon assignment is still valid evidence.

Equivalent code change in `_spatial_indicator_policy`:

```python
if quality in {"geocoded_street", "geocoded_centroid"} and scope_class != "outside_country":
    return True
```

This rule fires before the near-border branch.

**Effect.** 1 row flips NaN → True. No other rows affected (only 1 row of `geocoded_centroid × near_border_review × NaN`).

---

### 5.4 Centroid-precision labels → NaN (2026-05-11 revision)

**Implemented (commit pending, 2026-05-11).** Reverses the §5.3 promotion of `geocoded_centroid` to True. After internal review with two specialist agents (geo-architect + methodology-reviewer) and literature consultation, the consensus was that auto-including centroid-precision coordinates (~1-5 km positional error) in walking-accessibility indicators is methodologically indefensible.

**Diagnosis.** The platform's headline indicators measure walking accessibility at 15/30/60-minute isochrones. At 5 km/h, the 15-minute isochrone is ~1.25 km — **smaller than the centroid's own positional error**. Including such schools produces an indicator whose noise floor exceeds the signal (Apparicio, Cloutier & Shearmur 2008; Hewko, Smoyer-Tomic & Hodgson 2002). Auto-True coerces ~17K schools (3.2% of LAC) into a precision tier that the indicator semantically cannot use.

**Additional incoherence (pre-fix).** The pipeline treated two functionally identical labels asymmetrically:

- `geocoded_centroid` (deliberate cascade fill at ADM2 geometric centroid) → True
- `cluster_centroid` (covert centroid: 5+ schools at the same coord with different addresses, almost certainly the ministry filling `cabecera municipal` for all schools without GPS) → NaN

Both encode "school positioned at municipal centroid, ~1-5 km error." The provenance distinction (deliberate vs covert) is methodologically irrelevant: same precision floor, same impact on indicators. The reviewer's verdict: *"functional equivalence demands identical treatment"*.

**Proposed change.** Both `geocoded_centroid` and `cluster_centroid` → NaN (review). The downstream contract becomes: True = safe for any spatial indicator (precision < 50 m); NaN = consumer must opt in per-indicator (centroid precision usable for ADM2-level aggregates or 60-min indicators, not for 15/30-min walking); False = exclude (data anomaly or outside territory).

Equivalent code change in `_spatial_indicator_policy` (qc_core.py:1167):

```python
# OLD (§5.3):
if quality in {"geocoded_street", "geocoded_centroid"}:
    return True

# NEW (§5.4):
if quality == "geocoded_street":
    return True
# geocoded_centroid handled later, treated as NaN equivalent to cluster_centroid
...
if quality in {"geocoded_centroid", "cluster_centroid"}:
    return pd.NA
```

**Effect on LAC k-12 (529,635 schools).** Pre-fix: True 489,838 (92.5%) / NaN 35,485 (6.7%) / False 4,312 (0.8%). Post-fix: True ~486,977 (~91.95%) / NaN ~38,346 (~7.24%) / False 4,312 (0.81%). The ~2,861 `geocoded_centroid` rows reclassify True → NaN. Per-country impact concentrates in DOM (1,557 cascade fills, was 17.4% True, becomes 17.4% NaN), SLV, ECU, PAN, CHL (Phase B-2 countries).

**Rationale (literature)**:
- Apparicio, Cloutier & Shearmur (2008) — centroid bias swamps real accessibility variation in food-desert studies; the same logic applies to school accessibility.
- Hewko, Smoyer-Tomic & Hodgson (2002) — quantifies aggregation error in urban amenity accessibility; centroid placement systematically biases urban-vs-peripheral access estimates.
- Kwan (1998) — person-based accessibility requires spatial reference where people are, not where geometry averages.
- Openshaw (1984) — MAUP zoning effect: choosing the ADM2 centroid forces indicator resolution to ADM2 scale regardless of nominal isochrone.

**Downstream implication.** Indicators that aggregate at municipal level (e.g., schools per 1,000 school-age children per ADM2) are unaffected — they don't depend on intra-municipality position. Indicators that compute walking access (the platform's headline) now correctly exclude centroid-precision schools by default. An analyst running a municipal-aggregate indicator may explicitly opt-in to include NaN-marked centroid rows.

**Future refinement (post step 06 validated).** Replace geometric ADM2 centroids with **population-weighted centroids** computed from WorldPop 100m raster: `(Σ pop_i × lat_i, Σ pop_i × lon_i) / Σ pop_i`. This reduces systematic bias for skewed ADM2 (large rural municipalities with concentrated population) but does not eliminate the precision floor — population-weighted centroids remain NaN under the spec. The improvement is the bias reduction, not the include policy. New `coordinate_quality` value `geocoded_centroid_population` + `qc_centroid_method ∈ {geometric, pop_weighted, pop_weighted_snapped, fallback_geom}` are recommended (see `project_centroid_population_refinement.md` memory).

**Tracking.** Item for 15-May-2026 IDB meeting agenda: review concrete examples of centroid impact and confirm policy direction.

---

### 5.5 `spatial_only` countries cannot reach `gps_validated` (2026-05-11)

**Implemented (commit pending, 2026-05-11).** Reverts the `final_match_level=spatial_only` branch in `resolve_coordinate_quality` (qc_core.py:580-582 pre-fix). Schools in `spatial_only` countries had been emitting `gps_validated` whenever the BID admin polygon spatially contained the GPS — but that's an *assignment*, not a *validation*.

**Diagnosis.** JAM is the canonical example: 914 schools, all with `qc_match_level=SPATIAL_ONLY`, all with `qc_adm1_status=MATCH`, all with `raw_adm1_code=NaN` (the EMIS source has no admin codes). The "MATCH" is trivial: the spatial join assigned the parish, but there was no independent raw declaration to either contradict or corroborate it. Calling that `gps_validated` overpromises confidence.

**Why it matters.** The dashboard's quality tier ("Alta calidad" / "Sin auditoría") and the include policy both treat `gps_validated` as the gold standard. Trivially-MATCHed schools were borrowing that gold-standard signal without earning it. Compare:

| Country | `final_match_level` | raw admin codes | Pre-§5.5 label | Pre-§5.5 dashboard tier |
|---|---|---|---|---|
| BRB | `bbox_only` | no | `gps_unverified` | "Sin auditoría" 🩶 |
| JAM | `spatial_only` | no | `gps_validated` ⚠️ | "Alta calidad" 🟢 |

Both BRB and JAM have *no addresses, no admin codes from the source ministry*. Both can only be GPS-spot-checked against geometry, not validated against ministry declarations. They deserve identical treatment.

**Code change.** Resolver branch removed:

```python
# OLD:
elif final_level == "spatial_only" and adm1_status == "MATCH":
    validated = True

# NEW (§5.5):
# spatial_only and bbox_only countries cannot reach gps_validated —
# without raw admin codes there is no independent declaration to
# validate the GPS against. Best label is gps_unverified.
```

**Effect on LAC k-12.** JAM 912 schools flip `gps_validated → gps_unverified` (label change only — the include policy treats both as True for `inside_mainland_bbox`, so 99.8% include stays). The dashboard tier flips: JAM moves from "Alta calidad" (`pctInclude≥95%`) to "Sin auditoría" (`pctGpsUnverified≥50%`). LAC `gps_validated` total drops 478,044 → 477,132; `gps_unverified` total rises 363 → 1,275. Total True unchanged.

**Open question (deferred).** Whether `gps_unverified` should auto-include (current default) or be NaN. Most countries have very small `gps_unverified` counts; JAM and BRB are the only meaningful exposure. If `gps_unverified` → NaN, JAM falls from include=99.8% to include=0.2%. Discussion item for the 15-May-2026 meeting along with the centroid policy. Recommendation: keep gps_unverified=True default for now (analyst can override per indicator); revisit when we have better validation alternatives for spatial_only countries.

---

### 5.6 Extended `cluster_centroid` for sub-5 placeholders (2026-05-13)

**Implemented (commit pending, branch `bhs-reproducibility`).** The classical `cluster_centroid` label fires only for cluster size ≥ 5 (`CLUSTER_THRESHOLD`). Sub-5 placeholders (3 or 4 schools at the same coord with different MoE-declared admins) were silently labeled `gps_validated` — the include policy treated them as analyst-validated when they were in fact ministry placeholders.

**Rule extension (added in addition to `cluster_ge5`):**

```python
# In resolve_coordinate_quality, cluster_centroid branch:
if source == "original":
    if cluster >= CLUSTER_THRESHOLD:
        return "cluster_centroid", "cluster_ge5"
    if cluster in (3, 4) and diff_admin_locality:
        return "cluster_centroid", "cluster_3_4_diff_admin_locality"
    if cluster == 2 and diff_admin_locality and not n2_frontier:
        return "cluster_centroid", "cluster_2_diff_admin_locality"
```

Signals:
- `diff_admin_locality` (new column `qc_cluster_diff_admin_locality`): True if the cluster has ≥ 2 distinct values in any of raw_adm1, raw_adm2 or raw_locality. Categorical raw signal, not free-form `raw_street` (avoids false-positives from address formatting variants in same-campus cases like "Queen's Highway" vs "Queens Hwy").
- `n2_frontier` (new column `qc_n2_frontier_rescue`): True if at least one n=2 cluster member's coord sits within 5 km of its declared raw admin polygon edge (reuses existing `qc_distance_to_raw_polygon_km`). Frontier rescue prevents flipping legitimate cross-boundary mismatches.

**Helpers (qc_core.py):**
- `has_diff_admin_or_locality_in_cluster(df, addr_df)` — computes diff per cluster
- `detect_n2_frontier_rescue(df)` — computes frontier flag, requires `qc_distance_to_raw_polygon_km` pre-computed

**Why not lower CLUSTER_THRESHOLD instead?** The classical rule fires on count alone; a lower threshold would false-positive on legitimate same-campus pairs (primary + secondary in one building). The new rule needs both small count AND categorical raw diff — stronger signal at n=2 than the classical rule at n=5.

**Validation case (BHS).** Before extension: 138 schools, 125 gps_validated, 5 cluster_centroid (Nassau MoE central). After: 120 gps_validated, 10 cluster_centroid. The 5 new triggers (3 Abacos n=3 across Hope Town/Sandy Point/Murphy Town; 2 Exuma n=2 at literal half-degree placeholder coord) are unambiguous ministerial placeholders. 3 other n=2 BHS clusters (Old Bight high+primary, San Salvador Central+United Estates, Moss Town high+primary) were correctly NOT triggered — same raw_locality, real same-campus.

**LAC-wide impact.** 2,419 schools re-labeled (0.46% of LAC k-12). Distribution:
- BRA +1,368 · MEX +408 · ARG +222 · COL +187 · GTM +73 · PER +69 · URY +30 · CRI +24 · PRY +14 · PAN +12 · BHS +5 · SUR +6 · BLZ +9 · HND +4

Sources: ~1,903 from `gps_validated` (label change for true placeholders), ~516 from `geocoder_disagrees` / `boundary_zone` (cluster_centroid takes precedence over these in the chain).

**include_in_spatial_indicators=True** for LAC: drops ~0.40% (from 92.49% to ~92.0%). The lost 2,419 are now NaN (review) instead of True (auto-include) — honest accounting of placeholder uncertainty.

**Tests.** 8 new cases in `tests/test_qc_core.py::TestResolverPrecedence` covering n ∈ {2,3,4,5} × {with/without diff_admin_locality} × {with/without frontier rescue}.

---

## 6. Anomalies

### 6.1 `gps_validated × outside_country` = 2 (data anomaly)

| ISO | id_centro | sector | lat | lon | source | scope_class |
|---|---|---|---:|---:|---|---|
| ARG | 180089800 | Public | -27.45 | -56.91 | original | outside_country |
| COL | 286573004400 | Public | -0.19 | -75.06 | original | outside_country |

**Reading.** Both schools have `coordinate_quality=gps_validated` (passed admin checks) AND `qc_scope_class=outside_country` (coord falls outside the BID ADM0 polygon and not within 5 km of the border). This is logically inconsistent: if the spatial join validated the school against an ADM2 polygon inside the country, the coord cannot also be outside the country.

**Hypothesis.** The BID ADM2 polygons may extend slightly past the BID ADM0 polygon at certain border segments (sliver mismatches between the two layers). The school is in an ADM2 polygon that the ADM0 layer doesn't fully contain. The Step-02 ADM containment test uses the ADM0 polygon directly (`country_geom.covers(pt)`), independent of the ADM-level checks.

**Effect on policy.** Both rows are correctly excluded by rule 1 (`outside_country` → False). The label combo is informational only and doesn't leak into indicators.

**Action.** Track as a low-priority data backlog item. To fix: tighten the BID ADM2 polygons against ADM0, or relabel as `adm_mismatch` when this contradiction is detected.

### 6.2 `geocoded_centroid × near_border_review × NaN` = 1 (PAN 6895)

**§5.3 rescue (commit ffbe8a3) reverted by §5.4 (2026-05-11).** PAN 6895 returns to `include=NaN` along with all other `geocoded_centroid` rows, since centroid-precision coordinates can't enter walking-accessibility indicators by default. The school is still in the master table with its assigned coord; downstream consumers may opt in for municipal-level analyses.

### 6.3 `geocoder_disagrees × near_border_review × True` = 2 (MEX 07KTV0394A, MEX 07KTV0266F)

**Fixed in commit ffbe8a3** via §5.1 severity gate. Now `include=NaN`. Both are CONAFE SECUNDARIA COMUNITARIA schools (`KTV` = comunitario / telesecundaria / sub-modality) in Chiapas (MX07) where the geocoder placed them 27 km and 43 km from the GPS at score 85/87 — score <90 is the project's reject threshold (`project_score_based_geocoding` memory).

---

## 7. Country-specific notes (22 published ISOs)

Source: `COUNTRY_SCOPE` in `pipeline/constants.py:44-211`.

`final_match_level` distribution: 15 adm2 / 5 adm1 (incl. BHS) / 1 spatial_only (JAM) / 1 bbox_only (BRB).
`validation_tier` distribution: 19 standard / 3 limited (BHS, BRB, JAM) / 0 not_ready (HTI excluded).

> **2026-05-13 addendum — BHS onboarding.** BHS (n=138) joined the analysis scope after
> the tier table below was snapshotted, with `final_match_level=adm1` (via
> `ADM1_AGGREGATIONS["BHS"]` island-family mapping) and `validation_tier=limited`.
> The table has no BHS row; per-school detail lives in `results/QC/BHS_qc_report.md`.

### Tier-by-country summary (post §5.1+§5.2+§5.3+H1, 2026-05-10)

| ISO | n | final_match | tier | %include | dominant non-True label | notes |
|---|---:|---|---|---:|---|---|
| ARG | 35,313 | adm2 | standard | 93.4% | adm_mismatch (1,389) | INDEC ADM1 codes; CSV broken use GeoJSON |
| BLZ | 273 | adm1 | standard | 90.5% | adm_mismatch (23) | small N |
| BOL | 15,564 | adm1 | standard | 98.8% | cluster_centroid (70), adm_mismatch (71) | qc_adm1_col=Departamento; 2 rows reclassified from boundary_zone to adm_mismatch under O1 |
| BRA | 129,976 | adm2 | standard | 95.0% | cluster_centroid (3,613) | INEP gpkg cascade |
| BRB | 94 | bbox_only | limited | 100.0% | gps_unverified (94) | All 94 are gps_unverified by construction; auto-include via inside_mainland_bbox path |
| CHL | 8,356 | adm2 | standard | 99.5% | adm_mismatch (18) | qc_adm2_col=provincia_bid; Phase B-2 cascade not yet run |
| COL | 50,033 | adm2 | standard | 91.2% | missing (1,266), adm_mismatch (1,083), cluster_centroid (1,041) | DANE code-based matching post 2026-05-09 |
| CRI | 4,928 | adm2 | standard | 81.2% | missing (650), adm_mismatch (264) | High missing rate; Phase B-1 partial |
| DOM | 8,925 | adm2 | standard | 99.1% | adm_mismatch (76) | BID ADM1=Regiones, raw=Provincias → adm2 needed; cascade complete |
| ECU | 14,938 | adm2 | standard | 97.9% | adm_mismatch (256), missing (56), out_of_bounds (21 Galápagos) | Promoted adm1→adm2 in 2026-05 |
| GTM | 22,041 | adm2 | standard | 86.4% | adm_mismatch (2,449), geocoder_disagrees (432) | +5.0 pp from §5.2 (1,109 boundary_zone promoted, concentrated in Huehuetenango GT13=302) |
| GUY | 503 | adm1 | standard | 89.9% | gps_unverified (31 near-border), adm_mismatch (20) | 31 near-border not rescued (no code_col config) |
| HND | 11,633 | adm2 | standard | 87.0% | adm_mismatch (987), missing (423), out_of_bounds (64) | 63 outside_country — investigate |
| JAM | 914 | spatial_only | limited | 99.8% | missing (2) | No raw ministry, all Public; spatial_only = polygon containment only, no name match |
| MEX | 152,860 | adm2 | standard | 90.2% | cluster_centroid (8,805), geocoder_disagrees (4,467), adm_mismatch (1,783) | Largest country, dominant cluster_centroid contributor; 2 Chiapas rows held NaN by §5.1 |
| PAN | 3,615 | adm2 | standard | 85.6% | geocoded_centroid (519), adm_mismatch (479) | Cascade complete (2026-04); §5.3 promoted 1 ESC. TANGERINE row in Bocas del Toro |
| PER | 53,338 | adm2 | standard | 95.2% | adm_mismatch (2,209), cluster_centroid (214) | adm_mismatch 51% NaN distance — polygon coverage gaps |
| PRY | 7,628 | adm2 | standard | 86.9% | adm_mismatch (736), missing (258) | DMS coords; Phase B-1 partial |
| SLV | 5,762 | adm2 | standard | 82.6% | adm_mismatch (886), gps_unverified (70 near-border) | 70 near-border not rescued; high adm_mismatch fraction |
| SUR | 546 | adm2 | standard | 79.7% | adm_mismatch (97), boundary_zone (111 → True via §5.2) | +20.4 pp from §5.2 (Paramaribo ressort granularity edge artifacts) |
| URY | 2,395 | adm1 | standard | 94.3% | adm_mismatch (117) | Resolver invariant O1: adm1-only countries do not emit boundary_zone at adm1=MISMATCH; those 46 rows are adm_mismatch with the correct label |

### Country-specific rules / exceptions

- **BRB (`bbox_only`).** Cannot reach `gps_validated`. All 94 schools end at `gps_unverified` × `inside_mainland_bbox` × True. National-level (adm0) indicators are the only level Barbados can support; the Step-02 contract is BRB-aware (`gps_unverified` is the floor, not a flag).
- **JAM (`spatial_only`).** No ministry raw data; CIMA built from `JAM_total` (legacy R pipeline). All schools marked Public (EMIS only covers government + grant-aided). Validation by spatial containment only — no admin-name comparison. 99.8% include.
- **DOM (admin-level inversion).** BID ADM1 = 10 Regiones, raw = 32 Provincias. The pipeline uses `final_match_level=adm2` so the raw-Provincia maps to adm2 in the output. ADM1 status is empty across all DOM rows (`qc_adm1_status = NO_RAW_ADM` for 8,924/8,925).
- **ECU island handling.** 21 Galápagos schools live at `coordinate_quality=out_of_bounds` × `qc_scope_class=remote_territory_or_island` × `include=True`. Confirmed in 2026-05-08.
- **COL San Andrés.** 26 schools in San Andrés y Providencia treated identically to ECU Galápagos. The mainland `COUNTRY_BBOX` was narrowed in 2026-05-09 (commit `92ebced`) to `(-4.3, 12.5, -79.1, -66.8)` so SA falls outside it and gets the remote_territory label.
- **HND `outside_country`.** 63 schools — abnormally large outside_country bucket (compared to ARG/BOL/PER which have 1-4 each). Likely a raw data issue or BID ADM0 polygon edge artifact. Tracked as a backlog item.
- **MEX missing `adm2_code_col`.** Step 02's COL/ECU-style code-based admin recovery requires `adm{level}_code_col` in the per-country config. MEX doesn't have one. Effect: 65 of MEX's 67 near-border schools are rescued only at adm1 level (via name-based match on raw ENT), never adm2. The 2 unrescued MEX cases turn into the §5.1 bug.
- **SUR Paramaribo.** SUR's adm2 = ressort. The Paramaribo ressort granularity in the BID polygon layer is coarser than what raw uses (raw splits Paramaribo into Boven Sur, Nieuw Nickerie variants → `ADM2_ALIASES["SUR"]`). Result: 38% adm_mismatch by row count (mostly Paramaribo-area).
- **CHL Phase B-2 not run.** 1 missing + 4 OOB + 23 cluster_centroid + 18 adm_mismatch. Cascade implementation deferred (low ROI).
- **MEX/BRA/BOL `adm2_code_col` gaps.** MEX, BRA, BOL, CHL final_match=adm2 but their Step-02 configs don't define an `adm2_code_col` for code-based matching. Effect: their near-border rescue covers only adm1 (BRA 7/7, CHL 5/5, BOL 2/3, MEX 67/67 are adm1-only). Tracked as backlog: configurable per country.

---

## 8. Open questions / backlog

1. **Per-level include policy.** The current `include_in_spatial_indicators` is a single boolean. For `adm_mismatch` (13,539 rows) the right answer differs by indicator level: True for adm0, conditional on adm1_status for adm1, False for adm2. Two possible refactors: (a) emit three columns `include_adm0/include_adm1/include_adm2`, or (b) leave the boolean and document that consumers must re-derive per-level inclusion from `qc_adm1_status`/`qc_adm2_status`. Decision pending.
2. **Cluster size split.** Refining `cluster_centroid` policy by size — auto-exclude size ≥ 100 (≈ 3,244 MEX rows), conditional review for 5–9 (≈ 4,335 rows). Requires per-country sample audit before changing the default.
3. **175 unrescued near-border schools.** Adding `adm{level}_code_col` to Step-02 configs for SLV (70), MEX (65 at adm2), GUY (31), PAN (25), GTM (16), URY (13), PER (7), HND (6), BLZ (3), CRI (2), SUR (2). Each country requires a code column lookup (DANE/INEP-equivalent).
4. **HND `outside_country=63`.** Investigate whether these are raw-data error vs BID polygon edge.
5. **5,197 `adm_mismatch` rows with NaN `qc_distance_to_raw_polygon_km`.** Spatial join couldn't locate the raw-declared polygon (raw admin name not in BID polygon table). For these rows, the boundary_zone softening cannot fire even when geocoder corroborates. Hardest-to-fix subset; requires per-country alias work.
6. **PAN 6895 generalization.** The §5.3 fix promotes `geocoded_*` × `near_border_review` rows to True. Verify this doesn't open the policy to false positives in PRY / SLV / DOM cascade fills.
7. **Synthetic id_edificio prefix.** From 2026-05-09 close — `SYN` prefix may rename to `NOMATCH` / `PRIV`. Tracked separately.
8. **COL/PRY `bid_match_suspect=True` (9,904 rows).** Audit whether the BID id_edificio for those rows is stale or represents a "complejo educativo" aggregation. Tracked separately.
9. **(O2 from review-cycle 2026-05-10) — Decompose `boundary_zone` into kinds.** GTM Huehuetenango (geographic edge of polygon, defensible) and SUR Paramaribo (admin granularity / aliasing) fire the same label but represent different epistemic situations. Proposal: derived column `boundary_zone_kind ∈ {edge_geographic, granularity_admin}` distinguishing rows where both `distance_to_raw_polygon_km` and `geocode_distance_km` are < 1 km from those where one or both is in 1–5 km. Pair with adm2-level indicator gating: granularity_admin is safe for adm0/adm1 indicators only.
10. **(O3 from review-cycle 2026-05-10) — Recalibrate the 5 km boundary_zone threshold.** The current `BOUNDARY_ZONE_MAX_DISTANCE_KM = 5.0` is unjustified by literature. Proposal: 1 km high-confidence auto-include + 5 km review-only. Sensitivity sweep at {1, 2, 5, 10} km recommended before changing the default; impact concentrated in GTM and SUR.
11. **(M1 from review-cycle 2026-05-10) — MEX cluster_centroid MNAR documentation.** The default `fillna(False)` interpretation systematically under-includes ~8,805 MEX rural CONAFE telesecundarias whose physical existence is not in doubt. Document explicitly in §2.4 so indicator consumers can opt-in on a per-indicator basis.
12. **(U1 from review-cycle 2026-05-10) — Per-level include vector.** Replace the single Boolean with `include_adm0/include_adm1/include_adm2`. Structurally resolves O1/O2 and the URY/BOL/SUR/GTM cross-country comparability issues. Tracked as L-effort; pair with the dashboard payload refactor.

---

## Appendix A — Key constants

| constant | value | location |
|---|---|---|
| `CLUSTER_THRESHOLD` | 5 | `pipeline/qc_core.py:82` |
| `ADM0_BORDER_REVIEW_DISTANCE_KM` | 5.0 | `pipeline/qc_core.py:90` |
| `BOUNDARY_ZONE_MAX_DISTANCE_KM` | 5.0 | `pipeline/qc_core.py:454` |
| `BOUNDARY_ZONE_MAX_GEOCODER_DISTANCE_KM` | 5.0 | `pipeline/qc_core.py:455` |
| centroid bias threshold (`high`) | polygon area > 314 km² | `pipeline/qc_core.py` (centroid cascade) |
| ArcGIS score → street | ≥ 95 | `pipeline/04_geocode_missing.py` |
| ArcGIS score → centroid | 90 ≤ s < 95 | `pipeline/04_geocode_missing.py` |
| ArcGIS score → reject | < 90 | `pipeline/04_geocode_missing.py` |

## Appendix B — Reproducing this audit

```bash
# Build the audit cache (per-country CIMA enriched, filtered to LAC k-12)
uv run python -c "
import pandas as pd
lac = pd.read_csv('data/schools/AR/LAC_schools_k12_with_context.csv',
                  dtype={'id_centro': str, 'adm0_pcode': str})
ISOS = sorted(lac['adm0_pcode'].unique())
dfs = []
for iso in ISOS:
    dfs.append(pd.read_csv(f'data/schools/AR/{iso}/processed/{iso}_total_cima.csv',
                           dtype={'id_centro': str}))
cima = pd.concat(dfs, ignore_index=True)
cima['adm0_pcode'] = cima['adm0_pcode'].astype(str)
lac_ids = set(zip(lac['adm0_pcode'], lac['id_centro']))
cima['_k'] = list(zip(cima['adm0_pcode'], cima['id_centro']))
cima_k12 = cima[cima['_k'].isin(lac_ids)].drop(columns=['_k'])
cima_k12.to_pickle('results/QC/audit_cache_k12.pkl')
print(f'Cached {len(cima_k12):,} rows.')"

# Compute the §2 / §4 / §7 matrices
uv run python -c "
import pandas as pd
ck = pd.read_pickle('results/QC/audit_cache_k12.pkl')
def inc(v):
    if pd.isna(v): return 'NaN'
    s = str(v).strip().lower()
    return 'True' if s in ('true','1','1.0') else 'False' if s in ('false','0','0.0') else '?'
ck['_inc'] = ck['include_in_spatial_indicators'].apply(inc)

# §2 — coordinate_quality × include
print('=== §2 coordinate_quality × include ===')
print(ck.groupby('coordinate_quality')['_inc'].value_counts().unstack(fill_value=0))

# §4 — qc_scope_class × include
print('\n=== §4 qc_scope_class × include ===')
print(ck.groupby('qc_scope_class')['_inc'].value_counts().unstack(fill_value=0))

# §7 — per-country %include
print('\n=== §7 per-country ===')
for iso in sorted(ck['adm0_pcode'].unique()):
    sub = ck[ck['adm0_pcode']==iso]
    t = (sub['_inc']=='True').sum()
    print(f'  {iso}: n={len(sub):>6} %inc={100*t/len(sub):5.1f}')
"
```

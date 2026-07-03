# FMM vs OSRM — cross-validation of school-accessibility indicators (PAN, COL)

**Date:** 2026-05-16
**Scope:** Panama and Colombia · `acceso_escuela_pct` · sector Total · country level
**Methods compared:** FMM (Tier 1, cellular fast-marching over MAP friction) vs
OSRM (Tier 2, network routing over the OSM graph)

---

## 1. Purpose

Step 09 (FMM) is the Tier-1 travel-time engine; the Panama GIS pilot showed it
carries an optimism bias on short walking bands. Step 09b (OSRM) routes over the
real OSM network as the Tier-2 reality check. This note quantifies the
FMM↔OSRM gap for the two countries already produced — at country level **and
disaggregated by area and wealth quintile** — and applies the agreed
publication rule.

## 2. Method

Both engines are aggregated to the **identical** SCL schema so the numbers are
directly comparable:

- FMM: `pipeline/10_accessibility_aggregate.py` samples the Step 09 rasters on
  the WorldPop 1 km grid.
- OSRM: `pipeline/10b_accessibility_aggregate_osrm.py` reuses Step 10's
  aggregation core on the per-cell OSRM matrices from `09b_travel_time_osrm.py`.

Same band classification (≤15/30/60 min), same age coupling
(primaria→pop_5_9, secbaja→pop_10_14, secalta→pop_15_19), same population
weighting, same school gate (`include_in_spatial_indicators == True`).

All 12 OSRM matrices completed at **100.0 % reachable, 0 transport failures**
(after the ephemeral-port fix, commit `473fc79`). OSRM covers the **total**
school set only — no public/private split — so the comparison is sector=Total.

**Publication rule** (delta = FMM − OSRM, in percentage points):
|Δ| < 5 → publish point · 5 ≤ |Δ| ≤ 20 → publish range · |Δ| > 20 → validation pending.

Full per-slice detail (country + admin1, every area × quintile):
`results/QC/fmm_vs_osrm_comparison.csv`.

## 3. National results

### Walking

| Country | Level | Band | FMM | OSRM | Δ (pp) | Decision |
|---|---|---|---:|---:|---:|---|
| COL | primaria | ≤15 | 85.6 | 83.2 | +2.3 | publish point |
| COL | primaria | ≤30 | 94.7 | 93.1 | +1.7 | publish point |
| COL | primaria | ≤60 | 97.7 | 97.9 | −0.1 | publish point |
| COL | secbaja | ≤15 | 80.6 | 75.0 | +5.6 | publish range |
| COL | secbaja | ≤30 | 89.1 | 85.7 | +3.4 | publish point |
| COL | secalta | ≤15 | 80.2 | 73.5 | +6.7 | publish range |
| COL | secalta | ≤30 | 88.1 | 84.6 | +3.5 | publish point |
| PAN | primaria | ≤15 | 75.1 | 56.1 | **+19.0** | publish range |
| PAN | primaria | ≤30 | 87.9 | 82.5 | +5.5 | publish range |
| PAN | primaria | ≤60 | 93.7 | 94.2 | −0.5 | publish point |
| PAN | secbaja | ≤15 | 56.7 | 33.4 | **+23.3** | **validation pending** |
| PAN | secbaja | ≤30 | 73.5 | 62.7 | +10.8 | publish range |
| PAN | secalta | ≤15 | 39.3 | 19.6 | **+19.7** | publish range |
| PAN | secalta | ≤30 | 60.6 | 41.2 | **+19.4** | publish range |

### Motorized (summary)

All 18 motorized slices land at **publish point or publish range** — FMM and
OSRM agree within ~7 pp throughout (FMM runs slightly *below* OSRM because OSM
road speeds beat the MAP motorized friction surface). No motorized slice is
validation-pending, at country level or disaggregated.

## 4. Results by area — the bias is **not uniform**

Walking ≤15 min, sector Total, quintile Total. Δ = FMM − OSRM (pp):

| Country | Level | rural Δ | semiurban Δ | urban Δ |
|---|---|---:|---:|---:|
| COL | primaria | **+12.5** | −0.5 | +0.4 |
| COL | secbaja | +9.2 | +0.8 | +5.1 |
| COL | secalta | +9.5 | +1.7 | +6.5 |
| PAN | primaria | −0.1 | +3.9 | **+29.1** |
| PAN | secbaja | −3.7 | +0.5 | **+37.5** |
| PAN | secalta | −0.4 | +0.8 | **+30.2** |

The country-level gap hides a sharp structural pattern:

- **PAN — the FMM optimism is an *urban* phenomenon.** Rural PAN walking agrees
  almost exactly (Δ ≈ 0); the +19–23 pp national gap is driven entirely by
  urban cells, where FMM overestimates by **+29 to +38 pp**. In dense Panama City
  the cellular wavefront cuts straight across blocks, highways and water; OSRM
  forces the real network detours.
- **COL — the optimism is a *rural* phenomenon.** Urban and semi-urban COL agree
  within a few pp; the gap concentrates in rural areas (+9 to +12 pp), where the
  friction surface lets the wavefront move but the actual road network is sparse.

## 5. Results by wealth quintile

Walking ≤15 min, poverty quintile (quintile_1 = poorest), sector & area Total.
Δ = FMM − OSRM (pp):

| Country | Level | q1 Δ | q2 Δ | q3 Δ | q4 Δ | q5 Δ |
|---|---|---:|---:|---:|---:|---:|
| COL | primaria | −0.5 | +1.4 | +5.0 | +4.6 | +1.3 |
| COL | secalta | +4.1 | +5.2 | +8.7 | +8.3 | +7.0 |
| PAN | primaria | **−11.5** | **+20.6** | **+33.7** | **+28.1** | **+27.0** |
| PAN | secalta | −2.5 | +10.3 | +21.8 | **+37.5** | +31.4 |

- **COL — the poverty gradient survives the engine choice.** Deltas are modest
  and fairly uniform across quintiles; FMM and OSRM tell the same equity story
  (poorest quintile ~20 pp below the richest).
- **PAN — the FMM poverty gradient is distorted and must not be published.**
  Deltas swing from −11.5 pp (poorest) to +33.7 pp (q3), non-monotonically. FMM
  *underestimates* the poorest quintile and *overestimates* the wealthier,
  more-urban quintiles — so the FMM PAN equity gradient is an artefact of the
  urban bias correlating with wealth, not a real gradient.

## 6. Findings

1. **The FMM optimism bias is structured, not a fixed offset.** It is an *urban*
   bias in PAN (+29–38 pp) and a *rural* bias in COL (+9–12 pp). The national
   average masks both.
2. **COL walking is publishable** — at country level and disaggregated. FMM and
   OSRM agree within ~7 pp and tell the same area and quintile equity story.
3. **PAN walking is not publishable from FMM.** Neither the national figure nor —
   critically — the area and quintile breakdowns: FMM overestimates urban/wealthy
   walking access by 27–38 pp and distorts the equity gradient. OSRM is the
   reference for every PAN walking indicator.
4. **Motorized is solid for both countries**, at country level and disaggregated.
5. **r5py corroboration.** The Panama pilot's r5py router gave ≈52 % walking
   ≤15 min on the top-5 districts; our OSRM gives 56.1 % nationally. Two
   independent network routers land close and far below FMM's 75.1 % —
   confirming FMM, not OSRM, is the outlier.

## 7. Publication decision

Country-level slices (all modes/levels/bands × area × quintile, sector Total):

| Decision | Slices |
|---|---:|
| publish point | 266 |
| publish range | 121 |
| validation pending | 27 |

The 27 validation-pending slices are all **PAN walking, urban or wealthy
quintiles** — exactly the structured bias of §4–5.

**Recommendation:**
- **Motorized (PAN, COL):** publish FMM or OSRM — either is defensible.
- **COL walking:** publish FMM as point estimates, incl. area and quintile
  breakdowns; OSRM confirms it.
- **PAN walking:** publish **OSRM** (Tier 2) for every slice — national, area,
  quintile. Report FMM only as a documented upper bound; do not publish the FMM
  PAN walking equity gradient.

## 8. Caveats

- OSRM matrices are **total-sector only**; no public/private breakdown yet.
- admin1-level deltas are noisier than country level — small comarcas /
  departments with few populated cells produce wide swings. Use country and
  admin2-aggregate figures for headline reporting.
- OSRM uses the OSM snapshot `colombia-latest` (2026-05-15) and the pilot's
  Panama PBF; FMM uses the MAP 2019 friction surface — vintages differ slightly.
- Scope is the two countries produced so far. Scaling Step 09b + 10b to the
  remaining 20 ANALYSIS_ISOS is pending.

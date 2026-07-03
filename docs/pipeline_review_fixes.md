# Pipeline review — fixes and efficiency improvements

**Date:** 2026-04-20  
**Scope:** `pipeline/`, tests, cross-script consistency.

This document records concrete bugs, performance issues, and refactors identified in a full pipeline review. Items are ordered by **correctness first**, then **performance**, then **maintainability**.

---

## 1. Correctness — fix first

### 1.1 `pipeline/03_coverage_assessment.py` — wrong `BASE` and `results` paths

**Problem:**  
`BASE = Path(__file__).parent / "data" / "schools" / "AR"` resolves to `pipeline/data/schools/AR` (non-existent). Same for `out_dir = Path(__file__).parent / "results"`.

**Effect:** `read_processed()` returns `None` for every country; coverage metrics may be silently empty.

**Fix:** Use project-root-relative paths, consistent with other pipeline scripts:

```python
ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data" / "schools" / "AR"
out_dir = ROOT / "results"
```

Or `BASE = Path("data/schools/AR")` when the script is always run from repo root.

---

### 1.2 `pipeline/00_preprocess_admin.py` — wrong BID polygon path for CHL

**Problem:**  
`BID_LEVEL_2 = BASE / "LAC" / "level 2" / "lac-level-2.shp"` under `data/schools/AR/` — other scripts use `data/bounderys/LAC/level 2/lac-level-2.shp`.

**Effect:** `preprocess_chl()` fails with `FileNotFoundError` when rebuilding `CHL_admin.csv`.

**Fix:** Point `BID_LEVEL_2` at `ROOT / "data" / "bounderys" / "LAC" / "level 2" / "lac-level-2.shp"` (match spelling used elsewhere in the repo).

---

### 1.3 `tests/test_coordinates.py` — invalid `pytest.warns` usage

**Problem:**  
`pytest.warns(f"{iso}: georef rate ...")` passes a string; `pytest.warns` expects a warning type (e.g. `UserWarning`), not a message.

**Effect:** No warning is emitted; the “soft” check does nothing.

**Fix:** Use `warnings.warn(message, UserWarning)` inside a conditional, or remove the line and rely on the hard threshold only.

---

### 1.4 `pipeline/01_build_cima.py` — `process_HND` K-12 filter omits `nivel_secbaja`

**Problem:**  
Filter is `(nivel_primaria == 1) | (nivel_secalta == 1)`; `nivel_secbaja` is never OR’d in.

**Effect:** Today `nivel_secbaja == nivel_primaria`, so behavior is OK; if HND logic changes, rows with only secbaja could be dropped silently.

**Fix:** Add `| (df["nivel_secbaja"] == 1)` to the filter.

---

### 1.5 `pipeline/01_build_cima.py` — `process_DOM` longitude “fix”

**Problem:**  
Positive longitudes are negated unconditionally; bounds `(-75, -60)` are wider than DOM’s actual extent (~−72 to ~−68).

**Effect:** Risk of turning garbage into plausible-looking wrong coords before bounds nuke them.

**Fix:** Prefer dropping out-of-range values without sign-flip, or only flip when clearly swapped (e.g. lat in lon range and vice versa), aligned with QC logic in `02_qc_coordinates.py`.

---

### 1.6 `pipeline/02_qc_coordinates.py` — CSV encoding trial reads full file

**Problem:**  
`extract_addresses` may call `pd.read_csv` up to three times per file (utf-8, cfg encoding, latin-1) for large files (e.g. BRA microdata).

**Fix:** Trust `read_kwargs["encoding"]` when set; or sniff charset on first 64 KB only; avoid triple full-file reads.

---

### 1.7 ECU bounding box inconsistency — ✅ FIXED (2026-04-20)

**Problem:**  
`COUNTRY_BBOX["ECU"]` differs between `02_qc_coordinates.py` and `05_geocode_missing.py` (e.g. lon min −81 vs −92).

**Effect:** Geocode bbox rejection vs QC pre-check can disagree.

**Fix:** Single shared `COUNTRY_BBOX` (see §3.2); align ECU to one authoritative box.

**Resolution:** Consolidated into `pipeline/constants.py`. ECU is now `(-5.1, 1.5, -81.1, -75.2)` (mainland only) in all consumers. The old `(-92, -75)` value in `05_geocode_missing.py` that included Galápagos / open Pacific is gone. `results/qc_coordinate_summary.csv` is byte-identical after the refactor (SHA256 match). Also silently fixed: the PAN Comarca block of `ADM1_ALIASES` was missing from `05_geocode_missing.py` and is now available there too.

---

## 2. Performance

### 2.1 `pipeline/run_all.py` — subprocess per step

**Problem:** Each step is a new Python process → repeated cold import of geopandas/pandas/shapely (~seconds each). Step `05_geocode_missing.py` is not in `STEPS`.

**Fix:** Call `main()` functions in-process from one driver, or use `python -m pipeline...` with shared imports. Add optional geocode step to the documented full run.

---

### 2.2 `pipeline/01_build_cima.py` — serial country builds

**Problem:** Countries are independent; wall time is sum of all `process_*`.

**Fix:** After removing module-level `summary`/`errors` globals, use `concurrent.futures.ProcessPoolExecutor` with ~4 workers for CPU-bound readers; keep BRA network/download in main process if needed.

---

### 2.3 `pipeline/02_qc_coordinates.py` — `df.apply(assign_status, axis=1)`

**Problem:** Row-wise Python on 100k+ rows (MEX, BRA) is slow.

**Fix:** Vectorize with boolean masks and `np.where` / chained assignments for: swapped, OOB, no polygon, code match, spatial_only, name match + aliases.

---

### 2.4 `pipeline/05_geocode_missing.py` — `iterrows` + per-id `cima.loc[mask]`

**Problem:** `for _, row in target_df.iterrows()` is slow; updating CIMA with `mask = cima["id_centro"] == sid` per school is O(n_targets × n_cima).

**Fix:** `cima.set_index("id_centro").update(fill_accepted.set_index(...))` (or `merge` + column overwrite) for bulk updates.

---

### 2.5 `pipeline/05_geocode_missing.py` — full LAC shapefile read per country

**Problem:** `gpd.read_file(adm1_path)` loads entire LAC, then filters by `ADM0_PCODE`.

**Fix:** `where="ADM0_PCODE='MEX'"` with pyogrio/fiona if available, or load once at startup and cache by ISO.

---

### 2.6 Geocode cache — large JSON with `indent=2`, rewrite every N rows

**Problem:** Writes grow slow and huge as cache grows.

**Fix:** SQLite key-value store, or append-only JSONL, or `dbm`; avoid pretty-printing the whole cache on every periodic save.

---

### 2.7 `validate_coordinates` — Python list of `Point(...)`

**Problem:** Building points in a Python loop for large in-bounds sets.

**Fix:** `geometry=gpd.points_from_xy(df["longitud"], df["latitud"])`.

---

## 3. Maintainability / architecture

### 3.1 Monolithic `01_build_cima.py` (~1.8k lines)

**Problem:** Repeated patterns (`next((c for c in df.columns ...))`) caused real bugs (GUY lat, HND id, PAN `marco_col` precedence).

**Fix:**  
- Extract `find_col(df, *keywords, require=...)` with normalized matching.  
- Long term: per-country **config dataclass** + one `build_country(cfg)` implementation; each country = small config block.

---

### 3.2 Duplicated constants — ✅ PARTIALLY FIXED (2026-04-20)

**Problem:** `SCHEMA` / `REQUIRED_COLUMNS`, `COUNTRY_BBOX`, `ADM1_ALIASES`, and raw-path logic exist in multiple files (`01`, `02`, `05`, `conftest`).

**Fix:** `pipeline/constants.py` (or `pipeline/schema.py`) imported by build, QC, geocode, and tests.

**Resolution:** `pipeline/constants.py` created with `SCHEMA`, `REQUIRED_COLUMNS`, `ALL_ISOS`, `COUNTRY_BBOX`, `ADM1_ALIASES`. Consumed by `01_build_cima.py`, `02_qc_coordinates.py`, `05_geocode_missing.py`, and `tests/conftest.py`. Added `[tool.pytest.ini_options] pythonpath = ["."]` to `pyproject.toml` so `from pipeline.constants import …` resolves under pytest. Raw-path logic (`COUNTRY_CONFIG`) is still duplicated between `01` and `02` — see §3.3, still open.

---

### 3.3 `COUNTRY_CONFIG` vs `process_*` raw paths

**Problem:** `02` and `01` both encode raw file paths; drift risk on any ministry filename change.

**Fix:** Single source of truth for paths and column names consumed by both build and QC.

---

### 3.4 `pipeline/03_coverage_assessment.py` — giant `COUNTRY_META` dict

**Problem:** Large hardcoded tuples + prose; hard to diff and update.

**Fix:** `results/country_meta.csv` (or `data/meta/country_universe.csv`) loaded by the script; notes as a column.

---

### 3.5 Module-level `summary` / `errors` in `01_build_cima.py`

**Problem:** Prevents clean parallel execution and test isolation.

**Fix:** Each `process_*` returns `(df, meta_dict)`; `main()` aggregates.

---

### 3.6 Dead / stale code

| Location | Issue |
|----------|--------|
| `01_build_cima.py` `process_CHL` | `has_code` defined, never used — remove or use. |
| `01_build_cima.py` | Redundant `import shapefile as shp_lib` inside functions where module already imports it. |
| `04_qc_figures.py` | Static “next steps” / “URY skip” text; Figure 5 references old script name — refresh or generate from `qc_coordinate_summary.csv`. |

---

### 3.7 `tests/test_cima_schema.py` — re-reads CSV every test

**Problem:** `load(iso)` on every test method while `conftest.py` already has session-scoped `all_cima`.

**Fix:** Parametrize tests to use `all_cima[iso]` from fixture to cut disk I/O during pytest.

---

### 3.8 `process_GUY` — `Type` column branch after lowercasing headers

**Problem:** `if 'Type' in df.columns` after columns are lowercased — branch is dead; relies on fallback `next(...)`.

**Fix:** Use `'type' in df.columns` or drop the dead branch.

---

## 4. Optional correctness follow-ups

- **CHL:** CLAUDE.md notes missing `cod_ense` 710/810/910 in K-12 filter — extend `ens_df.isin([...])` when policy confirms.
- **`normalize_name` / DMS:** Unify `dms_to_dd` between `01` and `02` (single implementation, one import).

---

## 5. Suggested implementation order

| Priority | Item | Effort | Status |
|----------|------|--------|--------|
| P0 | Fix `03_coverage_assessment.py` paths | minutes | ✅ done |
| P0 | Fix `00_preprocess_admin.py` BID path | minutes | ✅ done |
| P0 | Verify coverage output after path fix | minutes | ✅ done |
| P0 | Fix `pytest.warns` in `test_coordinates.py` | minutes | ✅ done |
| P1 | Reconcile ECU bbox + shared constants module | ~1–2 h | ✅ done (2026-04-20) |
| P1 | Vectorize `assign_status` in `02_qc_coordinates.py` | ~1–2 h | open |
| P1 | Bulk CIMA update in `05_geocode_missing.py` | ~1 h | open |
| P2 | `find_col` helper + HND filter + DOM lon handling | ~2 h |
| P2 | `run_all` in-process + optional step 05 | ~30 min |
| P3 | Parallel `01_build_cima` + return values instead of globals | ~half day |
| P3 | Geocode cache backend (SQLite/JSONL) | ~2 h |
| P3 | Split `01_build_cima` into config-driven builder | multi-day |

---

## 6. Files referenced

| File | Main issues |
|------|-------------|
| `pipeline/01_build_cima.py` | HND filter, DOM lon, monolith, globals, GUY `Type` |
| `pipeline/02_qc_coordinates.py` | `apply(axis=1)`, triple CSV read, Point loop |
| `pipeline/03_coverage_assessment.py` | Wrong `BASE` / `out_dir` |
| `pipeline/00_preprocess_admin.py` | Wrong `BID_LEVEL_2` |
| `pipeline/run_all.py` | Subprocess overhead, missing step 05 |
| `pipeline/05_geocode_missing.py` | `iterrows`, O(n²) CIMA updates, full LAC read, JSON cache |
| `pipeline/04_qc_figures.py` | Stale static copy |
| `tests/test_coordinates.py` | `pytest.warns` misuse |
| `tests/test_cima_schema.py` | Redundant CSV reads |

---

*Generated from pipeline review. Apply changes in small PRs (P0/P1 first) and re-run `uv run python pipeline/02_qc_coordinates.py --qc-only` and `uv run pytest tests/ -v` after each batch.*

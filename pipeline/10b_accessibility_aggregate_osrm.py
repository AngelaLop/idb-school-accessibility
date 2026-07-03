"""
Step 10b — Accessibility indicators from the OSRM matrices, SCL long format.

Tier-2 (network-routing) counterpart of Step 10. Step 10 samples the FMM
travel-time rasters; this script reads the per-cell OSRM matrices written by
`09b_travel_time_osrm.py` and aggregates them with the *exact same* logic —
band classification, age coupling, population weighting, wealth quintiles and
admin roll-up — so the FMM and OSRM tables are directly comparable.

The OSRM parquet already is the WorldPop grid with a `time_to_nearest_min`
column, so there is no raster sampling: we just join that column onto the
enriched grid (for poverty / RWI quintiles) by `cell_id` and reuse Step 10's
`aggregate_country`.

Sector: 09b now writes per-sector matrices (_public_osrm / _private_osrm).
This script reads both, derives Total as the cell-wise min of the public and
private travel times — exactly as Step 10 does for FMM — and emits Public,
Private and Total rows. A sector with no matrix (e.g. HND is public-only by
design, CRI's private schools lack coordinates) is treated as no-data: its
rows are skipped and Total falls back to the sector that is present.

Output
------
    results/accessibility/accessibility_osrm_scl.csv

CLI
---
    uv run python pipeline/10b_accessibility_aggregate_osrm.py --countries PAN COL
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("step10b_osrm")

from _paths import REPO_ROOT as PROJECT_ROOT, RESULTS_ROOT  # noqa: E402

OSRM_DIR = RESULTS_ROOT / "osrm"
OUT_DIR = RESULTS_ROOT / "accessibility"

METHOD = "OSRM"
SOURCE = "CIMA/BID — OSRM network routing sobre OSM + WorldPop"


def _load_step10():
    """Import the Step 10 module (its filename starts with a digit, so it
    cannot be imported with a normal `import` statement)."""
    path = PROJECT_ROOT / "pipeline" / "10_accessibility_aggregate.py"
    spec = importlib.util.spec_from_file_location("step10_aggregate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sample_sector(iso3: str, mode: str, level: str, sector: str,
                   grid: pd.DataFrame) -> np.ndarray | None:
    """Read one per-sector OSRM matrix and align its travel time onto the grid.

    Returns a float64 array (one per grid cell, NaN where unmatched/unreachable)
    or None when the matrix file does not exist (sector has no schools)."""
    pq = OSRM_DIR / f"{iso3}_{mode}_{level}_{sector}_osrm.parquet"
    if not pq.exists():
        return None
    osrm = pd.read_parquet(pq, columns=["cell_id", "time_to_nearest_min"])
    tt_by_cell = dict(zip(osrm["cell_id"], osrm["time_to_nearest_min"]))
    tt = grid["cell_id"].map(tt_by_cell).to_numpy(dtype=np.float64)
    n_unmatched = int((~grid["cell_id"].isin(tt_by_cell)).sum())
    if n_unmatched:
        log.warning("[%s/%s/%s/%s] %d grid cells absent from OSRM matrix",
                    iso3, mode, level, sector, n_unmatched)
    return tt


def run(countries: list[str]) -> Path:
    s10 = _load_step10()
    a1_names, a2_names = s10.load_admin_names()
    all_rows: list[dict] = []

    for iso3 in countries:
        grid = s10.load_grid(iso3)           # cell_id, area, q_pov, q_rwi, ADM*, pop cols
        if "cell_id" not in grid.columns:
            raise RuntimeError(f"{iso3}: population grid has no cell_id column")

        for mode in s10.MODES:
            for level, (_, pop_col) in s10.LEVEL_META.items():
                # Read the public and private matrices, then derive Total as the
                # cell-wise min: travel time to the nearest school of ANY sector
                # is the nearer of the public and private times. Exact by
                # definition and identical in contract to Step 10's FMM path.
                tt_pub = _sample_sector(iso3, mode, level, "public", grid)
                tt_prv = _sample_sector(iso3, mode, level, "private", grid)

                if tt_pub is None and tt_prv is None:
                    log.warning("[%s/%s/%s] no public or private OSRM matrix — skipping",
                                iso3, mode, level)
                    continue
                if tt_pub is None:
                    log.warning("[%s/%s/%s] no public matrix — total = private only",
                                iso3, mode, level)
                    tt_tot = tt_prv
                elif tt_prv is None:
                    log.warning("[%s/%s/%s] no private matrix — total = public only",
                                iso3, mode, level)
                    tt_tot = tt_pub
                else:
                    tt_tot = np.fmin(tt_pub, tt_prv)  # NaN-aware: ignores missing side

                sectors_tt = [("total", tt_tot)]
                if tt_pub is not None:
                    sectors_tt.append(("public", tt_pub))
                if tt_prv is not None:
                    sectors_tt.append(("private", tt_prv))

                for sector, tt in sectors_tt:
                    work = grid.copy()
                    work["_pop"] = work[pop_col].astype(float)
                    for band in s10.TIME_BANDS:
                        reach = np.where(np.isfinite(tt) & (tt <= band), 1.0, 0.0)
                        work["_num"] = work["_pop"].to_numpy() * reach
                        all_rows.extend(s10.aggregate_country(
                            work, iso3, mode, level, sector, band, a1_names, a2_names))
                    log.info("[%s/%s/%s/%s] reachable<=60min: %.1f%% of cells",
                             iso3, mode, level, sector,
                             100.0 * np.mean(np.isfinite(tt) & (tt <= 60)))

    out = pd.DataFrame(all_rows)
    if not out.empty:
        # overwrite Step 10's FMM provenance with the OSRM provenance
        out["method"] = METHOD
        out["source"] = SOURCE

    out_path = OUT_DIR / "accessibility_osrm_scl.csv"
    return s10.write_scl_output(out, out_path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Step 10b — OSRM accessibility SCL aggregate.")
    ap.add_argument("--countries", nargs="+", required=True, help="ISO3 codes")
    ap.add_argument("--log-level", default="INFO",
                    choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level,
                        format="%(asctime)s  %(levelname)-7s  %(message)s",
                        datefmt="%H:%M:%S")
    run(args.countries)


if __name__ == "__main__":
    sys.exit(main())

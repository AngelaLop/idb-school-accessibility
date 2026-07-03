"""
Step 10 — Accessibility indicators, aggregated to SCL long format.

For each country it reads the Step 09 FMM travel-time rasters, samples them on
the WorldPop 1 km population grid, classifies each cell into time bands, and
aggregates the share of school-age population that can reach the nearest school
within each band — disaggregated by geography, area, wealth quintile, school
sector, mode and education level.

Indicator
---------
`acceso_escuela_pct` = % of the relevant age-group population living in a cell
whose travel time to the nearest school of that level is <= the time band.

  education_level → age group → population denominator
    primaria  → 05_09 → pop_5_9
    secbaja   → 10_14 → pop_10_14
    secalta   → 15_19 → pop_15_19

Disaggregation (one row per slice, SCL long/tidy format)
--------------------------------------------------------
  idgeo        country / admin1 / admin2   (admin1 = aggregate of its admin2)
  area         urban / semiurban / rural / Total
  quintile     quintile_1..5 (pobreza ADM2, BID) / rwi_q1..5 (RWI Meta, COL only)
               / Total      — 1 = poorest, 5 = richest
  sector       Public / Private / Total
  mode         walking / motorized
  education_level  primaria / secbaja / secalta
  time_band    le15 / le30 / le60 (minutes)

Quintiles are population-weighted within each country (each quintile holds ~20%
of the age-group population). Cells with no wealth value get no quintile row.

To keep the cross-product bounded: country and admin1 carry both the area and
the quintile breakdown; admin2 carries the area breakdown only.

Output
------
results/accessibility/accessibility_fmm_scl.csv  (all requested countries)

CLI
---
    uv run python pipeline/10_accessibility_aggregate.py --countries PAN COL
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import rowcol

log = logging.getLogger("step10_aggregate")

# ────────────────────────── paths & catalogs ───────────────────────────────────
from _paths import DATA_ROOT, RESULTS_ROOT  # noqa: E402
from constants import ADM1_ONLY_ISOS  # noqa: E402

GRID_DIR = DATA_ROOT / "population" / "WorldPop" / "processed"
TT_DIR = DATA_ROOT / "transportation" / "travel_times"
ADMIN_CSV = DATA_ROOT / "bounderys" / "LAC" / "level 2" / "lac-level-2.csv"
OUT_DIR = RESULTS_ROOT / "accessibility"

MODES = ["walking", "motorized"]
SECTORS = ["total", "public", "private"]
TIME_BANDS = [15, 30, 60]  # minutes

# education level → (age label, population column in the grid)
LEVEL_META: dict[str, tuple[str, str]] = {
    "primaria": ("05_09", "pop_5_9"),
    "secbaja": ("10_14", "pop_10_14"),
    "secalta": ("15_19", "pop_15_19"),
}

# WorldPop area_class → SCL `area` value
AREA_MAP = {"urbana": "urban", "no_urbana": "semiurban", "dispersa": "rural"}
AREA_VALUES = ["urban", "semiurban", "rural"]

POV_QUINTILES = [f"quintile_{i}" for i in range(1, 6)]
RWI_QUINTILES = [f"rwi_q{i}" for i in range(1, 6)]

# Reference year = the WorldPop population vintage used by Step 06
# (clipped_global_*_2023_CN_1km_R2025A). The indicator is a population-weighted
# share, so the population year is its reference year. Note the indicator mixes
# vintages — MAP friction 2019, OSM network ~2026 for OSRM, school data varies
# by country — but the population denominator is the defensible single `year`.
YEAR = 2023
METHOD = "FMM"
SOURCE = "CIMA/BID — FMM sobre fricción MAP 2019 + WorldPop"
INDICATOR = "acceso_geografico"  # BID SCL indicator family name


# ────────────────────────── wealth quintiles ───────────────────────────────────

def weighted_quintiles(value: pd.Series, weight: pd.Series,
                       higher_is_richer: bool, labels: list[str]) -> pd.Series:
    """
    Population-weighted quintile labels (labels[0] = poorest … labels[4] = richest).

    Cells with a missing value or non-positive weight get NaN (no quintile).
    Each quintile holds ~20 % of the total weight.
    """
    out = pd.Series(np.nan, index=value.index, dtype=object)
    valid = value.notna() & (weight > 0)
    if not valid.any():
        return out
    sub = pd.DataFrame({"v": value[valid], "w": weight[valid]})
    # sort ascending in *wealth*: poorest first
    sub = sub.sort_values("v", ascending=higher_is_richer, kind="mergesort")
    frac = (sub["w"].cumsum() - 0.5 * sub["w"]) / sub["w"].sum()
    idx = np.clip((frac * 5).astype(int), 0, 4)
    out.loc[sub.index] = [labels[i] for i in idx]
    return out


# ────────────────────────── raster sampling ────────────────────────────────────

def sample_raster(path: Path, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Sample a travel-time raster at the given lon/lat. Off-grid → NaN."""
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float64)
        transform, nodata = src.transform, src.nodata
        H, W = arr.shape
    rows, cols = rowcol(transform, lons, lats)
    rows = np.asarray(rows, dtype=int)
    cols = np.asarray(cols, dtype=int)
    tt = np.full(len(lons), np.nan, dtype=np.float64)
    inb = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
    tt[inb] = arr[rows[inb], cols[inb]]
    if nodata is not None and np.isfinite(nodata):
        tt[tt == nodata] = np.nan
    tt[~np.isfinite(tt)] = np.nan
    return tt


# ────────────────────────── grid preparation ───────────────────────────────────

def load_grid(iso3: str) -> pd.DataFrame:
    """Load the population grid and attach area + wealth-quintile columns."""
    df = pd.read_csv(GRID_DIR / f"population_grid_{iso3}.csv")
    df["area"] = df["area_class"].map(AREA_MAP)
    unmapped = df["area"].isna().sum()
    if unmapped:
        raise RuntimeError(f"{iso3}: {unmapped} cells with unmapped area_class")

    # school-age population used to weight the quintile cut (5–19)
    sa = df["pop_5_9"] + df["pop_10_14"] + df["pop_15_19"]

    # poverty quintiles — quintile_1 = poorest = HIGHEST poverty rate
    df["q_pov"] = weighted_quintiles(
        df["poverty_rate_adm2"], sa, higher_is_richer=False, labels=POV_QUINTILES)
    pov_cov = 100 * sa[df["q_pov"].notna()].sum() / sa.sum()

    # RWI quintiles — rwi_q1 = poorest = LOWEST RWI (NaN where RWI absent, e.g. PAN)
    if df["rwi"].notna().any():
        df["q_rwi"] = weighted_quintiles(
            df["rwi"], sa, higher_is_richer=True, labels=RWI_QUINTILES)
        rwi_cov = 100 * sa[df["q_rwi"].notna()].sum() / sa.sum()
    else:
        df["q_rwi"] = np.nan
        rwi_cov = 0.0

    log.info("[%s] %d cells | poverty-quintile cover %.1f%% | RWI-quintile cover %.1f%%",
             iso3, len(df), pov_cov, rwi_cov)
    return df


def load_admin_names() -> tuple[dict[str, str], dict[str, str]]:
    """Return (admin1_pcode → name, admin2_pcode → name) from the BID level-2 table."""
    adm = pd.read_csv(ADMIN_CSV)
    a1 = dict(zip(adm["ADM1_PCODE"].astype(str), adm["ADM1_EN"].astype(str)))
    a2 = dict(zip(adm["ADM2_PCODE"].astype(str), adm["ADM2_EN"].astype(str)))
    return a1, a2


# ────────────────────────── aggregation ────────────────────────────────────────

def _grouped(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Sum _pop and _num over `keys`; returns a frame indexed by the keys."""
    return df.groupby(keys, dropna=True)[["_pop", "_num"]].sum()


def aggregate_country(df: pd.DataFrame, iso3: str, mode: str, level: str,
                      sector: str, band: int, a1_names: dict, a2_names: dict) -> list[dict]:
    """Produce all SCL rows for one (mode, level, sector, band) combination."""
    age = LEVEL_META[level][0]
    band_lbl = f"le{band}"
    rows: list[dict] = []

    def emit(idgeo, a1p, a2p, area, quintile, pop, num):
        # Skip slices whose population rounds to 0.00: their published
        # population_base would be 0 and `value` a 0/0 artefact (R1 finding —
        # e.g. COL "Medio Atrato" urban, a municipality with ~no school-age
        # population in that area class).
        if pop is None or round(float(pop), 2) <= 0:
            return
        rows.append({
            "isoalpha3": iso3, "idgeo": idgeo,
            "admin1_pcode": a1p, "admin1_name": a1_names.get(a1p, "") if a1p else "",
            "admin2_pcode": a2p, "admin2_name": a2_names.get(a2p, "") if a2p else "",
            "indicator": INDICATOR, "mode": mode,
            "education_level": level, "age": age,
            "sector": {"total": "Total", "public": "Public", "private": "Private"}[sector],
            "area": area, "quintile": quintile, "time_band": band_lbl,
            "value": round(100.0 * num / pop, 4),
            "population_base": round(float(pop), 2),
            "year": YEAR, "method": METHOD, "source": SOURCE,
        })

    # ── country level ───────────────────────────────────────────────────────
    emit("country", "", "", "Total", "Total", df["_pop"].sum(), df["_num"].sum())
    for area, g in _grouped(df, ["area"]).iterrows():
        emit("country", "", "", area, "Total", g["_pop"], g["_num"])
    for q, g in _grouped(df, ["q_pov"]).iterrows():
        emit("country", "", "", "Total", q, g["_pop"], g["_num"])
    for q, g in _grouped(df, ["q_rwi"]).iterrows():
        emit("country", "", "", "Total", q, g["_pop"], g["_num"])

    # ── admin1 level (area + quintile breakdowns) ───────────────────────────
    a1_tot = _grouped(df, ["ADM1_PCODE"])
    for a1p, g in a1_tot.iterrows():
        emit("admin1", a1p, "", "Total", "Total", g["_pop"], g["_num"])
    for (a1p, area), g in _grouped(df, ["ADM1_PCODE", "area"]).iterrows():
        emit("admin1", a1p, "", area, "Total", g["_pop"], g["_num"])
    for (a1p, q), g in _grouped(df, ["ADM1_PCODE", "q_pov"]).iterrows():
        emit("admin1", a1p, "", "Total", q, g["_pop"], g["_num"])
    for (a1p, q), g in _grouped(df, ["ADM1_PCODE", "q_rwi"]).iterrows():
        emit("admin1", a1p, "", "Total", q, g["_pop"], g["_num"])

    # ── admin2 level (area breakdown only) ──────────────────────────────────
    # ADM1-only countries (e.g. URY) have placeholder census-section adm2 that
    # the platform does not report — stop at admin1 for them.
    if iso3 not in ADM1_ONLY_ISOS:
        a2_to_a1 = df.dropna(subset=["ADM2_PCODE"]).groupby("ADM2_PCODE")["ADM1_PCODE"].first()
        for a2p, g in _grouped(df, ["ADM2_PCODE"]).iterrows():
            emit("admin2", a2_to_a1.get(a2p, ""), a2p, "Total", "Total", g["_pop"], g["_num"])
        for (a2p, area), g in _grouped(df, ["ADM2_PCODE", "area"]).iterrows():
            emit("admin2", a2_to_a1.get(a2p, ""), a2p, area, "Total", g["_pop"], g["_num"])

    return rows


# Columns whose tuple uniquely identifies one SCL row within a single-method
# table. `write_scl_output` uses these to drop an incoming country's old rows
# and to assert the table stays duplicate-free.
SCL_KEY_COLUMNS = [
    "isoalpha3", "idgeo", "admin1_pcode", "admin2_pcode",
    "indicator", "mode", "education_level", "age", "sector",
    "area", "quintile", "time_band",
]


def write_scl_output(out: pd.DataFrame, out_path: Path) -> Path:
    """Write the SCL long table with append-by-country semantics.

    A plain ``to_csv`` overwrites the whole file, so ``--countries ARG`` used to
    silently drop the other countries' rows (same class of bug fixed for the
    step-02 side report by ``upsert_by_iso``). Instead: keep every country we
    did NOT just recompute, replace the ones we did (by the ISOs actually
    present in ``out``, not the ones requested — a country that produced no rows
    keeps its old data), sort for a stable on-disk order, and write atomically
    so a crash mid-write cannot leave a truncated CSV.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out.empty:
        log.warning("No SCL rows produced — leaving %s untouched.", out_path.name)
        return out_path

    incoming = sorted(out["isoalpha3"].unique())
    if out_path.exists():
        existing = pd.read_csv(out_path, encoding="utf-8-sig",
                               keep_default_na=False, low_memory=False)
        kept = existing[~existing["isoalpha3"].isin(incoming)]
        log.info("Append-by-country: replaced %d rows for %s, kept %d others.",
                 len(existing) - len(kept), ", ".join(incoming), len(kept))
        out = pd.concat([kept, out], ignore_index=True)

    out = out.sort_values(SCL_KEY_COLUMNS, kind="mergesort").reset_index(drop=True)

    n_dup = int(out.duplicated(subset=SCL_KEY_COLUMNS).sum())
    if n_dup:
        raise RuntimeError(
            f"{out_path.name}: {n_dup} duplicate SCL key rows after append — "
            "uniqueness invariant violated, refusing to write.")

    tmp = out_path.with_suffix(".csv.tmp")
    out.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, out_path)
    log.info("Wrote %d rows → %s", len(out), out_path)
    return out_path


def run(countries: list[str]) -> Path:
    a1_names, a2_names = load_admin_names()
    all_rows: list[dict] = []

    for iso3 in countries:
        grid = load_grid(iso3)
        lons = grid["lon"].to_numpy(dtype=np.float64)
        lats = grid["lat"].to_numpy(dtype=np.float64)

        for mode in MODES:
            for level in LEVEL_META:
                pop_col = LEVEL_META[level][1]

                # Sample the public and private sector rasters, then derive the
                # "total" sector as the cell-wise minimum: the travel time to the
                # nearest school of ANY sector is the nearer of the public and
                # private times. This is exact by definition and — unlike the
                # stand-alone all-schools raster — guaranteed monotone. skfmm is
                # a numerical solver and is not exactly monotone in the source
                # set; on the large combined source blob it produced a "total"
                # raster slower than "public", which is physically impossible.
                #
                # A sector raster is absent when the country has no georeferenced
                # school of that sector (e.g. CRI: all 601 private schools lack
                # coordinates; HND is public-only by design). Treat the absent
                # sector as no-data — skip its rows rather than publish a spurious
                # 0% — and let "total" fall back to the sector that is present.
                pub_path = TT_DIR / iso3 / f"{iso3}_{mode}_{level}_public.tif"
                prv_path = TT_DIR / iso3 / f"{iso3}_{mode}_{level}_private.tif"
                tt_pub = sample_raster(pub_path, lons, lats) if pub_path.exists() else None
                tt_prv = sample_raster(prv_path, lons, lats) if prv_path.exists() else None

                if tt_pub is None and tt_prv is None:
                    log.warning("[%s/%s/%s] no public or private raster — skipping",
                                iso3, mode, level)
                    continue
                if tt_pub is None:
                    log.warning("[%s/%s/%s] no public raster — total = private only",
                                iso3, mode, level)
                    tt_tot = tt_prv
                elif tt_prv is None:
                    log.warning("[%s/%s/%s] no private raster — total = public only",
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
                    for band in TIME_BANDS:
                        reach = np.where(np.isfinite(tt) & (tt <= band), 1.0, 0.0)
                        work["_num"] = work["_pop"].to_numpy() * reach
                        all_rows.extend(aggregate_country(
                            work, iso3, mode, level, sector, band, a1_names, a2_names))
                    log.info("[%s/%s/%s/%s] reachable<=60min: %.1f%% of cells sampled",
                             iso3, mode, level, sector,
                             100.0 * np.mean(np.isfinite(tt) & (tt <= 60)))

    out = pd.DataFrame(all_rows)
    out_path = OUT_DIR / "accessibility_fmm_scl.csv"
    return write_scl_output(out, out_path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Step 10 — accessibility SCL aggregate.")
    ap.add_argument("--countries", nargs="+", required=True, help="ISO3 codes")
    ap.add_argument("--log-level", default="INFO",
                    choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level,
                        format="%(asctime)s  %(levelname)-7s  %(message)s",
                        datefmt="%H:%M:%S")
    run(args.countries)


if __name__ == "__main__":
    main()

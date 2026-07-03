"""
Step 09 — Travel times via Fast Marching Method (FMM).

For each (country × mode × level) combination this script:
  1. Loads the clipped friction raster from Step 07
     (data/transportation/surface_friction/clipped/{ISO}/{ISO}_{mode}_2019.tif).
  2. Filters LAC_schools_k12_clean.csv to schools offering that level.
  3. Runs multi-source FMM (skfmm.travel_time) with the school cells as sources.
  4. Writes a float32 GeoTIFF in minutes, LZW-compressed.

Output
------
data/transportation/travel_times/{ISO}/{ISO}_{mode}_{level}.tif

Total scope: 22 ANALYSIS_ISOS × 2 modes × 3 levels = 132 rasters.

Mean-latitude parameterization
------------------------------
The friction rasters are in EPSG:4326 (decimal degrees). To get physically
correct travel times we convert dx_deg → dx_m using cos(mean_lat) at the
country centroid (read from data/bounderys/LAC/level 0/lac-level-0.shp).
This was hard-coded to PAN (8.4°) in the pilot.

CLI
---
    uv run python pipeline/09_travel_time_fmm.py \
        --countries PAN \
        --modes walking motorized \
        --levels primaria secbaja secalta \
        --sectors total public private

Sector variants
---------------
--sectors {total,public,private} anchors the wavefront on all K-12 schools
(total) or only the public / private subset. total → {ISO}_{mode}_{level}.tif;
public / private → {ISO}_{mode}_{level}_{sector}.tif.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import skfmm
from rasterio.transform import rowcol

# Allow running from project root: `uv run python pipeline/09_travel_time_fmm.py`
sys.path.insert(0, str(Path(__file__).parent))
from constants import ANALYSIS_ISOS  # noqa: E402

log = logging.getLogger("step09_fmm")


# ────────────────────────── paths & catalogs ───────────────────────────────────
from _paths import DATA_ROOT  # noqa: E402

FRICTION_DIR = DATA_ROOT / "transportation" / "surface_friction" / "clipped"
OUTPUT_DIR = DATA_ROOT / "transportation" / "travel_times"
# Use the enriched schools base — it carries include_in_spatial_indicators,
# which is the authoritative gate set by Step 02 finalize for accessibility work.
# The "clean" base is identity-only and would silently include cluster_centroid
# placeholders and adm_mismatch rows as if they were valid GPS anchors.
SCHOOLS_PATH = DATA_ROOT / "schools" / "AR" / "LAC_schools_k12_with_context.csv"
ADM0_PATH = DATA_ROOT / "bounderys" / "LAC" / "level 0" / "lac-level-0.shp"

# Step 07 friction file naming: {ISO}_walking_2019.tif, {ISO}_motorized_2019.tif
MODE_TO_FRICTION_SUFFIX: dict[str, str] = {
    "walking": "walking_2019",
    "motorized": "motorized_2019",
}

# Level → column in LAC_schools_k12_clean.csv
LEVEL_TO_COLUMN: dict[str, str] = {
    "primaria": "nivel_primaria",
    "secbaja": "nivel_secbaja",
    "secalta": "nivel_secalta",
}

MODES = list(MODE_TO_FRICTION_SUFFIX)
LEVELS = list(LEVEL_TO_COLUMN)

# Sector → value in the `sector` column of LAC_schools_k12_with_context.csv.
# "total" anchors the wavefront on all K-12 schools (no sector filter); the
# public / private variants anchor only on that sector's schools, so the
# resulting raster answers "accessibility to a public/private school".
SECTOR_TO_VALUE: dict[str, str | None] = {
    "total": None,
    "public": "Public",
    "private": "Private",
}
SECTORS = list(SECTOR_TO_VALUE)


# ────────────────────────── ISO3 ↔ ADM0_PCODE mapping ──────────────────────────
# lac-level-0.shp uses ADM0_PCODE which is *mostly* the ISO 3166-1 alpha-2 code
# but with three legacy 3-letter exceptions (ARG, BHS, JAM). All other rows are
# 2-letter codes (PA, BR, MX, ...). Build the map dynamically against the file.

def _build_iso3_to_adm0_pcode() -> dict[str, str]:
    """Return {ISO3 → ADM0_PCODE} for the 22 ANALYSIS_ISOS."""
    adm0 = gpd.read_file(ADM0_PATH)
    pcodes = set(adm0["ADM0_PCODE"].astype(str))
    iso2_overrides = {
        "PAN": "PA", "BRA": "BR", "MEX": "MX", "COL": "CO", "CHL": "CL",
        "PER": "PE", "ECU": "EC", "BOL": "BO", "PRY": "PY", "URY": "UY",
        "VEN": "VE", "GTM": "GT", "HND": "HN", "SLV": "SV", "CRI": "CR",
        "NIC": "NI", "DOM": "DO", "HTI": "HT", "BLZ": "BZ", "SUR": "SR",
        "GUY": "GY", "BRB": "BB", "TTO": "TT",
    }
    result: dict[str, str] = {}
    for iso3 in ANALYSIS_ISOS:
        if iso3 in pcodes:
            result[iso3] = iso3
        elif iso2_overrides.get(iso3) in pcodes:
            result[iso3] = iso2_overrides[iso3]
        else:
            raise RuntimeError(f"No ADM0_PCODE in lac-level-0.shp for ISO3={iso3}")
    return result


def get_country_mean_lat(iso3: str, iso3_to_pcode: dict[str, str]) -> float:
    """Centroid latitude of the country's ADM0 polygon (degrees)."""
    pcode = iso3_to_pcode[iso3]
    adm0 = gpd.read_file(ADM0_PATH)
    row = adm0[adm0["ADM0_PCODE"] == pcode]
    if row.empty:
        raise RuntimeError(f"ADM0_PCODE={pcode} not in {ADM0_PATH.name}")
    # Reproject to UTM for an honest planar centroid, then back to 4326
    utm = row.to_crs(row.estimate_utm_crs())
    return float(utm.geometry.centroid.to_crs(4326).y.iloc[0])


# ────────────────────────── FMM core (adapted from pilot) ──────────────────────

def _cell_size_metres(transform: rasterio.Affine, mean_lat_deg: float) -> tuple[float, float]:
    """Return (dy_metres, dx_metres) for a geographic (EPSG:4326) transform."""
    dy_deg = abs(transform.e)
    dx_deg = abs(transform.a)
    dy_m = dy_deg * 110_574.0
    dx_m = dx_deg * 111_320.0 * float(np.cos(np.radians(mean_lat_deg)))
    return dy_m, dx_m


def _load_friction(path: Path) -> tuple[np.ndarray, np.ndarray, rasterio.DatasetReader]:
    """
    Load a friction raster.

    Returns
    -------
    friction : (H, W) float64 — values in min/m; nodata cells → NaN
    mask     : (H, W) bool   — True where cell should be treated as impassable
    src      : open rasterio dataset (caller must close)
    """
    src = rasterio.open(path)
    raw = src.read(1).astype(np.float64)

    mask = np.zeros(raw.shape, dtype=bool)
    if src.nodata is not None:
        mask |= (raw == src.nodata)
    mask |= ~np.isfinite(raw)
    mask |= (raw <= 0.0)

    friction = np.where(mask, np.nan, raw)
    log.info("  friction loaded: shape=%s  valid=%d  masked=%d",
             raw.shape, int((~mask).sum()), int(mask.sum()))
    return friction, mask, src


def _rasterise_schools(
    school_lons: np.ndarray,
    school_lats: np.ndarray,
    transform: rasterio.Affine,
    shape: tuple[int, int],
    mask: np.ndarray,
) -> np.ndarray:
    """
    Convert school lon/lat to a boolean source mask on the raster grid.

    Schools that fall on masked (ocean/nodata) cells are snapped to the nearest
    valid land cell within a 3-pixel search radius so coastal schools still
    anchor the wavefront.
    """
    H, W = shape
    source_mask = np.zeros((H, W), dtype=bool)

    rows, cols = rowcol(transform, school_lons, school_lats)
    rows = np.asarray(rows, dtype=int)
    cols = np.asarray(cols, dtype=int)

    in_bounds = (rows >= 0) & (rows < H) & (cols >= 0) & (cols < W)
    rows, cols = rows[in_bounds], cols[in_bounds]

    valid_mask = ~mask
    snapped_r, snapped_c = [], []
    for r, c in zip(rows, cols):
        if valid_mask[r, c]:
            snapped_r.append(r)
            snapped_c.append(c)
            continue
        found = False
        for radius in range(1, 4):
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < H and 0 <= nc < W and valid_mask[nr, nc]:
                        snapped_r.append(nr)
                        snapped_c.append(nc)
                        found = True
                        break
                if found:
                    break
            if found:
                break

    if snapped_r:
        source_mask[snapped_r, snapped_c] = True

    log.info("  source cells: %d  (from %d schools — collisions reflect dedup at ~1km cell)",
             int(source_mask.sum()), len(school_lons))
    return source_mask


def _compute_travel_time(
    friction_path: Path,
    school_lons: np.ndarray,
    school_lats: np.ndarray,
    output_path: Path,
    mean_lat_deg: float,
    overwrite: bool,
) -> Path:
    if output_path.exists() and not overwrite:
        log.info("  already exists, skipping → %s", output_path.name)
        return output_path

    if len(school_lons) == 0:
        log.warning("  zero schools for this combination — skipping")
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)

    friction, mask, src = _load_friction(friction_path)
    try:
        dy_m, dx_m = _cell_size_metres(src.transform, mean_lat_deg)
        log.info("  cell size: dy=%.1f m  dx=%.1f m  (mean_lat=%.3f°)", dy_m, dx_m, mean_lat_deg)

        source_mask = _rasterise_schools(
            school_lons, school_lats, src.transform, src.shape, mask
        )

        if not source_mask.any():
            log.warning("  no school cells landed on the grid — skipping")
            return output_path

        phi_data = np.where(source_mask, -1.0, 1.0)
        phi = np.ma.MaskedArray(phi_data, mask=mask)

        with np.errstate(divide="ignore", invalid="ignore"):
            speed_data = np.where(mask, 0.0, 1.0 / friction)
        speed = np.ma.MaskedArray(speed_data, mask=mask)

        log.info("  running skfmm.travel_time shape=%s …", friction.shape)
        travel_times = skfmm.travel_time(phi, speed=speed, dx=(dy_m, dx_m))

        if np.ma.is_masked(travel_times):
            tt_arr = travel_times.filled(np.nan).astype(np.float32)
        else:
            tt_arr = np.asarray(travel_times, dtype=np.float32)

        # Clamp tiny negative artifacts from skfmm (numerical noise in cells
        # adjacent to sources; observed magnitudes <15 min affecting <0.01% of cells).
        neg_mask = (tt_arr < 0) & np.isfinite(tt_arr)
        if neg_mask.any():
            n_neg = int(neg_mask.sum())
            worst = float(tt_arr[neg_mask].min())
            log.info("  clamped %d negative cells (worst=%.2f min) → 0", n_neg, worst)
            tt_arr = np.where(neg_mask, 0.0, tt_arr).astype(np.float32)

        log.info("  travel-time range: %.1f – %.1f min",
                 float(np.nanmin(tt_arr)), float(np.nanmax(tt_arr)))

        profile = src.profile.copy()
        profile.update(dtype="float32", count=1, nodata=np.nan,
                       compress="lzw", predictor=2)
        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(tt_arr, 1)

        log.info("  saved → %s", output_path)
    finally:
        src.close()

    return output_path


# ────────────────────────── orchestration ──────────────────────────────────────

def _load_schools() -> pd.DataFrame:
    """Read the enriched schools base with the minimal columns we need."""
    required = ["adm0_pcode", "id_centro", "latitud", "longitud",
                "nivel_primaria", "nivel_secbaja", "nivel_secalta",
                "sector", "include_in_spatial_indicators"]
    df = pd.read_csv(SCHOOLS_PATH, low_memory=False)
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"{SCHOOLS_PATH.name} missing columns: {missing}")
    return df


def _schools_for_country_level(
    schools_df: pd.DataFrame, iso3: str, level: str, sector: str = "total"
) -> tuple[np.ndarray, np.ndarray]:
    """
    Return (lons, lats) for schools in country offering this level.

    Filters:
      - adm0_pcode == iso3
      - the level flag == 1
      - latitud / longitud not null
      - include_in_spatial_indicators == True (Step 02 finalize gate)
      - sector == SECTOR_TO_VALUE[sector] when sector != "total"

    Rows where include_in_spatial_indicators is NaN are EXCLUDED — that bucket
    is the "review explicitly" cohort and must not silently anchor a wavefront.
    """
    col = LEVEL_TO_COLUMN[level]
    mask = (
        (schools_df["adm0_pcode"] == iso3)
        & (schools_df[col] == 1)
        & schools_df["latitud"].notna()
        & schools_df["longitud"].notna()
        & (schools_df["include_in_spatial_indicators"] == True)  # noqa: E712
    )
    sector_value = SECTOR_TO_VALUE[sector]
    if sector_value is not None:
        mask &= (schools_df["sector"] == sector_value)
    sub = schools_df[mask]
    return sub["longitud"].to_numpy(dtype=np.float64), sub["latitud"].to_numpy(dtype=np.float64)


def _friction_path(iso3: str, mode: str) -> Path:
    return FRICTION_DIR / iso3 / f"{iso3}_{MODE_TO_FRICTION_SUFFIX[mode]}.tif"


def _output_path(iso3: str, mode: str, level: str, sector: str = "total") -> Path:
    # sector="total" keeps the original unsuffixed name (backward compatible);
    # public / private get an explicit suffix.
    stem = f"{iso3}_{mode}_{level}" if sector == "total" else f"{iso3}_{mode}_{level}_{sector}"
    return OUTPUT_DIR / iso3 / f"{stem}.tif"


def run(
    countries: list[str],
    modes: list[str],
    levels: list[str],
    sectors: list[str] | None = None,
    overwrite: bool = False,
) -> list[Path]:
    sectors = sectors or ["total"]
    schools_df = _load_schools()
    log.info("Loaded %d schools from %s", len(schools_df), SCHOOLS_PATH.name)

    iso3_to_pcode = _build_iso3_to_adm0_pcode()

    written: list[Path] = []
    for iso3 in countries:
        if iso3 not in ANALYSIS_ISOS:
            log.warning("[%s] not in ANALYSIS_ISOS — skipping", iso3)
            continue

        mean_lat = get_country_mean_lat(iso3, iso3_to_pcode)
        log.info("[%s] mean_lat=%.3f°", iso3, mean_lat)

        for mode in modes:
            fric_path = _friction_path(iso3, mode)
            if not fric_path.exists():
                log.error("[%s/%s] friction missing → %s", iso3, mode, fric_path)
                continue

            for level in levels:
                for sector in sectors:
                    lons, lats = _schools_for_country_level(schools_df, iso3, level, sector)
                    out_path = _output_path(iso3, mode, level, sector)
                    log.info("[%s/%s/%s/%s] schools=%d → %s",
                             iso3, mode, level, sector, len(lons), out_path.name)
                    _compute_travel_time(fric_path, lons, lats, out_path, mean_lat, overwrite)
                    if out_path.exists():
                        written.append(out_path)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description="Step 09 — Travel times via FMM.")
    ap.add_argument("--countries", nargs="+", default=["all"],
                    help="ISO3 codes or 'all' (= ANALYSIS_ISOS)")
    ap.add_argument("--modes", nargs="+", default=MODES, choices=MODES)
    ap.add_argument("--levels", nargs="+", default=LEVELS, choices=LEVELS)
    ap.add_argument("--sectors", nargs="+", default=["total"], choices=SECTORS,
                    help="School sector(s) to anchor the wavefront on")
    ap.add_argument("--overwrite", action="store_true",
                    help="Recompute even if output exists")
    ap.add_argument("--log-level", default="INFO",
                    choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    countries = ANALYSIS_ISOS if args.countries == ["all"] else args.countries
    log.info("Countries: %s", countries)
    log.info("Modes: %s   Levels: %s   Sectors: %s", args.modes, args.levels, args.sectors)

    written = run(countries, args.modes, args.levels, args.sectors, args.overwrite)
    log.info("Done. %d rasters written or up to date.", len(written))


if __name__ == "__main__":
    main()

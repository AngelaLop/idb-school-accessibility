"""
06_pop_grid.py
--------------
Build the canonical population base grid at 1 km resolution for each country
in the platform. One row per populated 1 km cell; each cell carries:

  - cell_id, lat, lon
  - pop_total                   from WorldPop 100m → 1km block sum (10x10)
  - pop_5_9, pop_10_14, pop_15_19  from LAC f+m 1km school-age rasters
  - area_class                  urbana / no_urbana / dispersa per definitions.md
                                (contiguous-cluster classification, 8-connectivity)
  - ADM1_PCODE, ADM2_PCODE      BID admin polygons rasterized to the 1km grid
  - rwi, rwi_error, rwi_dist_km Meta RWI nearest-cell join (NaN if >5km)
  - poverty_rate_adm2, nbi_rate_adm2  IDB Mapa de Pobreza inherited per ADM2

This is the foundation table for downstream accessibility computation, equity
stratification, and the Población / Nivel Socioeconómico / Área dashboard
section.

Inputs
------
  data/population/WorldPop/{ISO}/{iso}_pop_2023_CN_100m_R2025A_v1.tif
  data/population/WorldPop/LAC/clipped_global_{f,m}_{05,10,15}_2023_CN_1km_R2025A_UA_v1.tif
  data/bounderys/LAC/level 2/lac-level-2.shp
  data/Poverty Rates/lac-level-2.csv
  data/Poverty Rates/meta-rwi/{ISO}/{iso}_relative_wealth_index.csv  (optional)

Outputs
-------
  data/population/WorldPop/processed/population_grid_{ISO}.csv
  data/population/WorldPop/processed/_manifest.csv         (per-country summary)

Usage
-----
  uv run python pipeline/06_pop_grid.py --countries COL
  uv run python pipeline/06_pop_grid.py --countries all
  uv run python pipeline/06_pop_grid.py --countries ARG BRA MEX  # subset
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio import features
from rasterio.enums import Resampling
from rasterio.warp import reproject
from scipy.ndimage import label
from scipy.spatial import cKDTree

warnings.filterwarnings("ignore")

from _paths import REPO_ROOT as ROOT, DATA_ROOT  # noqa: E402
sys.path.insert(0, str(ROOT / "pipeline"))
from constants import PIPELINE_ISOS  # noqa: E402

POP_DIR = DATA_ROOT / "population" / "WorldPop"
LAC_DIR = POP_DIR / "LAC"
OUT_DIR = POP_DIR / "processed"
RWI_DIR = DATA_ROOT / "Poverty Rates" / "meta-rwi"
ADM1_SHP = DATA_ROOT / "bounderys" / "LAC" / "level 1" / "lac-level-1.shp"
ADM2_SHP = DATA_ROOT / "bounderys" / "LAC" / "level 2" / "lac-level-2.shp"
POVERTY_CSV = DATA_ROOT / "Poverty Rates" / "lac-level-2.csv"

LAC_RASTERS = {
    "pop_5_9":   ["clipped_global_f_05_2023_CN_1km_R2025A_UA_v1.tif",
                  "clipped_global_m_05_2023_CN_1km_R2025A_UA_v1.tif"],
    "pop_10_14": ["clipped_global_f_10_2023_CN_1km_R2025A_UA_v1.tif",
                  "clipped_global_m_10_2023_CN_1km_R2025A_UA_v1.tif"],
    "pop_15_19": ["clipped_global_f_15_2023_CN_1km_R2025A_UA_v1.tif",
                  "clipped_global_m_15_2023_CN_1km_R2025A_UA_v1.tif"],
}

# definitions.md thresholds (per asentamiento contiguo de celdas con densidad>=150)
URBAN_DENSITY = 300.0       # hab / km²
URBAN_MIN_POP = 5000.0
SEMIURBAN_MIN_DENSITY = 150.0
SEMIURBAN_MIN_POP = 200.0

BLOCK_100M_TO_1KM = 10
RWI_DIST_MASK_KM = 5.0


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_pop_raster(iso: str) -> Path | None:
    iso_dir = POP_DIR / iso
    if not iso_dir.exists():
        return None
    tifs = sorted(iso_dir.glob("*pop_*100m*.tif"))
    return tifs[0] if tifs else None


def _aggregate_100m_to_1km(pop_path: Path):
    """Aggregate WorldPop 100m raster to 1km via warp.reproject with
    Resampling.sum. Uses GDAL's tiled IO so peak memory stays bounded
    even on the BRA raster (~9 GB if loaded whole)."""
    block = BLOCK_100M_TO_1KM
    with rasterio.open(pop_path) as src:
        h_new, w_new = src.height // block, src.width // block
        nodata = src.nodata
        new_transform = rasterio.Affine(
            src.transform.a * block, src.transform.b, src.transform.c,
            src.transform.d, src.transform.e * block, src.transform.f,
        )
        pop_1km = np.zeros((h_new, w_new), dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1),
            destination=pop_1km,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=new_transform,
            dst_crs=src.crs,
            resampling=Resampling.sum,
            src_nodata=nodata,
            dst_nodata=0,
        )
    pop_1km = np.where(pop_1km < 0, 0.0, pop_1km).astype(np.float32)

    rows, cols = np.indices(pop_1km.shape)
    lats = new_transform.f + (rows + 0.5) * new_transform.e
    lons = new_transform.c + (cols + 0.5) * new_transform.a
    return pop_1km, new_transform, lats, lons


def _classify_area(pop_1km: np.ndarray, lats: np.ndarray) -> np.ndarray:
    pixel_y_deg = abs(lats[1, 0] - lats[0, 0]) if lats.shape[0] > 1 else 0.0083333
    cell_h_km = pixel_y_deg * 111.0
    cell_area_km2 = cell_h_km * cell_h_km * np.cos(np.radians(lats))

    density = np.divide(
        pop_1km, cell_area_km2,
        out=np.zeros_like(pop_1km), where=cell_area_km2 > 0,
    )
    candidate = density >= SEMIURBAN_MIN_DENSITY
    structure = np.ones((3, 3), dtype=int)
    cluster_labels, n_clusters = label(candidate, structure=structure)

    out = np.full(pop_1km.shape, "dispersa", dtype="U10")
    if n_clusters > 0:
        flat_labels = cluster_labels.ravel()
        flat_pop = pop_1km.ravel()
        flat_area = cell_area_km2.ravel()
        cluster_pop = np.bincount(flat_labels, weights=flat_pop)
        cluster_area = np.bincount(flat_labels, weights=flat_area)
        cluster_density = np.divide(
            cluster_pop, cluster_area,
            out=np.zeros_like(cluster_pop), where=cluster_area > 0,
        )

        urban_ids = np.where(
            (cluster_density >= URBAN_DENSITY) & (cluster_pop >= URBAN_MIN_POP)
        )[0]
        urban_ids = urban_ids[urban_ids != 0]
        nonurban_ids = np.where(
            (cluster_pop >= SEMIURBAN_MIN_POP) & (cluster_pop < URBAN_MIN_POP)
        )[0]
        nonurban_ids = nonurban_ids[nonurban_ids != 0]
        large_low_density = np.where(
            (cluster_pop >= URBAN_MIN_POP) & (cluster_density < URBAN_DENSITY)
        )[0]
        large_low_density = large_low_density[large_low_density != 0]

        urban_mask = np.isin(cluster_labels, urban_ids)
        nonurban_mask = np.isin(cluster_labels, np.concatenate([nonurban_ids, large_low_density]))
        out[urban_mask] = "urbana"
        out[nonurban_mask] = "no_urbana"

    return out, n_clusters


def _sample_lac_rasters(lats_flat: np.ndarray, lons_flat: np.ndarray) -> dict:
    out = {}
    for col_name, files in LAC_RASTERS.items():
        total = np.zeros(len(lats_flat), dtype=np.float32)
        for fname in files:
            path = LAC_DIR / fname
            if not path.exists():
                raise FileNotFoundError(f"Missing LAC raster: {path}")
            with rasterio.open(path) as src:
                nodata = src.nodata
                rows = ((src.transform.f - lats_flat) / -src.transform.e).astype(np.int64)
                cols = ((lons_flat - src.transform.c) / src.transform.a).astype(np.int64)
                in_bounds = (rows >= 0) & (rows < src.height) & (cols >= 0) & (cols < src.width)
                vals = np.zeros(len(lats_flat), dtype=np.float32)
                if in_bounds.any():
                    arr = src.read(1)
                    raw = arr[rows[in_bounds], cols[in_bounds]].astype(np.float32)
                    raw = np.where(raw == nodata, 0.0, raw)
                    raw = np.where(raw < 0, 0.0, raw)
                    vals[in_bounds] = raw
            total += vals
        out[col_name] = total
    return out


def _rasterize_adm(iso: str, shape: tuple, transform: rasterio.Affine):
    """Rasterize BID admin polygons. Try ADM2 first; fall back to ADM1 for
    countries not covered at ADM2 level (BHS, BLZ, BRB, JAM in BID 2025).
    Returns (raster, idx_to_adm2, idx_to_adm1, level_used) where level_used
    is 'adm2' or 'adm1'."""
    adm2 = gpd.read_file(ADM2_SHP)
    adm2 = adm2[adm2["ADM0_PCODE"] == iso].copy().reset_index(drop=True)
    if len(adm2) > 0:
        adm2["_int"] = np.arange(1, len(adm2) + 1, dtype=np.uint32)
        idx_to_adm2 = dict(zip(adm2["_int"].values, adm2["ADM2_PCODE"].values))
        idx_to_adm1 = dict(zip(adm2["_int"].values, adm2["ADM1_PCODE"].values))
        shapes = ((geom, val) for geom, val in zip(adm2.geometry, adm2["_int"]))
        raster = features.rasterize(
            shapes, out_shape=shape, transform=transform,
            fill=0, dtype="uint32",
        )
        return raster, idx_to_adm2, idx_to_adm1, "adm2"

    # Fallback: use ADM1
    adm1 = gpd.read_file(ADM1_SHP)
    adm1 = adm1[adm1["ADM0_PCODE"] == iso].copy().reset_index(drop=True)
    if len(adm1) == 0:
        return None, {}, {}, "none"
    adm1["_int"] = np.arange(1, len(adm1) + 1, dtype=np.uint32)
    idx_to_adm2: dict = {int(i): None for i in adm1["_int"].values}
    idx_to_adm1 = dict(zip(adm1["_int"].values, adm1["ADM1_PCODE"].values))
    shapes = ((geom, val) for geom, val in zip(adm1.geometry, adm1["_int"]))
    raster = features.rasterize(
        shapes, out_shape=shape, transform=transform,
        fill=0, dtype="uint32",
    )
    return raster, idx_to_adm2, idx_to_adm1, "adm1"


# ──────────────────────────────────────────────────────────────────────────────
# Main per-country build
# ──────────────────────────────────────────────────────────────────────────────

def build_population_grid(iso: str) -> dict:
    t0 = perf_counter()
    stats = {"iso": iso, "status": "pending"}

    pop_path = _resolve_pop_raster(iso)
    if pop_path is None:
        print(f"[{iso}] SKIP: no WorldPop 100m raster in {POP_DIR / iso}")
        stats["status"] = "skipped_no_worldpop"
        return stats

    print(f"[{iso}] >>> {pop_path.name}")

    # 1. Aggregate 100m -> 1km
    pop_1km, transform_1km, lats, lons = _aggregate_100m_to_1km(pop_path)
    stats["grid_h"] = pop_1km.shape[0]
    stats["grid_w"] = pop_1km.shape[1]
    stats["pop_total"] = float(pop_1km.sum())
    print(f"[{iso}] grid {pop_1km.shape[0]} x {pop_1km.shape[1]}, "
          f"pop = {stats['pop_total']:,.0f}")

    # 2. Area classification
    area_arr, n_clusters = _classify_area(pop_1km, lats)
    stats["n_clusters_density150"] = int(n_clusters)

    # 3. Rasterize admin (ADM2 first, fall back to ADM1)
    adm_int_arr, idx_to_adm2, idx_to_adm1, level = _rasterize_adm(iso, pop_1km.shape, transform_1km)
    if adm_int_arr is None:
        print(f"[{iso}] SKIP: no ADM polygons in BID shapefiles")
        stats["status"] = "skipped_no_polygons"
        return stats
    stats["adm_level_used"] = level
    if level == "adm1":
        print(f"[{iso}] using ADM1 fallback (no ADM2 polygons in BID)")

    # 4. Mask to populated cells
    mask = pop_1km > 0
    n_cells = int(mask.sum())
    stats["n_populated_cells"] = n_cells
    if n_cells == 0:
        print(f"[{iso}] SKIP: no populated cells")
        stats["status"] = "skipped_empty"
        return stats

    df = pd.DataFrame({
        "lat": lats[mask],
        "lon": lons[mask],
        "pop_total": pop_1km[mask],
        "area_class": area_arr[mask],
        "_adm_int": adm_int_arr[mask],
    })
    df["ADM2_PCODE"] = df["_adm_int"].map(idx_to_adm2)
    df["ADM1_PCODE"] = df["_adm_int"].map(idx_to_adm1)
    df = df.drop(columns="_adm_int")
    df.insert(0, "cell_id", np.arange(len(df)))

    n_no_adm = int(df["ADM2_PCODE"].isna().sum())
    stats["n_cells_outside_adm2"] = n_no_adm

    # 5. School-age sampling
    school_age = _sample_lac_rasters(df["lat"].to_numpy(), df["lon"].to_numpy())
    for k, v in school_age.items():
        df[k] = v
    stats["pop_5_9_total"] = float(df["pop_5_9"].sum())
    stats["pop_10_14_total"] = float(df["pop_10_14"].sum())
    stats["pop_15_19_total"] = float(df["pop_15_19"].sum())

    # 6. Nearest RWI cell
    rwi_path = RWI_DIR / iso / f"{iso.lower()}_relative_wealth_index.csv"
    if rwi_path.exists():
        rwi = pd.read_csv(rwi_path)
        tree = cKDTree(rwi[["latitude", "longitude"]].to_numpy())
        dist_deg, idx = tree.query(df[["lat", "lon"]].to_numpy(), k=1)
        df["rwi"] = rwi["rwi"].to_numpy()[idx]
        df["rwi_error"] = rwi["error"].to_numpy()[idx]
        df["rwi_dist_km"] = dist_deg * 111.0
        too_far = df["rwi_dist_km"] > RWI_DIST_MASK_KM
        df.loc[too_far, ["rwi", "rwi_error"]] = np.nan
        stats["n_rwi_cells_input"] = len(rwi)
        stats["n_cells_with_rwi"] = int(df["rwi"].notna().sum())
        stats["n_cells_rwi_masked_5km"] = int(too_far.sum())
    else:
        df["rwi"] = np.nan
        df["rwi_error"] = np.nan
        df["rwi_dist_km"] = np.nan
        stats["n_rwi_cells_input"] = 0
        stats["n_cells_with_rwi"] = 0
        stats["n_cells_rwi_masked_5km"] = 0

    # 7. IDB poverty merge
    poverty = pd.read_csv(POVERTY_CSV)
    poverty = poverty[poverty["ADM0_PCODE"] == iso][
        ["ADM2_PCODE", "POVERTY_RATE", "NBI_RATE"]
    ].rename(columns={"POVERTY_RATE": "poverty_rate_adm2", "NBI_RATE": "nbi_rate_adm2"})
    stats["poverty_rows_idb"] = len(poverty)
    df = df.merge(poverty, on="ADM2_PCODE", how="left")
    stats["n_cells_with_poverty"] = int(df["poverty_rate_adm2"].notna().sum())
    stats["n_cells_with_nbi"] = int(df["nbi_rate_adm2"].notna().sum())

    # 8. Final ordering & save
    cols = [
        "cell_id", "lat", "lon",
        "pop_total", "pop_5_9", "pop_10_14", "pop_15_19",
        "area_class",
        "ADM1_PCODE", "ADM2_PCODE",
        "rwi", "rwi_error", "rwi_dist_km",
        "poverty_rate_adm2", "nbi_rate_adm2",
    ]
    df = df[cols]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"population_grid_{iso}.csv"
    df.to_csv(out_path, index=False)
    stats["output_path"] = str(out_path.relative_to(ROOT))
    stats["output_size_mb"] = round(out_path.stat().st_size / 1024 ** 2, 1)

    pop_by_area = df.groupby("area_class")["pop_total"].sum()
    pop_share = (pop_by_area / pop_by_area.sum() * 100).round(2)
    stats["pct_pop_urbana"] = float(pop_share.get("urbana", 0.0))
    stats["pct_pop_no_urbana"] = float(pop_share.get("no_urbana", 0.0))
    stats["pct_pop_dispersa"] = float(pop_share.get("dispersa", 0.0))

    elapsed = perf_counter() - t0
    stats["elapsed_sec"] = round(elapsed, 1)
    stats["status"] = "ok"
    print(f"[{iso}] OK in {elapsed:.1f}s, {n_cells:,} cells, "
          f"urb={stats['pct_pop_urbana']:.1f}%, "
          f"semi={stats['pct_pop_no_urbana']:.1f}%, "
          f"disp={stats['pct_pop_dispersa']:.1f}% "
          f"-> {out_path.name} ({stats['output_size_mb']} MB)")
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[2])
    p.add_argument(
        "--countries",
        nargs="+",
        default=["COL"],
        help="ISO codes to process, or 'all' for the full pipeline scope.",
    )
    args = p.parse_args()

    isos = list(PIPELINE_ISOS) if args.countries == ["all"] else args.countries

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for iso in isos:
        try:
            rows.append(build_population_grid(iso))
        except Exception as e:  # noqa: BLE001
            print(f"[{iso}] ERROR: {type(e).__name__}: {e}")
            rows.append({"iso": iso, "status": f"error_{type(e).__name__}", "error_msg": str(e)})

    manifest = pd.DataFrame(rows)
    out = OUT_DIR / "_manifest.csv"
    manifest.to_csv(out, index=False)
    print(f"\n=== Manifest -> {out} ===")
    summary_cols = ["iso", "status", "adm_level_used", "n_populated_cells", "pop_total",
                    "pct_pop_urbana", "pct_pop_no_urbana", "pct_pop_dispersa",
                    "n_cells_with_rwi", "n_cells_with_poverty", "n_cells_with_nbi",
                    "elapsed_sec"]
    summary_cols = [c for c in summary_cols if c in manifest.columns]
    print(manifest[summary_cols].to_string(index=False))


if __name__ == "__main__":
    main()

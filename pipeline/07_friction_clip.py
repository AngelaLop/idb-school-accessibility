"""
07_friction_clip.py
-------------------
Clip the three global Malaria Atlas Project (MAP) friction surfaces to each
country in the LAC accessibility platform (PIPELINE_ISOS, currently 23).

Friction surfaces are minutes-per-metre rasters: the time cost of traversing a
1 km cell at the natural speed of the dominant land-cover / road-network type
underneath. They are the input to a Fast Marching Method (FMM) eikonal solver
that produces a travel-time-to-nearest-school raster downstream (Step 09, TBD).

This script does NOT compute travel times. It only slices the global rasters
into per-country tiles so that downstream FMM / aggregation steps can load
small per-country arrays into memory.

Inputs
------
  data/transportation/surface_friction/201501_Global_Travel_Speed_Friction_Surface_2015/...tif
  data/transportation/surface_friction/202001_Global_Motorized_Friction_Surface_2019/...tif
  data/transportation/surface_friction/202001_Global_Walking_Only_Friction_Surface_2019/...tif
  data/bounderys/LAC/level 0/lac-level-0.shp

All rasters: EPSG:4326, 17400 x 43200, ~30 arc-second (~1 km at equator),
float32. Motorized/walking 2019 use NoData=-9999; travel-speed 2015 has no
explicit NoData tag (caller must handle 0 / negative values).

Outputs
-------
  data/transportation/surface_friction/clipped/{ISO}/{ISO}_travel_speed_2015.tif
  data/transportation/surface_friction/clipped/{ISO}/{ISO}_motorized_2019.tif
  data/transportation/surface_friction/clipped/{ISO}/{ISO}_walking_2019.tif
  data/transportation/surface_friction/clipped/_manifest.csv

Each output is LZW-compressed GeoTIFF, same CRS and pixel grid as source
(no resampling). The read window is the country's ADM0 bounding box padded
by ``--buffer`` degrees on every side. Cells just beyond the border keep
their friction values — downstream FMM needs that context to compute
correct travel times for schools and populations near the boundary. We do
NOT mask with the polygon itself, both because (a) FMM near borders requires
continuous friction outside ADM0, and (b) buffering complex multipolygons
(ARG with islands) reliably blows shapely's GEOS allocator.

Usage
-----
  uv run python pipeline/07_friction_clip.py --countries all
  uv run python pipeline/07_friction_clip.py --countries PAN
  uv run python pipeline/07_friction_clip.py --countries ARG BRA --force

Notes
-----
- ADM0 shapefile has mixed pcode lengths (ARG/BHS/JAM are 3-letter; the rest
  are 2-letter). ADM0_PCODE_TO_ISO3 below resolves the mapping.
- Buffer default = 0.05 deg (~5 km at the equator), applied to the bounding
  box, not to the polygon. Override with --buffer.
- Output naming intentionally drops the "Global_*" prefix to keep paths short.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from time import perf_counter

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.windows import from_bounds

from _paths import REPO_ROOT as ROOT, DATA_ROOT  # noqa: E402
sys.path.insert(0, str(ROOT / "pipeline"))
from constants import PIPELINE_ISOS  # noqa: E402

FRICTION_DIR = DATA_ROOT / "transportation" / "surface_friction"
ADM0_SHP = DATA_ROOT / "bounderys" / "LAC" / "level 0" / "lac-level-0.shp"
OUT_DIR = FRICTION_DIR / "clipped"

GLOBAL_RASTERS: dict[str, Path] = {
    "travel_speed_2015": FRICTION_DIR
        / "201501_Global_Travel_Speed_Friction_Surface_2015"
        / "201501_Global_Travel_Speed_Friction_Surface_2015.tif",
    "motorized_2019": FRICTION_DIR
        / "202001_Global_Motorized_Friction_Surface_2019"
        / "202001_Global_Motorized_Friction_Surface_2019.tif",
    "walking_2019": FRICTION_DIR
        / "202001_Global_Walking_Only_Friction_Surface_2019"
        / "202001_Global_Walking_Only_Friction_Surface_2019.tif",
}

# Map ADM0_PCODE values in lac-level-0.shp to PIPELINE_ISOS (3-letter ISO).
# Three pcodes already match (ARG, BHS, JAM); the other 20 are 2-letter.
ADM0_PCODE_TO_ISO3: dict[str, str] = {
    "ARG": "ARG",
    "BB":  "BRB",
    "BHS": "BHS",
    "BO":  "BOL",
    "BR":  "BRA",
    "BZ":  "BLZ",
    "CL":  "CHL",
    "CO":  "COL",
    "CR":  "CRI",
    "DO":  "DOM",
    "EC":  "ECU",
    "GT":  "GTM",
    "GY":  "GUY",
    "HN":  "HND",
    "HT":  "HTI",
    "JAM": "JAM",
    "MX":  "MEX",
    "PA":  "PAN",
    "PE":  "PER",
    "PY":  "PRY",
    "SR":  "SUR",
    "SV":  "SLV",
    "UY":  "URY",
}


def _load_country_geom(iso3: str, gdf: gpd.GeoDataFrame):
    """Return the (unbuffered) ADM0 geometry for one ISO3, or None.

    We do NOT buffer the polygon itself — buffering large multipolygons
    (e.g. ARG with islands) causes GEOS allocation failures and is also
    unnecessary. The read window is expanded by `buffer_deg` so that the
    output raster includes a margin around the country; the polygon mask
    is then applied unbuffered inside that window."""
    match = gdf[gdf["_iso3"] == iso3]
    if match.empty:
        return None
    if hasattr(match.geometry, "union_all"):
        return match.geometry.union_all()
    return match.geometry.unary_union


def _clip_one(src_path: Path, geom, buffer_deg: float, out_path: Path) -> dict:
    """Clip src_path to a buffered bounding box around ``geom``. LZW GeoTIFF.

    Strategy:
      1. Compute ``geom.bounds`` and pad by ``buffer_deg`` on every side.
      2. Read only that window from the global raster (fast — no full-raster scan).

    The polygon mask is intentionally NOT applied. Cells just beyond ADM0 keep
    their friction values, so the downstream FMM eikonal solver has physical
    room to compute correct travel times for schools and population near the
    country border. The country attribution itself happens later, at the
    aggregation step (zonal stats by ADM2). Country-internal-only travel can
    be enforced at that stage if desired.
    """
    minx, miny, maxx, maxy = geom.bounds
    minx -= buffer_deg
    miny -= buffer_deg
    maxx += buffer_deg
    maxy += buffer_deg

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(src_path) as src:
        # Snap bounds to the global raster's pixel grid (avoid sub-pixel offsets).
        window = from_bounds(minx, miny, maxx, maxy, transform=src.transform)
        window = window.round_offsets(op="floor").round_lengths(op="ceil")
        # Clip to the global raster's extent (no LAC country approaches the
        # lat=-60 / lat=85 cut-offs, so this is a safety guard).
        window = window.intersection(
            rasterio.windows.Window(0, 0, src.width, src.height)
        )

        data = src.read(1, window=window)
        win_transform = src.window_transform(window)
        nodata = src.nodata

        profile = src.profile.copy()
        profile.update(
            height=int(data.shape[0]),
            width=int(data.shape[1]),
            transform=win_transform,
            compress="lzw",
            predictor=2,  # float32 friendly
            tiled=True,
            blockxsize=256,
            blockysize=256,
        )
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(data, 1)

    if nodata is not None:
        valid = data[(data != nodata) & np.isfinite(data)]
    else:
        valid = data[np.isfinite(data) & (data > 0)]
    return {
        "height": int(data.shape[0]),
        "width": int(data.shape[1]),
        "n_valid": int(valid.size),
        "n_total": int(data.size),
        "min": float(valid.min()) if valid.size else float("nan"),
        "max": float(valid.max()) if valid.size else float("nan"),
        "mean": float(valid.mean()) if valid.size else float("nan"),
        "out_size_mb": out_path.stat().st_size / 1e6,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--countries",
        nargs="+",
        default=["all"],
        help="ISO3 codes or 'all' (default).",
    )
    parser.add_argument(
        "--buffer",
        type=float,
        default=0.05,
        help="Buffer in degrees applied to ADM0 polygon. ~0.05 deg = ~5 km at equator.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-clip even when the output GeoTIFF already exists.",
    )
    args = parser.parse_args()

    if args.countries == ["all"]:
        isos = list(PIPELINE_ISOS)
    else:
        unknown = [c for c in args.countries if c not in PIPELINE_ISOS]
        if unknown:
            print(f"ERROR: unknown ISOs (not in PIPELINE_ISOS): {unknown}", file=sys.stderr)
            return 2
        isos = list(args.countries)

    # Validate raster + polygon inputs upfront
    for kind, path in GLOBAL_RASTERS.items():
        if not path.exists():
            print(f"ERROR: missing global raster '{kind}': {path}", file=sys.stderr)
            return 2
    if not ADM0_SHP.exists():
        print(f"ERROR: missing ADM0 shapefile: {ADM0_SHP}", file=sys.stderr)
        return 2

    print(f"Loading ADM0 polygons from {ADM0_SHP.name}")
    gdf = gpd.read_file(ADM0_SHP)
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        print(f"ERROR: ADM0 shapefile must be EPSG:4326, got {gdf.crs}", file=sys.stderr)
        return 2
    gdf["_iso3"] = gdf["ADM0_PCODE"].map(ADM0_PCODE_TO_ISO3)

    missing = [iso for iso in isos if iso not in set(gdf["_iso3"])]
    if missing:
        print(f"ERROR: ISOs not present in ADM0 shapefile: {missing}", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR / "_manifest.csv"
    rows: list[dict] = []

    print(f"Clipping {len(GLOBAL_RASTERS)} rasters x {len(isos)} countries "
          f"(buffer={args.buffer} deg, force={args.force})")
    total_t0 = perf_counter()

    for iso in isos:
        geom = _load_country_geom(iso, gdf)
        country_dir = OUT_DIR / iso
        country_dir.mkdir(parents=True, exist_ok=True)
        for kind, src_path in GLOBAL_RASTERS.items():
            out_path = country_dir / f"{iso}_{kind}.tif"
            if out_path.exists() and not args.force:
                size_mb = out_path.stat().st_size / 1e6
                print(f"  [skip] {iso} {kind:20s} -> {out_path.name} ({size_mb:.1f} MB, already exists)",
                      flush=True)
                rows.append({"iso": iso, "kind": kind, "status": "skipped",
                             "out_size_mb": size_mb, "elapsed_s": 0.0})
                continue
            t0 = perf_counter()
            try:
                stats = _clip_one(src_path, geom, args.buffer, out_path)
            except Exception as exc:
                print(f"  [FAIL] {iso} {kind}: {exc!r}", file=sys.stderr, flush=True)
                rows.append({"iso": iso, "kind": kind, "status": "error",
                             "error": repr(exc), "elapsed_s": perf_counter() - t0})
                continue
            elapsed = perf_counter() - t0
            print(f"  [ok]   {iso} {kind:20s} -> {out_path.name} "
                  f"({stats['out_size_mb']:.1f} MB, "
                  f"{stats['width']}x{stats['height']}, "
                  f"valid={stats['n_valid']:,}/{stats['n_total']:,}, "
                  f"mean={stats['mean']:.4g} min/m, "
                  f"{elapsed:.1f}s)", flush=True)
            rows.append({"iso": iso, "kind": kind, "status": "written",
                         "elapsed_s": elapsed, **stats})

    # Write manifest
    if rows:
        cols = sorted({k for r in rows for k in r.keys()})
        with manifest_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nManifest -> {manifest_path.relative_to(ROOT)}")

    total_elapsed = perf_counter() - total_t0
    n_ok = sum(1 for r in rows if r.get("status") == "written")
    n_skip = sum(1 for r in rows if r.get("status") == "skipped")
    n_err = sum(1 for r in rows if r.get("status") == "error")
    print(f"\nDone in {total_elapsed:.1f}s "
          f"(written={n_ok}, skipped={n_skip}, error={n_err})")
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

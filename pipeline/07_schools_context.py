"""Build the derived schools-context layer.

Step 07 lives downstream of step-05 (`{ISO}_schools_clean.csv`, 14 cols incl.
id_edificio) and step-06 (`population_grid_{ISO}.csv`). CIMA enriched files
stay untouched; enrichment lives only in this derived layer.

Substeps (run in sequence):

    --step join       Spatial join clean ↔ population_grid_{ISO}.csv at 1 km cell
    --step lac        Concatenate the 21 schools_with_context, K-12 filter, write LAC base
    --step lac-clean  Concatenate the 21 schools_clean (no enrichment), K-12 filter

The `--step clean` substep was removed when step-05 took ownership of the
canonical school base. Run `pipeline/05_base_k_12_clean.py` instead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd

from _paths import REPO_ROOT as ROOT, DATA_ROOT  # noqa: E402
sys.path.insert(0, str(ROOT))

from pipeline.constants import ANALYSIS_ISOS  # noqa: E402

CIMA_DIR = DATA_ROOT / "schools" / "AR"
GRID_DIR = DATA_ROOT / "population" / "WorldPop" / "processed"

# WorldPop grid cell pitch in degrees (1/120 ≈ 0.00833°, ~1 km at the equator).
GRID_PITCH = 1.0 / 120.0

# Columns inherited from population_grid_{ISO}.csv at join time.
GRID_COLS_INHERITED: tuple[str, ...] = (
    "cell_id", "area_class",
    "rwi", "rwi_error", "rwi_dist_km",
    "poverty_rate_adm2", "nbi_rate_adm2",
)

# Pulled from CIMA enriched into the with_context output (not in clean).
# Critical so downstream consumers can filter spatial-indicator inclusion
# without re-reading the 47-col CIMA.
CIMA_EXTRA_COLS: tuple[str, ...] = ("include_in_spatial_indicators",)

# Canonical schools-clean schema (14 columns on disk, owned by step-05).
# id_edificio comes from BID's LAC_merged.csv via the cascade resolved in step-05;
# rows with no BID match get a synthetic `{ISO}_SYN_{N:05d}`.
CLEAN_COLUMNS: tuple[str, ...] = (
    "adm0_pcode", "adm1_pcode", "adm2_pcode",
    "id_centro", "id_edificio", "sector",
    "nivel_primaria", "nivel_secbaja", "nivel_secalta",
    "latitud", "longitud",
    "coordinate_source", "coordinate_quality", "qc_scope_class",
)


def _cima_path(iso: str) -> Path:
    return CIMA_DIR / iso / "processed" / f"{iso}_total_cima.csv"


def _clean_path(iso: str) -> Path:
    return CIMA_DIR / iso / "processed" / f"{iso}_schools_clean.csv"


def _context_path(iso: str) -> Path:
    return CIMA_DIR / iso / "processed" / f"{iso}_schools_with_context.csv"


def _grid_path(iso: str) -> Path:
    return GRID_DIR / f"population_grid_{iso}.csv"


def _lac_path() -> Path:
    return CIMA_DIR / "LAC_schools_k12_with_context.csv"


def _lac_clean_path() -> Path:
    return CIMA_DIR / "LAC_schools_k12_clean.csv"


def _index_lattice(lat_series: pd.Series, lon_series: pd.Series,
                   ref_lat: float, ref_lon: float) -> tuple[pd.Series, pd.Series]:
    """Encode (lat, lon) as integer (lat_idx, lon_idx) on the WorldPop lattice.

    Float-point safe: uses round() on the index, so two cells whose stored
    centers differ by sub-microdegree noise still collide on the same key.
    NaN coords become NaN indices (they fail any merge naturally).
    """
    lat_idx = ((lat_series - ref_lat) / GRID_PITCH).round()
    lon_idx = ((lon_series - ref_lon) / GRID_PITCH).round()
    return lat_idx.astype("Int64"), lon_idx.astype("Int64")


def _join_one(iso: str) -> dict:
    clean_path = _clean_path(iso)
    if not clean_path.exists():
        raise FileNotFoundError(
            f"{iso}: missing schools_clean at {clean_path}. "
            f"Run pipeline/05_base_k_12_clean.py first."
        )
    grid_path = _grid_path(iso)
    if not grid_path.exists():
        raise FileNotFoundError(
            f"{iso}: missing population_grid at {grid_path}. "
            f"Country either lacks a WorldPop grid (HTI) or step-06 wasn't run."
        )

    schools = pd.read_csv(clean_path, dtype={"id_centro": str})
    cima = pd.read_csv(_cima_path(iso), dtype={"id_centro": str},
                       usecols=["id_centro", *CIMA_EXTRA_COLS])
    schools = schools.merge(cima, on="id_centro", how="left", validate="one_to_one")

    grid = pd.read_csv(grid_path)
    if grid.empty:
        raise ValueError(f"{iso}: population grid is empty at {grid_path}")

    # Anchor the lattice on the first grid cell. Any cell works as long as it lives on the lattice.
    ref_lat = float(grid["lat"].iloc[0])
    ref_lon = float(grid["lon"].iloc[0])

    grid["_lat_idx"], grid["_lon_idx"] = _index_lattice(grid["lat"], grid["lon"], ref_lat, ref_lon)
    schools["_lat_idx"], schools["_lon_idx"] = _index_lattice(
        schools["latitud"], schools["longitud"], ref_lat, ref_lon,
    )

    grid_keep = grid[["_lat_idx", "_lon_idx", *GRID_COLS_INHERITED]]
    joined = schools.merge(grid_keep, on=["_lat_idx", "_lon_idx"], how="left")
    joined = joined.drop(columns=["_lat_idx", "_lon_idx"])

    # Enforce stable column order: clean schema → CIMA extras → grid inherited.
    ordered = [*CLEAN_COLUMNS, *CIMA_EXTRA_COLS, *GRID_COLS_INHERITED]
    joined = joined.loc[:, ordered]

    dest = _context_path(iso)
    dest.parent.mkdir(parents=True, exist_ok=True)
    joined.to_csv(dest, index=False)

    georef_mask = joined["latitud"].notna() & joined["longitud"].notna()
    matched_mask = joined["cell_id"].notna()
    spatial_in = joined["include_in_spatial_indicators"].fillna(False).astype(bool)
    return {
        "iso": iso,
        "n_total": int(len(joined)),
        "n_georef": int(georef_mask.sum()),
        "n_cell_matched": int(matched_mask.sum()),
        "n_cell_unmatched_georef": int((georef_mask & ~matched_mask).sum()),
        "pct_matched_of_georef": (
            round(100.0 * matched_mask.sum() / max(1, georef_mask.sum()), 1)
        ),
        "n_spatial_in": int(spatial_in.sum()),
        "n_spatial_in_matched": int((spatial_in & matched_mask).sum()),
    }


def step_join(isos: Iterable[str]) -> None:
    rows = [_join_one(iso) for iso in isos]
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print()
    print(
        f"Wrote {len(df)} schools_with_context files. "
        f"Cell-matched: {df['n_cell_matched'].sum():,} / "
        f"{df['n_georef'].sum():,} georef "
        f"({100.0 * df['n_cell_matched'].sum() / max(1, df['n_georef'].sum()):.1f}%)."
    )


def step_lac(isos: Iterable[str]) -> None:
    frames = []
    for iso in isos:
        path = _context_path(iso)
        if not path.exists():
            raise FileNotFoundError(
                f"{iso}: missing schools_with_context at {path}. Run --step join first."
            )
        frames.append(pd.read_csv(path, dtype={"id_centro": str}))
    full = pd.concat(frames, ignore_index=True)

    levels = ["nivel_primaria", "nivel_secbaja", "nivel_secalta"]
    is_k12 = full[levels].fillna(0).astype(int).sum(axis=1) > 0
    k12 = full.loc[is_k12].copy()

    dest = _lac_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    k12.to_csv(dest, index=False)

    summary = (
        k12.assign(n=1)
        .groupby("adm0_pcode", as_index=False)
        .agg(
            n_k12=("n", "sum"),
            n_georef=("latitud", lambda s: int(s.notna().sum())),
            n_cell_matched=("cell_id", lambda s: int(s.notna().sum())),
        )
    )
    print(summary.to_string(index=False))
    print()
    print(
        f"Wrote LAC k-12 base: {len(k12):,} / {len(full):,} schools "
        f"({100.0 * len(k12) / max(1, len(full)):.1f}%) "
        f"({len(frames)} countries) -> {dest.relative_to(ROOT)}"
    )


def step_lac_clean(isos: Iterable[str]) -> None:
    frames = []
    for iso in isos:
        path = _clean_path(iso)
        if not path.exists():
            raise FileNotFoundError(
                f"{iso}: missing schools_clean at {path}. "
                f"Run pipeline/05_base_k_12_clean.py first."
            )
        frames.append(pd.read_csv(path, dtype={"id_centro": str}))
    full = pd.concat(frames, ignore_index=True)

    levels = ["nivel_primaria", "nivel_secbaja", "nivel_secalta"]
    is_k12 = full[levels].fillna(0).astype(int).sum(axis=1) > 0
    k12 = full.loc[is_k12].copy()

    dest = _lac_clean_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    k12.to_csv(dest, index=False)

    summary = (
        k12.assign(n=1)
        .groupby("adm0_pcode", as_index=False)
        .agg(n_k12=("n", "sum"), n_georef=("latitud", lambda s: int(s.notna().sum())))
    )
    print(summary.to_string(index=False))
    print()
    print(
        f"Wrote LAC k-12 clean (no context): {len(k12):,} schools "
        f"({len(frames)} countries) -> {dest.relative_to(ROOT)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--step", choices=("join", "lac", "lac-clean"), required=True,
    )
    parser.add_argument(
        "--countries", nargs="+", default=ANALYSIS_ISOS,
        help="Subset of ISOs (default: 21 ANALYSIS_ISOS).",
    )
    args = parser.parse_args()

    if args.step == "join":
        step_join(args.countries)
        return
    if args.step == "lac":
        step_lac(args.countries)
        return
    if args.step == "lac-clean":
        step_lac_clean(args.countries)
        return

    raise NotImplementedError(f"--step {args.step!r}")


if __name__ == "__main__":
    main()

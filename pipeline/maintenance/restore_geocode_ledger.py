"""Restore audit ledger entries for "ghost geocode" rows.

A ghost geocode is a CIMA row with `coordinate_source='geocoded'` that has
no matching ACCEPT row in `results/geocode_results.csv`. Step-04 wrote the
coords from a successful geocoder fill in some prior run, but a later
re-run on the same country (with the pre-fix save logic that replaced ALL
rows by ISO) destroyed the audit row. The coordinate stayed in CIMA, the
ledger lost its provenance.

This script reconstructs the ledger row using the metadata that survives
in CIMA — geocoded_lat/lon, geocode_source, geocode_precision, arcgis_score,
acceptance, and the validation_audit columns. The original query string and
geocode_distance_km are not preserved on disk, so they are written as
empty / NaN respectively. Every other field comes from the CIMA snapshot.

This is a one-shot migration; once run, the ghost-geocode regression test
(`tests/test_no_ghost_geocodes.py::test_no_ghost_geocodes_in_live_data`)
will pass. The save-logic fix in `04_geocode_missing.py` ensures no new
ghosts are created on future runs, so this script should not need to run
again.

Usage (from project root):
    uv run python pipeline/maintenance/restore_geocode_ledger.py --dry-run
    uv run python pipeline/maintenance/restore_geocode_ledger.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_CSV = PROJECT_ROOT / "results" / "geocode_results.csv"
CIMA_ROOT = PROJECT_ROOT / "data" / "schools" / "AR"

LEDGER_COLUMNS = [
    "iso", "id_centro", "nombre_centro",
    "geocoded_lat", "geocoded_lon",
    "geocode_source", "geocode_precision", "arcgis_score",
    "geocode_query", "geocode_distance_km",
    "target_type",
    "original_lat", "original_lon",
    "geocoded_in_adm1", "geocoded_in_adm2",
    "geo_adm1_check", "geo_adm2_check",
    "original_in_adm1", "original_in_adm2",
    "orig_adm1_check", "orig_adm2_check",
    "acceptance", "adm1_check", "adm2_check",
]


def find_orphans(ledger: pd.DataFrame) -> dict[str, list[str]]:
    """Return {iso: [id_centro, ...]} for CIMA rows missing from the ledger."""
    accept_keys = set(
        zip(
            ledger.loc[
                (ledger["target_type"] == "fill")
                & (ledger["acceptance"].isin(
                    ["ACCEPT", "ACCEPT_CENTROID", "ACCEPT_WITH_FLAG"]
                )),
                "iso",
            ],
            ledger.loc[
                (ledger["target_type"] == "fill")
                & (ledger["acceptance"].isin(
                    ["ACCEPT", "ACCEPT_CENTROID", "ACCEPT_WITH_FLAG"]
                )),
                "id_centro",
            ],
        )
    )

    orphans: dict[str, list[str]] = {}
    for cima_path in CIMA_ROOT.glob("*/processed/*_total_cima.csv"):
        iso = cima_path.parent.parent.name
        df = pd.read_csv(cima_path, dtype={"id_centro": str}, low_memory=False)
        if "coordinate_source" not in df.columns:
            continue
        cima_geocoded = df[df["coordinate_source"].fillna("") == "geocoded"]
        for sid in cima_geocoded["id_centro"]:
            if (iso, sid) not in accept_keys:
                orphans.setdefault(iso, []).append(sid)
    return orphans


def reconstruct_row(iso: str, id_centro: str, cima_row: pd.Series) -> dict:
    """Build a ledger row from CIMA fields. Missing fields → empty / NaN."""
    out = {col: "" for col in LEDGER_COLUMNS}
    out["iso"] = iso
    out["id_centro"] = str(id_centro)
    out["nombre_centro"] = cima_row.get("nombre_centro", "")
    out["geocoded_lat"] = cima_row.get("latitud_geocoded", cima_row.get("latitud"))
    out["geocoded_lon"] = cima_row.get("longitud_geocoded", cima_row.get("longitud"))
    out["geocode_source"] = cima_row.get("geocode_source", "") or ""
    out["geocode_precision"] = cima_row.get("geocode_precision", "") or ""
    out["arcgis_score"] = cima_row.get("arcgis_score", np.nan)
    out["geocode_query"] = ""  # not preserved on disk
    out["geocode_distance_km"] = cima_row.get("geocode_distance_km", np.nan)
    out["target_type"] = "fill"
    out["original_lat"] = np.nan  # fills had no original GPS
    out["original_lon"] = np.nan
    for col in ("geocoded_in_adm1", "geocoded_in_adm2",
                "geo_adm1_check", "geo_adm2_check",
                "original_in_adm1", "original_in_adm2",
                "orig_adm1_check", "orig_adm2_check"):
        out[col] = cima_row.get(col, "") or ""
    out["acceptance"] = cima_row.get("acceptance", "") or ""
    out["adm1_check"] = cima_row.get("adm1_check", "") or ""
    out["adm2_check"] = cima_row.get("adm2_check", "") or ""
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be restored without writing.",
    )
    args = parser.parse_args()

    if not RESULTS_CSV.exists():
        print(f"ERROR: {RESULTS_CSV} not found", file=sys.stderr)
        return 1

    print(f"Reading ledger: {RESULTS_CSV}")
    ledger = pd.read_csv(
        RESULTS_CSV, dtype={"id_centro": str}, low_memory=False
    )
    print(f"  Current ledger size: {len(ledger):,} rows")

    print("Scanning CIMAs for ghost geocodes...")
    orphans = find_orphans(ledger)
    if not orphans:
        print("  No ghost geocodes found. Ledger and CIMAs are in sync.")
        return 0

    total = sum(len(ids) for ids in orphans.values())
    summary = ", ".join(f"{iso}={len(ids)}" for iso, ids in orphans.items())
    print(f"  Found {total} ghost geocodes: {summary}")

    new_rows = []
    for iso, ids in orphans.items():
        cima_path = CIMA_ROOT / iso / "processed" / f"{iso}_total_cima.csv"
        cima = pd.read_csv(cima_path, dtype={"id_centro": str}, low_memory=False)
        cima_idx = cima.set_index("id_centro")
        for sid in ids:
            if sid not in cima_idx.index:
                continue
            row = cima_idx.loc[sid]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]  # duplicates: take first
            new_rows.append(reconstruct_row(iso, sid, row))

    if not new_rows:
        print("  No reconstructable rows produced.")
        return 0

    new_df = pd.DataFrame(new_rows, columns=LEDGER_COLUMNS)
    print(f"  Reconstructed {len(new_df):,} rows.")
    print(f"    By acceptance: {new_df['acceptance'].value_counts().to_dict()}")
    print(f"    By precision:  {new_df['geocode_precision'].value_counts().to_dict()}")
    print(f"    By source:     {new_df['geocode_source'].value_counts().to_dict()}")

    if args.dry_run:
        print("\n[dry-run] Would append these rows to the ledger.")
        print(f"  New ledger size would be: {len(ledger) + len(new_df):,}")
        return 0

    # Align columns: ledger may have extra columns (or fewer) than the
    # reconstructed rows. Bring both to the union of columns.
    for col in new_df.columns:
        if col not in ledger.columns:
            ledger[col] = pd.NA
    for col in ledger.columns:
        if col not in new_df.columns:
            new_df[col] = pd.NA

    combined = pd.concat(
        [ledger[ledger.columns.tolist()], new_df[ledger.columns.tolist()]],
        ignore_index=True,
    )
    combined = combined.drop_duplicates(
        subset=["iso", "id_centro"], keep="last"
    )
    combined.to_csv(RESULTS_CSV, index=False, encoding="utf-8")
    print(f"\nLedger restored: {len(combined):,} rows -> {RESULTS_CSV}")
    print(f"  Net added: {len(combined) - len(ledger):,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

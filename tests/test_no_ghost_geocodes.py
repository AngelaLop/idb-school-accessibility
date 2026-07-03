"""Regression tests for the ghost geocode bug.

Defect (caught 2026-05-04): step-04 wrote `coordinate_source='geocoded'`
to CIMA when a fill was accepted, but a subsequent re-run on the same
country could:
  (a) leave the prior accept in CIMA without re-attempting the school
      (school already has latitud, so it falls out of fill_targets), AND
  (b) wipe the prior row from `geocode_results.csv` because the save logic
      did "delete all rows for processed ISOs, then write new ones".

Result: CIMA row says "this came from the geocoder" but the audit ledger
has nothing to back it up. 451 ARG + 83 PRY orphans were found in the live
data before the fix.

The fix has two parts:
  1. Save logic merges by (iso, id_centro), keep latest. No row is destroyed.
  2. Fill rejection actively clears CIMA when a previously-accepted row is
     now rejected — "left as NaN" must be true on disk, not just in print.

These tests lock the invariants both fixes guarantee.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def step04_module():
    """Load 04_geocode_missing.py as a module so we can reach private helpers."""
    sys.path.insert(0, str(PROJECT_ROOT / "pipeline"))
    spec = importlib.util.spec_from_file_location(
        "step04", PROJECT_ROOT / "pipeline" / "04_geocode_missing.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Pieza 1 — merge-by-id_centro in the save logic
# ---------------------------------------------------------------------------


def _save_results(combined: pd.DataFrame, results_path: Path) -> pd.DataFrame:
    """Replicates the save logic from main() inline so we can unit-test it.

    Mirrors `pipeline/04_geocode_missing.py` lines around 1318-1340. Any
    change to the saving strategy must update both this fixture AND the
    production code.
    """
    if results_path.exists():
        existing = pd.read_csv(
            results_path, dtype={"id_centro": str}, low_memory=False
        )
        missing_cols = [c for c in combined.columns if c not in existing.columns]
        for col in missing_cols:
            existing[col] = pd.NA
        combined = pd.concat(
            [existing[combined.columns], combined],
            ignore_index=True,
        )
        combined = combined.drop_duplicates(
            subset=["iso", "id_centro"], keep="last"
        )
    combined.to_csv(results_path, index=False, encoding="utf-8")
    return combined


def test_save_preserves_prior_accepts_for_unprocessed_schools(tmp_path):
    """A school accepted in run 1 must NOT disappear from CSV when run 2
    re-processes the same ISO but doesn't include that id_centro."""
    results_path = tmp_path / "geocode_results.csv"

    # Run 1: ARG with two schools, both accepted as fills
    run1 = pd.DataFrame([
        {"iso": "ARG", "id_centro": "100", "target_type": "fill",
         "acceptance": "ACCEPT", "geocoded_lat": -34.6, "geocoded_lon": -58.3,
         "arcgis_score": 97.0},
        {"iso": "ARG", "id_centro": "200", "target_type": "fill",
         "acceptance": "ACCEPT", "geocoded_lat": -34.7, "geocoded_lon": -58.4,
         "arcgis_score": 96.5},
    ])
    _save_results(run1, results_path)

    # Run 2: ARG re-runs but only school 300 is in fill_targets now
    # (schools 100 and 200 already have coords from run 1, so they are not
    # re-attempted by identify_targets).
    run2 = pd.DataFrame([
        {"iso": "ARG", "id_centro": "300", "target_type": "fill",
         "acceptance": "REJECT", "geocoded_lat": -34.8, "geocoded_lon": -58.5,
         "arcgis_score": 75.0},
    ])
    final = _save_results(run2, results_path)

    # Schools 100 and 200 must still be in the ledger (defect would drop them)
    assert set(final["id_centro"]) == {"100", "200", "300"}
    accepted_ids = set(
        final.loc[final["acceptance"] == "ACCEPT", "id_centro"]
    )
    assert accepted_ids == {"100", "200"}, (
        f"Prior ACCEPT rows for ARG/100 and ARG/200 must survive a re-run "
        f"that doesn't include them. Found: {accepted_ids}"
    )


def test_save_overwrites_when_same_id_reattempted(tmp_path):
    """If the same (iso, id_centro) appears in run 2 with a new outcome,
    the new outcome wins (the old row is superseded, not duplicated)."""
    results_path = tmp_path / "geocode_results.csv"

    run1 = pd.DataFrame([
        {"iso": "ARG", "id_centro": "100", "target_type": "fill",
         "acceptance": "ACCEPT", "arcgis_score": 97.0},
    ])
    _save_results(run1, results_path)

    run2 = pd.DataFrame([
        {"iso": "ARG", "id_centro": "100", "target_type": "fill",
         "acceptance": "REJECT", "arcgis_score": 75.0},
    ])
    final = _save_results(run2, results_path)

    arg_100 = final[(final["iso"] == "ARG") & (final["id_centro"] == "100")]
    assert len(arg_100) == 1, "(iso, id_centro) must be unique"
    assert arg_100.iloc[0]["acceptance"] == "REJECT", "newest run wins"


def test_save_does_not_touch_other_isos(tmp_path):
    """Re-running ARG must leave PRY rows untouched (no cross-country
    side-effects from the merge)."""
    results_path = tmp_path / "geocode_results.csv"

    run1 = pd.DataFrame([
        {"iso": "ARG", "id_centro": "100", "acceptance": "ACCEPT"},
        {"iso": "PRY", "id_centro": "200", "acceptance": "ACCEPT"},
    ])
    _save_results(run1, results_path)

    run2_arg_only = pd.DataFrame([
        {"iso": "ARG", "id_centro": "300", "acceptance": "REJECT"},
    ])
    final = _save_results(run2_arg_only, results_path)

    pry_rows = final[final["iso"] == "PRY"]
    assert len(pry_rows) == 1
    assert pry_rows.iloc[0]["id_centro"] == "200"
    assert pry_rows.iloc[0]["acceptance"] == "ACCEPT"


# ---------------------------------------------------------------------------
# Pieza 2 — fill rejection clears CIMA
# ---------------------------------------------------------------------------


def _apply_fill_rejection_clear(cima: pd.DataFrame, fill_rejected_ids: set,
                                  validation_audit_cols: list) -> tuple[pd.DataFrame, int]:
    """Replicates the clearing block from process_country()."""
    if "coordinate_source" not in cima.columns:
        return cima, 0
    rej_mask = (
        cima["id_centro"].astype(str).isin({str(s) for s in fill_rejected_ids})
        & (cima["coordinate_source"] == "geocoded")
    )
    if not rej_mask.any():
        return cima, 0
    n_cleared = int(rej_mask.sum())
    cima = cima.copy()
    cima.loc[rej_mask, ["latitud", "longitud",
                        "latitud_geocoded", "longitud_geocoded",
                        "geocode_distance_km", "arcgis_score"]] = np.nan
    for col in ("coordinate_source", "geocode_source",
                "geocode_precision", "acceptance"):
        if col in cima.columns:
            cima.loc[rej_mask, col] = ""
    for col in validation_audit_cols:
        if col in cima.columns:
            cima.loc[rej_mask, col] = ""
    return cima, n_cleared


def test_rejection_clears_previously_accepted_fill():
    """A school previously accepted as fill, now rejected, must have its
    geocoded coords wiped from CIMA — not silently retained."""
    cima = pd.DataFrame([
        {"id_centro": "100", "latitud": -34.6, "longitud": -58.3,
         "latitud_geocoded": -34.6, "longitud_geocoded": -58.3,
         "coordinate_source": "geocoded", "geocode_source": "arcgis",
         "geocode_precision": "street", "acceptance": "ACCEPT",
         "arcgis_score": 97.0},
    ])
    rejected_ids = {"100"}
    cleared, n = _apply_fill_rejection_clear(cima, rejected_ids, [])

    assert n == 1
    row = cleared.iloc[0]
    assert pd.isna(row["latitud"])
    assert pd.isna(row["longitud"])
    assert pd.isna(row["latitud_geocoded"])
    assert pd.isna(row["longitud_geocoded"])
    assert row["coordinate_source"] == ""
    assert row["acceptance"] == ""


def test_rejection_does_not_touch_original_gps():
    """A compare-target school with original GPS (not geocoded) must NOT
    be cleared even if its id_centro lands in the rejected set."""
    cima = pd.DataFrame([
        {"id_centro": "200", "latitud": -34.5, "longitud": -58.2,
         "latitud_geocoded": np.nan, "longitud_geocoded": np.nan,
         "coordinate_source": "original", "geocode_source": "",
         "geocode_precision": "", "acceptance": "FLAG",
         "arcgis_score": np.nan},
    ])
    rejected_ids = {"200"}  # in the set, but coordinate_source != 'geocoded'
    cleared, n = _apply_fill_rejection_clear(cima, rejected_ids, [])

    assert n == 0
    row = cleared.iloc[0]
    assert row["latitud"] == -34.5, "original GPS preserved"
    assert row["coordinate_source"] == "original"


def test_rejection_does_not_touch_unrelated_geocoded_schools():
    """If id_centro 100 is rejected, 200 (also coordinate_source='geocoded')
    must remain intact."""
    cima = pd.DataFrame([
        {"id_centro": "100", "latitud": -34.6, "longitud": -58.3,
         "latitud_geocoded": -34.6, "longitud_geocoded": -58.3,
         "coordinate_source": "geocoded", "geocode_source": "arcgis",
         "geocode_precision": "street", "acceptance": "ACCEPT",
         "arcgis_score": 97.0},
        {"id_centro": "200", "latitud": -34.7, "longitud": -58.4,
         "latitud_geocoded": -34.7, "longitud_geocoded": -58.4,
         "coordinate_source": "geocoded", "geocode_source": "arcgis",
         "geocode_precision": "street", "acceptance": "ACCEPT",
         "arcgis_score": 96.0},
    ])
    rejected_ids = {"100"}
    cleared, n = _apply_fill_rejection_clear(cima, rejected_ids, [])

    assert n == 1
    row_100 = cleared[cleared["id_centro"] == "100"].iloc[0]
    row_200 = cleared[cleared["id_centro"] == "200"].iloc[0]
    assert pd.isna(row_100["latitud"])
    assert row_200["latitud"] == -34.7
    assert row_200["coordinate_source"] == "geocoded"


# ---------------------------------------------------------------------------
# End-to-end invariant: every CIMA fill must have a CSV ledger entry
# ---------------------------------------------------------------------------


def test_no_ghost_geocodes_in_live_data():
    """For every (iso, id_centro) where CIMA shows coordinate_source='geocoded'
    and acceptance is ACCEPT/ACCEPT_CENTROID/ACCEPT_WITH_FLAG, there must be
    a corresponding row in geocode_results.csv documenting the geocoder
    response. No orphans allowed.

    Skipped if results/geocode_results.csv doesn't exist (e.g., fresh clone
    before any pipeline run).
    """
    results_csv = PROJECT_ROOT / "results" / "geocode_results.csv"
    if not results_csv.exists():
        pytest.skip("geocode_results.csv not present — skipping invariant check")

    ledger = pd.read_csv(
        results_csv, dtype={"id_centro": str}, low_memory=False
    )
    accept_ledger_keys = set(
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

    orphans_by_iso = {}
    cima_root = PROJECT_ROOT / "data" / "schools" / "AR"
    for cima_path in cima_root.glob("*/processed/*_total_cima.csv"):
        iso = cima_path.parent.parent.name
        df = pd.read_csv(
            cima_path, dtype={"id_centro": str}, low_memory=False
        )
        if "coordinate_source" not in df.columns:
            continue
        # geocoded fills written by Phase B-1 (cascade B-2 uses
        # coordinate_source='centroid_cascade' and lives outside the CSV
        # by design, so we filter to coordinate_source == 'geocoded' only).
        cima_geocoded = df[
            df["coordinate_source"].fillna("") == "geocoded"
        ]
        for _, row in cima_geocoded.iterrows():
            if (iso, row["id_centro"]) not in accept_ledger_keys:
                orphans_by_iso.setdefault(iso, []).append(row["id_centro"])

    if orphans_by_iso:
        summary = ", ".join(
            f"{iso}={len(ids)}" for iso, ids in orphans_by_iso.items()
        )
        total = sum(len(ids) for ids in orphans_by_iso.values())
        pytest.fail(
            f"Found {total} ghost geocodes (CIMA coordinate_source='geocoded' "
            f"without matching ACCEPT row in geocode_results.csv): {summary}. "
            f"This means a re-run of step-04 destroyed prior accept evidence."
        )

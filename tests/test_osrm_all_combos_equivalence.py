"""
Equivalence test: the new `run_mode_all_combos` (one OSRM search per cell per
mode) must produce output BIT-IDENTICAL to the per-combo `run` (what the ARG
notebook used). We can't run a live OSRM server here, but OSRM is deterministic
per (origin, destination) pair, so we drive BOTH code paths with the same
deterministic mock `osrm_table`. If they match under the mock, they match under
real OSRM.

Uses real PAN inputs (subset of the WorldPop grid + PAN public/private schools)
so the school geometry and K-nearest selection are realistic.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]

GRID_CSV = ROOT / "data/population/WorldPop/processed/population_grid_PAN.csv"
SCHOOLS_CSV = ROOT / "data/schools/AR/LAC_schools_k12_with_context.csv"


def _load_mod():
    path = ROOT / "pipeline" / "09b_travel_time_osrm.py"
    sys.path.insert(0, str(ROOT / "pipeline"))  # for `from _paths import ...`
    spec = importlib.util.spec_from_file_location("osrm09b", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hav_seconds(lat1, lon1, lat2, lon2):
    """Great-circle metres → seconds at 5 km/h (1.3889 m/s). Deterministic."""
    R = 6_371_000.0
    p = np.pi / 180
    a = (np.sin((lat2 - lat1) * p / 2) ** 2
         + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2)
    return (2 * R * np.arcsin(np.sqrt(a))) / 1.3889


def test_all_combos_matches_per_combo(tmp_path, monkeypatch):
    if not GRID_CSV.exists() or not SCHOOLS_CSV.exists():
        pytest.skip("PAN grid / LAC schools not present (data bundle not mounted)")
    mod = _load_mod()

    # --- real PAN inputs, grid subset for speed ------------------------------
    grid = pd.read_csv(GRID_CSV).head(300)
    wp = tmp_path / "wp"
    wp.mkdir()
    grid.to_csv(wp / "population_grid_PAN.csv", index=False)

    sch = pd.read_csv(SCHOOLS_CSV, low_memory=False)
    sch = sch[sch["adm0_pcode"] == "PAN"]
    spath = tmp_path / "schools_PAN.csv"
    sch.to_csv(spath, index=False)

    monkeypatch.setattr(mod, "WORLDPOP_DIR", wp)
    monkeypatch.setattr(mod, "SCHOOLS_PATH", spath)

    # --- deterministic mock OSRM: haversine seconds, unreachable beyond 30 min
    def mock_table(profile, sources_xy, dests_xy):
        src = np.asarray(sources_xy, dtype=float)   # (S, 2) as (lon, lat)
        dst = np.asarray(dests_xy, dtype=float)      # (D, 2)
        out = np.empty((len(src), len(dst)), dtype=np.float64)
        for si in range(len(src)):
            sec = _hav_seconds(src[si, 1], src[si, 0], dst[:, 1], dst[:, 0])
            out[si] = np.where(sec <= 30 * 60, sec, np.nan)  # >30 min = unreachable
        return out

    monkeypatch.setattr(mod, "osrm_table", mock_table)

    combos = [(s, l) for s in ("public", "private")
              for l in ("primaria", "secbaja", "secalta")]

    old = tmp_path / "old"; old.mkdir()
    new = tmp_path / "new"; new.mkdir()

    # per-combo (the ARG-notebook path)
    monkeypatch.setattr(mod, "OUT_DIR", old)
    for sector, level in combos:
        mod.run("PAN", "walking", level, sector, k=50, max_workers=4, overwrite=True)

    # all-combos (the new path)
    monkeypatch.setattr(mod, "OUT_DIR", new)
    mod.run_mode_all_combos("PAN", "walking", k=50, max_workers=4, overwrite=True)

    # every combo's parquet must be identical
    for sector, level in combos:
        name = f"PAN_walking_{level}_{sector}_osrm.parquet"
        fo, fn = old / name, new / name
        assert fo.exists(), f"per-combo missing {name}"
        assert fn.exists(), f"all-combos missing {name}"
        a = pd.read_parquet(fo)
        b = pd.read_parquet(fn)
        pd.testing.assert_frame_equal(a, b, check_like=False)

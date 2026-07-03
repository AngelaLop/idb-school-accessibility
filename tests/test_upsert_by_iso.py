"""Regression tests for the `upsert_by_iso` helper added in step 02.

Locks the fix for the side-report overwrite bug: previously, running
`02_qc_coordinates.py --countries XXX` would silently drop rows for every
country not in the batch. Upsert must preserve them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture(scope="module")
def upsert():
    spec = importlib.util.spec_from_file_location(
        "qc02", Path(__file__).resolve().parent.parent / "pipeline" / "02_qc_coordinates.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
    spec.loader.exec_module(mod)
    return mod.upsert_by_iso


def test_creates_file_when_missing(tmp_path, upsert):
    out = tmp_path / "report.csv"
    new = pd.DataFrame([{"iso": "PAN", "n": 3615}])
    result = upsert(new, out)
    assert out.exists()
    assert len(result) == 1
    assert result.iloc[0]["iso"] == "PAN"


def test_preserves_other_isos(tmp_path, upsert):
    out = tmp_path / "report.csv"
    pd.DataFrame([
        {"iso": "BRA", "n": 129976},
        {"iso": "MEX", "n": 152860},
        {"iso": "PAN", "n": 3500},  # stale value
    ]).to_csv(out, index=False)

    new = pd.DataFrame([{"iso": "PAN", "n": 3615}])
    result = upsert(new, out)

    by_iso = result.set_index("iso")["n"].to_dict()
    assert by_iso == {"BRA": 129976, "MEX": 152860, "PAN": 3615}


def test_replaces_all_rows_for_incoming_isos(tmp_path, upsert):
    out = tmp_path / "report.csv"
    pd.DataFrame([
        {"iso": "PAN", "id_centro": "1", "qc_status": "MATCH"},
        {"iso": "PAN", "id_centro": "2", "qc_status": "MATCH"},
        {"iso": "PAN", "id_centro": "3", "qc_status": "MATCH"},
        {"iso": "BRA", "id_centro": "x", "qc_status": "MATCH"},
    ]).to_csv(out, index=False)

    new = pd.DataFrame([
        {"iso": "PAN", "id_centro": "1", "qc_status": "MISMATCH"},
        {"iso": "PAN", "id_centro": "2", "qc_status": "MISMATCH"},
    ])
    result = upsert(new, out)

    pan = result[result["iso"] == "PAN"]
    bra = result[result["iso"] == "BRA"]
    assert len(pan) == 2  # not 5 — old PAN rows fully replaced
    assert (pan["qc_status"] == "MISMATCH").all()
    assert len(bra) == 1
    assert bra.iloc[0]["id_centro"] == "x"


def test_handles_new_columns_added_to_schema(tmp_path, upsert):
    out = tmp_path / "report.csv"
    pd.DataFrame([{"iso": "BRA", "n": 100}]).to_csv(out, index=False)

    # New batch carries an extra column (schema evolution).
    new = pd.DataFrame([{"iso": "PAN", "n": 50, "new_metric": 1.5}])
    result = upsert(new, out)

    assert set(result["iso"]) == {"BRA", "PAN"}
    assert "new_metric" in result.columns
    assert pd.isna(result.set_index("iso").loc["BRA", "new_metric"])
    assert result.set_index("iso").loc["PAN", "new_metric"] == 1.5

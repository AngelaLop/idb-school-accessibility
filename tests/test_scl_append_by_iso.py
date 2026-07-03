"""Regression tests for `write_scl_output` (step 10 / 10b append-by-country).

Locks the fix for the SCL overwrite bug: running
`10b_accessibility_aggregate_osrm.py --countries ARG` used to overwrite the
global CSV with only ARG, silently dropping every other country. The writer
must replace only the incoming countries' rows, preserve the rest, stay
idempotent across re-runs, and refuse to write a table with duplicate keys.

Sibling of `test_upsert_by_iso.py` (the same class of bug, fixed for step 02).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture(scope="module")
def write_scl_output():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "pipeline"))
    spec = importlib.util.spec_from_file_location(
        "step10_aggregate", root / "pipeline" / "10_accessibility_aggregate.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.write_scl_output


def _row(iso, *, area="Total", quintile="Total", value=50.0):
    """A minimal but schema-complete SCL row."""
    return {
        "isoalpha3": iso, "idgeo": "country",
        "admin1_pcode": "", "admin1_name": "",
        "admin2_pcode": "", "admin2_name": "",
        "indicator": "acceso_geografico", "mode": "walking",
        "education_level": "primaria", "age": "05_09",
        "sector": "Total", "area": area, "quintile": quintile,
        "time_band": "le15", "value": value, "population_base": 1000.0,
        "year": 2023, "method": "OSRM", "source": "test",
    }


def test_creates_file_when_missing(tmp_path, write_scl_output):
    out = tmp_path / "scl.csv"
    write_scl_output(pd.DataFrame([_row("PAN")]), out)
    assert out.exists()
    assert set(pd.read_csv(out)["isoalpha3"]) == {"PAN"}


def test_preserves_other_countries(tmp_path, write_scl_output):
    out = tmp_path / "scl.csv"
    write_scl_output(pd.DataFrame([_row("BOL"), _row("CHL")]), out)
    write_scl_output(pd.DataFrame([_row("PAN")]), out)  # add a new country
    assert set(pd.read_csv(out)["isoalpha3"]) == {"BOL", "CHL", "PAN"}


def test_replaces_only_incoming_country(tmp_path, write_scl_output):
    out = tmp_path / "scl.csv"
    write_scl_output(pd.DataFrame([_row("PAN", value=10.0),
                                   _row("BOL", value=20.0)]), out)
    # re-run PAN with a new value; BOL must be untouched
    write_scl_output(pd.DataFrame([_row("PAN", value=99.0)]), out)
    got = pd.read_csv(out).set_index("isoalpha3")
    assert got.loc["PAN", "value"] == 99.0
    assert got.loc["BOL", "value"] == 20.0


def test_idempotent_rerun(tmp_path, write_scl_output):
    out = tmp_path / "scl.csv"
    df = pd.DataFrame([_row("CRI", area="urban"), _row("CRI", area="rural")])
    for _ in range(3):  # the V2 validation: 3 consecutive runs, no growth
        write_scl_output(df, out)
    got = pd.read_csv(out)
    assert len(got) == 2          # not 6 — no duplication across re-runs
    assert (got["isoalpha3"] == "CRI").all()


def test_empty_input_leaves_file_untouched(tmp_path, write_scl_output):
    out = tmp_path / "scl.csv"
    write_scl_output(pd.DataFrame([_row("PAN")]), out)
    before = out.read_bytes()
    write_scl_output(pd.DataFrame(), out)   # produced nothing
    assert out.read_bytes() == before

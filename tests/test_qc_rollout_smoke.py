"""Smoke checks on real CIMAs finalized under the schema-v2 rollout.

These are intentionally narrow and high-signal. They verify the four
countries used for the real rollout smoke test:
    - BRA: large Phase B-1 country with heavy geocoder evidence
    - PAN: legacy centroid cascade migration
    - DOM: adm2 policy / no-street fallback edge case
    - JAM: spatial_only policy

The goal is to catch obvious rollout regressions on real files without
making the suite brittle to future reclassification improvements.
"""

from __future__ import annotations

import pandas as pd
import pytest

from conftest import _cima_path
from pipeline.constants import (
    COORDINATE_QUALITY_VALUES_V2,
    COORDINATE_SOURCES,
    QC_CENTROID_BIASES,
    QC_EVIDENCE_VERSION,
    QC_SCOPE_CLASS_VALUES,
)

SMOKE_ISOS = ("BRA", "PAN", "DOM", "JAM")
SMOKE_REQUIRED_COLUMNS = (
    "coordinate_source",
    "coordinate_quality",
    "coordinate_quality_reason",
    "qc_in_bounds",
    "qc_scope_class",
    "include_in_spatial_indicators",
    "qc_swapped",
    "adm1_pcode",
    "adm2_pcode",
    "qc_adm1_status",
    "qc_adm2_status",
    "qc_match_level",
    "qc_cluster_size_exact",
    "qc_cluster_diff_addr_exact",
    "qc_cluster_size_50m",
    "qc_cluster_diff_addr_50m",
    "qc_centroid_bias",
    "qc_evidence_version",
)


def _load(iso: str) -> pd.DataFrame:
    path = _cima_path(iso)
    if not path.exists():
        pytest.skip(f"{iso}: no CIMA file")
    return pd.read_csv(path, dtype={"id_centro": str})


def _has_coords(df: pd.DataFrame) -> pd.Series:
    lat = pd.to_numeric(df["latitud"], errors="coerce")
    lon = pd.to_numeric(df["longitud"], errors="coerce")
    return lat.notna() & lon.notna() & ~((lat == 0) | (lon == 0))


@pytest.mark.parametrize("smoke_iso", SMOKE_ISOS)
def test_finalize_rollout_columns_present(smoke_iso):
    df = _load(smoke_iso)
    missing = [col for col in SMOKE_REQUIRED_COLUMNS if col not in df.columns]
    assert not missing, f"{smoke_iso}: missing schema-v2 columns {missing}"


@pytest.mark.parametrize("smoke_iso", SMOKE_ISOS)
def test_finalize_rollout_qc_evidence_version_is_2(smoke_iso):
    df = _load(smoke_iso)
    assert "qc_evidence_version" in df.columns, f"{smoke_iso}: missing qc_evidence_version"
    versions = set(pd.to_numeric(df["qc_evidence_version"], errors="coerce").dropna().astype(int))
    assert versions == {QC_EVIDENCE_VERSION}, (
        f"{smoke_iso}: expected only version {QC_EVIDENCE_VERSION}, found {versions}"
    )


@pytest.mark.parametrize("smoke_iso", SMOKE_ISOS)
def test_finalize_rollout_quality_values_in_v2_enum(smoke_iso):
    df = _load(smoke_iso)
    assert "coordinate_quality" in df.columns, f"{smoke_iso}: missing coordinate_quality"
    values = set(df["coordinate_quality"].fillna("").astype(str).str.strip())
    invalid = values - set(COORDINATE_QUALITY_VALUES_V2)
    assert not invalid, f"{smoke_iso}: invalid v2 coordinate_quality values {sorted(invalid)}"


@pytest.mark.parametrize("smoke_iso", SMOKE_ISOS)
def test_finalize_rollout_scope_values_in_enum(smoke_iso):
    df = _load(smoke_iso)
    assert "qc_scope_class" in df.columns, f"{smoke_iso}: missing qc_scope_class"
    values = set(df["qc_scope_class"].fillna("").astype(str).str.strip())
    invalid = values - set(QC_SCOPE_CLASS_VALUES)
    assert not invalid, f"{smoke_iso}: invalid qc_scope_class values {sorted(invalid)}"


@pytest.mark.parametrize("smoke_iso", SMOKE_ISOS)
def test_finalize_rollout_no_blank_quality_for_georef_rows(smoke_iso):
    df = _load(smoke_iso)
    has_coords = _has_coords(df)
    blank_quality = (
        df["coordinate_quality"].isna()
        | (df["coordinate_quality"].astype(str).str.strip() == "")
    )
    offenders = df[has_coords & blank_quality]
    assert offenders.empty, (
        f"{smoke_iso}: {len(offenders)} georeferenced schools have blank coordinate_quality"
    )


@pytest.mark.parametrize("smoke_iso", SMOKE_ISOS)
def test_finalize_rollout_no_coords_implies_missing(smoke_iso):
    df = _load(smoke_iso)
    no_coords = ~_has_coords(df)
    offenders = df[no_coords & (df["coordinate_quality"] != "missing")]
    assert offenders.empty, (
        f"{smoke_iso}: {len(offenders)} schools without usable coordinates are not labeled 'missing'"
    )


@pytest.mark.parametrize("smoke_iso", SMOKE_ISOS)
def test_finalize_rollout_coordinate_source_in_v2_enum_for_georef_rows(smoke_iso):
    df = _load(smoke_iso)
    has_coords = _has_coords(df)
    values = df.loc[has_coords, "coordinate_source"].fillna("").astype(str).str.strip()
    invalid = sorted(set(values) - set(COORDINATE_SOURCES))
    assert not invalid, (
        f"{smoke_iso}: invalid coordinate_source values on georeferenced rows: {invalid}"
    )


def test_pan_centroid_cascade_migrated_on_real_cima():
    df = _load("PAN")
    cascade = df[df["coordinate_source"] == "centroid_cascade"]
    assert not cascade.empty, "PAN: expected centroid_cascade rows after finalize"
    assert set(cascade["qc_centroid_bias"]).issubset(QC_CENTROID_BIASES), (
        f"PAN: invalid qc_centroid_bias values {sorted(set(cascade['qc_centroid_bias']) - set(QC_CENTROID_BIASES))}"
    )
    allowed = {"geocoded_centroid", "adm_mismatch"}
    assert set(cascade["coordinate_quality"]).issubset(allowed), (
        "PAN: centroid cascade rows should resolve to geocoded_centroid unless a stronger adm_mismatch overrides it"
    )
    assert {"normal", "high"}.issubset(set(cascade["qc_centroid_bias"])), (
        "PAN: expected both normal and high centroid bias buckets after migration"
    )

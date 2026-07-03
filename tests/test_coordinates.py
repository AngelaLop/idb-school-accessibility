"""Test coordinate quality across CIMA files."""

import pandas as pd
import pytest
from pathlib import Path
from conftest import COUNTRY_BBOX, _cima_path, RESULTS_DIR

# Countries with known low georef (exempt from 80% threshold)
LOW_GEOREF_EXEMPT = {"HTI"}  # Haiti has 0% — no coord source available

QC_REPORT = RESULTS_DIR / "qc_coordinate_summary.csv"
QC_DUPES = RESULTS_DIR / "qc_duplicate_coordinates.csv"


def load(iso):
    return pd.read_csv(_cima_path(iso), dtype={"id_centro": str})


class TestProjection:
    """Ensure all coordinates are in WGS84 decimal degrees (not projected meters).
    Critical: downstream analysis clips schools against LAC boundaries in EPSG:4326."""

    def test_latitudes_in_decimal_degrees(self, iso):
        """Latitude must be between -90 and 90 (not projected meters)."""
        df = load(iso)
        georef = df.dropna(subset=["latitud"])
        if georef.empty:
            pytest.skip(f"{iso}: no georeferenced schools")

        outside = georef[(georef["latitud"] < -90) | (georef["latitud"] > 90)]
        assert len(outside) == 0, (
            f"{iso}: {len(outside)} schools with latitude outside [-90, 90] — "
            f"likely projected coordinates (meters). "
            f"Range: [{georef['latitud'].min():.1f}, {georef['latitud'].max():.1f}]"
        )

    def test_longitudes_in_decimal_degrees(self, iso):
        """Longitude must be between -180 and 180 (not projected meters)."""
        df = load(iso)
        georef = df.dropna(subset=["longitud"])
        if georef.empty:
            pytest.skip(f"{iso}: no georeferenced schools")

        outside = georef[(georef["longitud"] < -180) | (georef["longitud"] > 180)]
        assert len(outside) == 0, (
            f"{iso}: {len(outside)} schools with longitude outside [-180, 180] — "
            f"likely projected coordinates (meters). "
            f"Range: [{georef['longitud'].min():.1f}, {georef['longitud'].max():.1f}]"
        )


class TestCoordinates:
    """Validate coordinate quality and coverage."""

    def test_no_zero_zero(self, iso):
        df = load(iso)
        georef = df.dropna(subset=["latitud", "longitud"])
        at_origin = georef[(georef["latitud"] == 0) & (georef["longitud"] == 0)]
        assert len(at_origin) == 0, (
            f"{iso}: {len(at_origin)} schools at (0, 0)"
        )

    def test_within_country_bbox(self, iso):
        df = load(iso)
        georef = df.dropna(subset=["latitud", "longitud"])
        if georef.empty:
            pytest.skip(f"{iso}: no georeferenced schools")

        bbox = COUNTRY_BBOX.get(iso)
        if bbox is None:
            pytest.skip(f"{iso}: no bounding box defined")

        # Schema v2: only schools policy-included for spatial indicators must lie in bbox.
        # Rows excluded from spatial use can sit anywhere — they are filtered downstream.
        if "include_in_spatial_indicators" in georef.columns:
            spatial_in = georef[georef["include_in_spatial_indicators"] == True]
        else:
            spatial_in = georef
        if spatial_in.empty:
            pytest.skip(f"{iso}: no schools policy-included for spatial indicators")

        min_lat, max_lat, min_lon, max_lon = bbox
        margin = 2  # degrees — accounts for border schools and bbox imprecision
        outside = spatial_in[
            (spatial_in["latitud"] < min_lat - margin) | (spatial_in["latitud"] > max_lat + margin)
            | (spatial_in["longitud"] < min_lon - margin) | (spatial_in["longitud"] > max_lon + margin)
        ]
        # Remote-territory / island rows are legitimately outside the mainland bbox
        # (e.g., ECU Galapagos, CHL Easter Island) and Step 02 marks them explicitly.
        if "qc_scope_class" in outside.columns:
            outside = outside[outside["qc_scope_class"] != "remote_territory_or_island"]
        assert len(outside) == 0, (
            f"{iso}: {len(outside)} schools outside mainland bbox, policy-included for "
            f"spatial indicators, and not classified as remote_territory_or_island. "
            f"Example: {outside[['id_centro', 'latitud', 'longitud', 'qc_scope_class']].head(3).to_dict('records')}"
        )

    def test_no_swapped_lat_lon(self, iso):
        """In LAC, latitude should generally be < 35 and longitude < -30."""
        df = load(iso)
        georef = df.dropna(subset=["latitud", "longitud"])
        if georef.empty:
            pytest.skip(f"{iso}: no georeferenced schools")

        # If longitude is positive and large, coords are likely swapped
        swapped = georef[
            (georef["longitud"] > 0) & (georef["longitud"].abs() > 30)
        ]
        assert len(swapped) == 0, (
            f"{iso}: {len(swapped)} schools with likely swapped lat/lon (positive longitude)"
        )

    def test_georef_rate_above_threshold(self, iso):
        """At least 80% of schools should have coordinates."""
        if iso in LOW_GEOREF_EXEMPT:
            pytest.skip(f"{iso}: exempt from georef threshold")

        df = load(iso)
        total = len(df)
        georef = df["latitud"].notna().sum()
        rate = georef / total if total > 0 else 0

        assert rate >= 0.60, (
            f"{iso}: georef rate {rate:.1%} ({georef:,}/{total:,}) below 60% threshold"
        )


class TestQCValidation:
    """Tests based on QC coordinate report (admin boundary validation)."""

    def test_mismatch_rate_below_threshold(self, iso):
        """Schools whose coordinates land in a different admin unit than their address should be < 5%."""
        if not QC_REPORT.exists():
            pytest.skip("QC report not generated yet — run pipeline/02_qc_coordinates.py")

        qc = pd.read_csv(QC_REPORT)
        row = qc[qc["iso"] == iso]
        if row.empty:
            pytest.skip(f"{iso}: not in QC report")

        row = row.iloc[0]
        checked = row["qc_checked"]
        mismatches = row["mismatch"]
        if checked == 0:
            pytest.skip(f"{iso}: no schools QC-checked")

        rate = mismatches / checked
        assert rate < 0.05, (
            f"{iso}: {mismatches:,.0f}/{checked:,.0f} ({rate:.1%}) coordinate-address mismatches — above 5% threshold"
        )

    def test_no_out_of_bounds(self, iso):
        """No schools should have coordinates outside their country entirely."""
        if not QC_REPORT.exists():
            pytest.skip("QC report not generated yet")

        qc = pd.read_csv(QC_REPORT)
        row = qc[qc["iso"] == iso]
        if row.empty:
            pytest.skip(f"{iso}: not in QC report")

        oob = row.iloc[0]["out_of_bounds"]
        assert oob == 0, (
            f"{iso}: {oob:.0f} schools with coordinates outside country boundary"
        )

    def test_duplicate_coords_with_diff_address_below_threshold(self, iso):
        """Schools sharing exact coordinates but different addresses should be < 20%."""
        if not QC_REPORT.exists():
            pytest.skip("QC report not generated yet")

        qc = pd.read_csv(QC_REPORT)
        row = qc[qc["iso"] == iso]
        if row.empty:
            pytest.skip(f"{iso}: not in QC report")

        row = row.iloc[0]
        dup_diff = row["dup_diff_addr_pct"]
        assert dup_diff < 20.0, (
            f"{iso}: {dup_diff:.1f}% of schools share coordinates with different addresses — above 20% threshold"
        )

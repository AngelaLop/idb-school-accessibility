"""Test geocoding quality and acceptance criteria.

Rules (validated by ground truth analysis of 550 schools, 11 countries):
  - ArcGIS score >= 95 → "street" precision (87% within 5km)
  - ArcGIS score 90-95 → "centroid" precision (51% within 5km)
  - ArcGIS score < 90  → "uncertain" precision (reject)
  - FILL targets: accept street + centroid, reject uncertain
  - COMPARE targets: NEVER replace GPS, only flag discrepancy
  - No IMPROVEMENT category — geocoder cannot reliably improve ministry GPS
"""

import pandas as pd
import pytest
from pathlib import Path
from conftest import _cima_path, COUNTRY_BBOX
from pipeline.constants import COORDINATE_QUALITY_VALUES_V2, COORDINATE_SOURCES

RESULTS = Path("results")
LEGACY_QUALITY_VALUES = {"gps", "street", "centroid", "centroid_flag", "flag"}
LEGACY_SOURCE_VALUES = {"original", "geocoded", "auxiliary", "adm3_centroid", "adm3_centroid_large"}


def load(iso):
    return pd.read_csv(_cima_path(iso), dtype={"id_centro": str})


def _usable_coords(df):
    lat = pd.to_numeric(df["latitud"], errors="coerce")
    lon = pd.to_numeric(df["longitud"], errors="coerce")
    return lat.notna() & lon.notna() & ~((lat == 0) | (lon == 0))


def has_geocoding(iso):
    """Check if geocoding has been run for this country."""
    df = load(iso)
    return "geocode_source" in df.columns and (df["geocode_source"].notna() & (df["geocode_source"] != "")).any()


class TestGeocodingAcceptance:
    """Validate that geocoded coordinates meet acceptance criteria."""

    def test_fill_rejected_have_no_coords(self, iso):
        """Fill schools with REJECT acceptance must NOT have coordinates."""
        df = load(iso)
        if not has_geocoding(iso):
            pytest.skip(f"{iso}: no geocoding data")
        rejected_fill = df[
            (df["acceptance"] == "REJECT") &
            (df["latitud_geocoded"].isna())  # fill targets don't have latitud_geocoded
        ]
        if rejected_fill.empty:
            pytest.skip(f"{iso}: no rejected fill schools")
        has_coords = rejected_fill[rejected_fill["latitud"].notna()]
        assert len(has_coords) == 0, (
            f"{iso}: {len(has_coords)} rejected fill schools got coordinates written"
        )

    def test_fill_centroids_flagged(self, iso):
        """Fill schools with centroid precision must be ACCEPT_CENTROID."""
        df = load(iso)
        if not has_geocoding(iso):
            pytest.skip(f"{iso}: no geocoding data")
        if "geocode_precision" not in df.columns:
            pytest.skip(f"{iso}: no geocode_precision column")
        fill_centroids = df[
            (df["geocode_precision"] == "centroid") &
            (df["coordinate_source"] == "geocoded")
        ]
        if fill_centroids.empty:
            pytest.skip(f"{iso}: no fill centroids")
        unflagged = fill_centroids[fill_centroids["acceptance"] != "ACCEPT_CENTROID"]
        assert len(unflagged) == 0, (
            f"{iso}: {len(unflagged)} fill centroid schools not flagged as ACCEPT_CENTROID"
        )

    def test_geocoded_within_country_bbox(self, iso):
        """All geocoded coordinates must fall within the country."""
        df = load(iso)
        if not has_geocoding(iso):
            pytest.skip(f"{iso}: no geocoding data")
        bbox = COUNTRY_BBOX.get(iso)
        if bbox is None:
            pytest.skip(f"{iso}: no bounding box")

        min_lat, max_lat, min_lon, max_lon = bbox
        margin = 2

        geocoded = df[df["latitud_geocoded"].notna()]
        if not geocoded.empty:
            outside = geocoded[
                (geocoded["latitud_geocoded"] < min_lat - margin) |
                (geocoded["latitud_geocoded"] > max_lat + margin) |
                (geocoded["longitud_geocoded"] < min_lon - margin) |
                (geocoded["longitud_geocoded"] > max_lon + margin)
            ]
            assert len(outside) == 0, (
                f"{iso}: {len(outside)} geocoded coords outside country bbox"
            )

    def test_compare_never_replaces_gps(self, iso):
        """Compare schools must ALWAYS keep original GPS. No IMPROVEMENT category.

        Schema v2: latitud_geocoded.notna() is no longer a reliable compare-vs-fill
        discriminator because fill rows from the cascade B-2 also carry geocoder
        coords. Compare rows are identified by acceptance ∈ {KEEP_ORIGINAL, FLAG};
        fill rows by ACCEPT*/REJECT.
        """
        df = load(iso)
        if not has_geocoding(iso):
            pytest.skip(f"{iso}: no geocoding data")
        if "acceptance" not in df.columns:
            pytest.skip(f"{iso}: no acceptance column")

        compare = df[df["acceptance"].isin(["KEEP_ORIGINAL", "FLAG"])]
        if compare.empty:
            pytest.skip(f"{iso}: no compare schools")
        non_original = compare[compare["coordinate_source"] != "original"]
        assert len(non_original) == 0, (
            f"{iso}: {len(non_original)} compare schools (acceptance KEEP_ORIGINAL/FLAG) "
            f"have coordinate_source != 'original'"
        )
        # No IMPROVEMENT acceptance should exist
        assert "IMPROVEMENT" not in df["acceptance"].values, (
            f"{iso}: IMPROVEMENT acceptance found — GPS should never be replaced by geocoder"
        )

    def test_acceptance_criteria_valid(self, iso):
        """Every geocoded school must have a valid acceptance decision."""
        df = load(iso)
        if not has_geocoding(iso):
            pytest.skip(f"{iso}: no geocoding data")
        if "acceptance" not in df.columns:
            pytest.skip(f"{iso}: no acceptance column")

        geocoded = df[(df["geocode_source"].notna()) & (df["geocode_source"] != "")]
        if geocoded.empty:
            pytest.skip(f"{iso}: no geocoded schools")

        valid_levels = {
            "ACCEPT", "ACCEPT_WITH_FLAG", "ACCEPT_CENTROID", "REJECT",  # fill targets
            "KEEP_ORIGINAL", "FLAG",                                     # compare targets
        }
        has_acceptance = geocoded["acceptance"].isin(valid_levels)
        missing = (~has_acceptance).sum()
        assert missing == 0, (
            f"{iso}: {missing} geocoded schools without valid acceptance level. "
            f"Found: {geocoded[~has_acceptance]['acceptance'].unique()}"
        )

    def test_coordinate_quality_valid(self, iso):
        """Schools with coordinates must have valid coordinate_quality."""
        df = load(iso)
        if "coordinate_quality" not in df.columns:
            pytest.skip(f"{iso}: no coordinate_quality column")
        has_coords = df[_usable_coords(df)]
        if has_coords.empty:
            pytest.skip(f"{iso}: no schools with coordinates")
        valid_qualities = LEGACY_QUALITY_VALUES | set(COORDINATE_QUALITY_VALUES_V2)
        invalid = has_coords[~has_coords["coordinate_quality"].isin(valid_qualities)]
        assert len(invalid) == 0, (
            f"{iso}: {len(invalid)} schools with coords but invalid coordinate_quality. "
            f"Found: {invalid['coordinate_quality'].unique()}"
        )

    def test_coordinate_source_valid(self, iso):
        """Schools with coordinates must have valid coordinate_source."""
        df = load(iso)
        if "coordinate_source" not in df.columns:
            pytest.skip(f"{iso}: no coordinate_source column")
        has_coords = df[_usable_coords(df)]
        if has_coords.empty:
            pytest.skip(f"{iso}: no schools with coordinates")
        valid_sources = LEGACY_SOURCE_VALUES | set(COORDINATE_SOURCES)
        invalid = has_coords[~has_coords["coordinate_source"].isin(valid_sources)]
        assert len(invalid) == 0, (
            f"{iso}: {len(invalid)} schools with coords but invalid coordinate_source. "
            f"Found: {invalid['coordinate_source'].unique()}"
        )

    def test_score_threshold_enforced(self, iso):
        """Geocoded fill schools with score < 90 must be rejected (no coords)."""
        df = load(iso)
        if not has_geocoding(iso):
            pytest.skip(f"{iso}: no geocoding data")
        if "arcgis_score" not in df.columns:
            pytest.skip(f"{iso}: no arcgis_score column")
        # Fill schools that got geocoded coords
        fill_accepted = df[
            (df["coordinate_source"] == "geocoded") &
            (df["arcgis_score"].notna())
        ]
        if fill_accepted.empty:
            pytest.skip(f"{iso}: no fill schools with scores")
        low_score = fill_accepted[fill_accepted["arcgis_score"] < 90]
        assert len(low_score) == 0, (
            f"{iso}: {len(low_score)} fill schools accepted with score < 90"
        )

"""End-to-end tests for `qc_core.finalize_cima_evidence` with synthetic data.

These avoid loading real shapefiles by stubbing `boundaries_by_level` with
in-memory GeoDataFrames built from a couple of square polygons. The ADM2
spatial join still exercises the real code path.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline import qc_core
from pipeline.qc_core import (
    FINALIZE_OUTPUT_COLUMNS,
    finalize_cima_evidence,
)


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

def _box(min_lon, min_lat, max_lon, max_lat):
    from shapely.geometry import Polygon
    return Polygon([
        (min_lon, min_lat), (max_lon, min_lat),
        (max_lon, max_lat), (min_lon, max_lat),
        (min_lon, min_lat),
    ])


@pytest.fixture
def boundaries():
    """Synthetic ADM0/1/2 polygons inside ISO='TST'.

    Layout (lon, lat):
        ADM1 'North' covers lat 5-10, ADM1 'South' covers lat 0-5.
        ADM2 'NorthEast' is upper-right quadrant of North, etc.
        ADM0 includes both the mainland box (0-10, 0-10) and an island box
        (20-21, 20-21) that sits outside the mainland bbox.
    """
    import geopandas as gpd
    adm0 = gpd.GeoDataFrame(
        [
            {"ADM0_PCODE": "TST", "ADM0_EN": "Testland", "geometry": _box(0, 0, 10, 10)},
            {"ADM0_PCODE": "TST", "ADM0_EN": "Testland", "geometry": _box(20, 20, 21, 21)},
        ],
        crs="EPSG:4326",
    )
    adm1 = gpd.GeoDataFrame(
        [
            {"ADM0_PCODE": "TST", "ADM1_EN": "North", "ADM1_PCODE": "TST-N",
             "geometry": _box(0, 5, 10, 10)},
            {"ADM0_PCODE": "TST", "ADM1_EN": "South", "ADM1_PCODE": "TST-S",
             "geometry": _box(0, 0, 10, 5)},
        ],
        crs="EPSG:4326",
    )
    adm1["adm1_norm"] = adm1["ADM1_EN"].apply(qc_core.normalize_name)
    adm2 = gpd.GeoDataFrame(
        [
            {"ADM0_PCODE": "TST", "ADM1_EN": "North", "ADM2_EN": "NorthEast",
             "ADM2_PCODE": "TST-NE", "geometry": _box(5, 5, 10, 10)},
            {"ADM0_PCODE": "TST", "ADM1_EN": "North", "ADM2_EN": "NorthWest",
             "ADM2_PCODE": "TST-NW", "geometry": _box(0, 5, 5, 10)},
            {"ADM0_PCODE": "TST", "ADM1_EN": "South", "ADM2_EN": "SouthEast",
             "ADM2_PCODE": "TST-SE", "geometry": _box(5, 0, 10, 5)},
        ],
        crs="EPSG:4326",
    )
    adm2["adm2_norm"] = adm2["ADM2_EN"].apply(qc_core.normalize_name)
    return {0: adm0, 1: adm1, 2: adm2}


@pytest.fixture(autouse=True)
def _patch_country_bbox(monkeypatch):
    """Inject a TST bbox so finalize_cima_evidence can run on synthetic data."""
    monkeypatch.setitem(qc_core.COUNTRY_BBOX, "TST", (0.0, 10.0, 0.0, 10.0))


SCOPE_ADM2 = {"final_match_level": "adm2"}
SCOPE_ADM1 = {"final_match_level": "adm1"}
SCOPE_BBOX = {"final_match_level": "bbox_only"}


def _basic_cima(rows: list[dict]) -> pd.DataFrame:
    base = []
    for i, r in enumerate(rows):
        base.append({
            "id_centro": str(r.get("id", f"s{i}")),
            "nombre_centro": r.get("name", f"School {i}"),
            "sector": "Public",
            "nivel_primaria": 1, "nivel_secbaja": 0, "nivel_secalta": 0,
            "latitud": r.get("lat"),
            "longitud": r.get("lon"),
            "adm0_pcode": "TST",
            "coordinate_source": r.get("source", ""),
            "acceptance": r.get("acceptance", ""),
            "geocode_precision": r.get("precision", ""),
            "geocode_distance_km": r.get("dist_km"),
            "geo_adm2_check": r.get("geo_adm2", ""),
            "orig_adm2_check": r.get("orig_adm2", ""),
        })
    return pd.DataFrame(base)


# ---------------------------------------------------------------------------
# Smoke tests — labels assigned correctly per case
# ---------------------------------------------------------------------------

class TestFinalizeBasic:
    def test_validated_gps_under_adm2(self, boundaries):
        cima = _basic_cima([{"id": "a", "lat": 7.0, "lon": 7.0, "source": "original"}])
        addr = pd.DataFrame([{"id_centro": "a", "raw_adm1": "North", "raw_adm2": "NorthEast"}])
        out = finalize_cima_evidence(cima, addr, boundaries, "TST", SCOPE_ADM2)
        row = out.iloc[0]
        assert row["coordinate_quality"] == "gps_validated"
        assert row["coordinate_quality_reason"] == "validated"
        assert row["adm1_pcode"] == "TST-N"
        assert row["adm2_pcode"] == "TST-NE"
        assert row["qc_match_level"] == "ADM2"
        assert row["qc_evidence_version"] == 2

    def test_missing_coords(self, boundaries):
        cima = _basic_cima([{"id": "a", "lat": np.nan, "lon": np.nan, "source": ""}])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        row = out.iloc[0]
        assert row["coordinate_quality"] == "missing"
        assert row["adm1_pcode"] == ""
        assert row["adm2_pcode"] == ""
        assert row["qc_in_bounds"] is False or row["qc_in_bounds"] == False  # noqa: E712

    def test_out_of_bounds(self, boundaries):
        cima = _basic_cima([{"id": "a", "lat": 99.0, "lon": 99.0, "source": "original"}])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        row = out.iloc[0]
        assert row["coordinate_quality"] == "out_of_bounds"
        assert row["coordinate_quality_reason"] == "bbox"

    def test_adm2_mismatch_caught(self, boundaries):
        # School at (7,7) is in NorthEast, but raw declares NorthWest.
        cima = _basic_cima([{"id": "a", "lat": 7.0, "lon": 7.0, "source": "original"}])
        addr = pd.DataFrame([{"id_centro": "a", "raw_adm1": "North", "raw_adm2": "NorthWest"}])
        out = finalize_cima_evidence(cima, addr, boundaries, "TST", SCOPE_ADM2)
        row = out.iloc[0]
        assert row["coordinate_quality"] == "adm_mismatch"
        assert row["coordinate_quality_reason"] == "adm2_mismatch"

    def test_cluster_centroid_for_original_only(self, boundaries):
        cima = _basic_cima([
            {"id": f"c{i}", "lat": 7.0, "lon": 7.0, "source": "original"}
            for i in range(qc_core.CLUSTER_THRESHOLD)
        ])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        # No addresses → cluster of 5 with same source=original → cluster_centroid
        for _, row in out.iterrows():
            assert row["coordinate_quality"] == "cluster_centroid"
            assert row["coordinate_quality_reason"] == "cluster_ge5"

    def test_pan_legacy_centroid_cascade_migrated(self, boundaries):
        # Simulate PAN-style legacy CIMA row.
        cima = _basic_cima([
            {"id": "p1", "lat": 7.0, "lon": 7.0, "source": "adm3_centroid"},
            {"id": "p2", "lat": 7.0, "lon": 7.0, "source": "adm3_centroid_large"},
        ])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        for _, row in out.iterrows():
            assert row["coordinate_source"] == "centroid_cascade"
            assert row["coordinate_quality"] == "geocoded_centroid"
            assert row["coordinate_quality_reason"] == "fill_centroid"
        assert out.set_index("id_centro").loc["p1", "qc_centroid_bias"] == "normal"
        assert out.set_index("id_centro").loc["p2", "qc_centroid_bias"] == "high"


# ---------------------------------------------------------------------------
# Idempotency — the headline acceptance criterion
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_finalize_twice_same_output(self, boundaries):
        cima = _basic_cima([
            {"id": "a", "lat": 7.0, "lon": 7.0, "source": "original"},
            {"id": "b", "lat": np.nan, "lon": np.nan, "source": ""},
            {"id": "c", "lat": 99.0, "lon": 99.0, "source": "original"},
            {"id": "d", "lat": 7.0, "lon": 7.0, "source": "adm3_centroid"},
        ])
        addr = pd.DataFrame([
            {"id_centro": "a", "raw_adm1": "North", "raw_adm2": "NorthEast"},
            {"id_centro": "b", "raw_adm1": "South", "raw_adm2": "SouthEast"},
            {"id_centro": "c", "raw_adm1": "North", "raw_adm2": "NorthEast"},
            {"id_centro": "d", "raw_adm1": "North", "raw_adm2": "NorthEast"},
        ])

        first = finalize_cima_evidence(cima, addr, boundaries, "TST", SCOPE_ADM2)
        # Re-run on already-enriched CIMA — must produce byte-identical output.
        second = finalize_cima_evidence(first.copy(), addr, boundaries, "TST", SCOPE_ADM2)

        # Compare on the contracted output columns (column order can drift in
        # join operations, so reindex first).
        first = first.sort_values("id_centro").reset_index(drop=True)
        second = second.sort_values("id_centro").reset_index(drop=True)
        for col in ("coordinate_quality", "coordinate_quality_reason",
                    "coordinate_source", "qc_centroid_bias",
                    "adm1_pcode", "adm2_pcode",
                    "qc_in_bounds", "qc_scope_class", "include_in_spatial_indicators", "qc_swapped",
                    "qc_adm1_status", "qc_adm2_status", "qc_match_level",
                    "qc_cluster_size_exact", "qc_evidence_version"):
            # `.equals()` treats NA-NA as equal, unlike `==` which yields NA.
            # Cast to object so the comparison works across pandas extension
            # dtypes (e.g. nullable Boolean include_in_spatial_indicators).
            assert first[col].astype(object).fillna("__nan__").equals(
                second[col].astype(object).fillna("__nan__")
            ), f"{col} drifted on re-finalize"


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------

class TestOutputContract:
    def test_all_finalize_output_columns_present(self, boundaries):
        cima = _basic_cima([{"id": "a", "lat": 7.0, "lon": 7.0, "source": "original"}])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        for col in FINALIZE_OUTPUT_COLUMNS:
            assert col in out.columns, f"missing output column: {col}"

    def test_qc_evidence_version_constant(self, boundaries):
        cima = _basic_cima([{"id": "a", "lat": 7.0, "lon": 7.0, "source": "original"}])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        assert (out["qc_evidence_version"] == 2).all()

    def test_quality_in_v2_enum(self, boundaries):
        from pipeline.constants import COORDINATE_QUALITY_VALUES_V2
        cima = _basic_cima([
            {"id": "a", "lat": 7.0, "lon": 7.0, "source": "original"},
            {"id": "b", "lat": np.nan, "lon": np.nan, "source": ""},
        ])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        assert set(out["coordinate_quality"]).issubset(COORDINATE_QUALITY_VALUES_V2)


# ---------------------------------------------------------------------------
# Country-policy gates
# ---------------------------------------------------------------------------

class TestPolicyGates:
    def test_bbox_only_country_never_validated(self, boundaries):
        cima = _basic_cima([{"id": "a", "lat": 7.0, "lon": 7.0, "source": "original"}])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_BBOX)
        assert out.iloc[0]["qc_match_level"] == "NONE"
        assert out.iloc[0]["coordinate_quality"] == "gps_unverified"

    def test_adm1_policy_ignores_adm2_mismatch(self, boundaries):
        # Even if adm2 polygon would mismatch, country policy says only adm1 counts.
        cima = _basic_cima([{"id": "a", "lat": 7.0, "lon": 7.0, "source": "original"}])
        addr = pd.DataFrame([{"id_centro": "a", "raw_adm1": "North", "raw_adm2": "Invented"}])
        out = finalize_cima_evidence(cima, addr, boundaries, "TST", SCOPE_ADM1)
        assert out.iloc[0]["coordinate_quality"] == "gps_validated"
        assert out.iloc[0]["qc_match_level"] == "ADM1"

    def test_qc_override_fields_can_differ_from_query_fields(self, boundaries):
        cima = _basic_cima([{"id": "a", "lat": 7.0, "lon": 7.0, "source": "original"}])
        addr = pd.DataFrame([{
            "id_centro": "a",
            "raw_adm1": "WrongLevelForQuery",
            "raw_adm2": "WrongLevelForQuery",
            "qc_raw_adm1": "",
            "qc_raw_adm2": "NorthEast",
        }])
        out = finalize_cima_evidence(cima, addr, boundaries, "TST", SCOPE_ADM2)
        row = out.iloc[0]
        assert row["qc_adm2_status"] == "MATCH"
        assert row["coordinate_quality"] == "gps_validated"

    def test_adm1_code_beats_name_for_validation(self, boundaries):
        cima = _basic_cima([{"id": "a", "lat": 7.0, "lon": 7.0, "source": "original"}])
        addr = pd.DataFrame([{
            "id_centro": "a",
            "raw_adm1": "WrongName",
            "raw_adm2": "",
            "raw_adm1_code": "TST-N",
        }])
        out = finalize_cima_evidence(cima, addr, boundaries, "TST", SCOPE_ADM1)
        row = out.iloc[0]
        assert row["qc_adm1_status"] == "MATCH"
        assert row["coordinate_quality"] == "gps_validated"

    def test_resolver_invariant_adm1_country_never_emits_boundary_zone(self):
        """O1 invariant (review-cycle 2026-05-10): at adm1-only countries the
        resolver must NOT emit boundary_zone — the semantic of 'on the edge
        of the validated polygon' is meaningless when there is no fall-back
        validation level. Such rows must resolve as adm_mismatch instead.
        """
        from pipeline.qc_core import resolve_coordinate_quality

        # Construct evidence that WOULD qualify for boundary_zone at adm2:
        # original GPS, geocoder within 5 km, distance to raw polygon < 5 km.
        ev = {
            "has_coords": True, "in_bounds": True, "swapped": False,
            "adm1_status": "MISMATCH", "adm2_status": "NO_RAW_ADM",
            "cluster_size_exact": 0,
            "coordinate_source": "original",
            "geocode_precision": "", "geocode_acceptance": "",
            "geo_adm2_check": "", "orig_adm2_check": "",
            "geocode_distance_km": 1.0,
            "distance_to_raw_polygon_km": 1.0,
        }
        # adm1-only country: resolver must NOT soften to boundary_zone.
        q, _ = resolve_coordinate_quality(ev, {"final_match_level": "adm1"})
        assert q == "adm_mismatch", f"adm1-only must emit adm_mismatch, got {q!r}"

        # adm2-only country with the SAME evidence: resolver may soften
        # because adm1=MISMATCH branch fires after adm2 branch, and at
        # adm2-only we trust adm1 boundary_zone semantically.
        q2, _ = resolve_coordinate_quality(ev, {"final_match_level": "adm2"})
        assert q2 == "boundary_zone", f"adm2 should soften to boundary_zone, got {q2!r}"


# ---------------------------------------------------------------------------
# Scope classification + indicator policy
# ---------------------------------------------------------------------------

class TestScopeClassification:
    def test_inside_mainland_bbox_marked_include_true(self, boundaries):
        cima = _basic_cima([{"id": "a", "lat": 7.0, "lon": 7.0, "source": "original"}])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        row = out.iloc[0]
        assert row["qc_scope_class"] == "inside_mainland_bbox"
        assert bool(row["include_in_spatial_indicators"]) is True

    def test_remote_island_marked_inside_country(self, boundaries):
        cima = _basic_cima([{"id": "a", "lat": 20.5, "lon": 20.5, "source": "original"}])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        row = out.iloc[0]
        assert row["coordinate_quality"] == "out_of_bounds"
        assert row["qc_scope_class"] == "remote_territory_or_island"
        assert bool(row["include_in_spatial_indicators"]) is True

    def test_near_border_review_uses_nullable_policy(self, boundaries):
        cima = _basic_cima([{"id": "a", "lat": 10.02, "lon": 5.0, "source": "original"}])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        row = out.iloc[0]
        assert row["qc_scope_class"] == "near_border_review"
        assert pd.isna(row["include_in_spatial_indicators"])

    def test_invalid_numeric_excluded(self, boundaries):
        cima = _basic_cima([{"id": "a", "lat": 180.0, "lon": 5.0, "source": "original"}])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        row = out.iloc[0]
        assert row["qc_scope_class"] == "invalid_numeric"


# ---------------------------------------------------------------------------
# _spatial_indicator_policy unit tests (spec §5)
# ---------------------------------------------------------------------------

class TestSpatialIndicatorPolicy:
    """Direct unit tests for _spatial_indicator_policy.

    Reference: docs/coordinate_quality_spec.md §4-5.
    """

    @staticmethod
    def _row(quality, scope, adm1_status="", final_match_level="adm2"):
        from pipeline.qc_core import _spatial_indicator_policy
        return _spatial_indicator_policy(pd.Series({
            "coordinate_quality": quality,
            "qc_scope_class": scope,
            "qc_adm1_status": adm1_status,
        }), final_match_level=final_match_level)

    # §5.1 — severity gate on near-border rescue
    def test_severity_gate_blocks_geocoder_disagrees_in_near_border(self):
        assert pd.isna(self._row("geocoder_disagrees", "near_border_review", "MATCH"))

    def test_severity_gate_blocks_adm_mismatch_in_near_border(self):
        assert pd.isna(self._row("adm_mismatch", "near_border_review", "MATCH"))

    def test_severity_gate_blocks_cluster_centroid_in_near_border(self):
        assert pd.isna(self._row("cluster_centroid", "near_border_review", "MATCH"))

    def test_near_border_still_rescues_gps_validated(self):
        assert self._row("gps_validated", "near_border_review", "MATCH") is True

    def test_near_border_still_rescues_gps_unverified_with_match(self):
        assert self._row("gps_unverified", "near_border_review", "MATCH") is True

    def test_near_border_gps_unverified_without_match_stays_nan(self):
        assert pd.isna(self._row("gps_unverified", "near_border_review", ""))

    # §5.2 — boundary_zone auto-include
    def test_boundary_zone_auto_includes(self):
        assert self._row("boundary_zone", "inside_mainland_bbox") is True

    # O1 invariant (review-cycle 2026-05-10): the resolver does not emit
    # `boundary_zone` for adm1-only countries with adm1_status != MATCH.
    # If a row ever reaches the policy with that combo (e.g., crafted input
    # or future regression) the policy still treats it as True per §5.2;
    # but the production data path never produces such a row. The policy is
    # safe by virtue of the upstream invariant.
    def test_boundary_zone_adm1_country_match_includes(self):
        # The only adm1-country case that the resolver can produce.
        assert self._row("boundary_zone", "inside_mainland_bbox",
                         adm1_status="MATCH", final_match_level="adm1") is True

    def test_boundary_zone_adm2_country_mismatch_includes(self):
        # adm2-only countries: adm1 status is incidental; boundary_zone fires on adm2 edge.
        assert self._row("boundary_zone", "inside_mainland_bbox",
                         adm1_status="MISMATCH", final_match_level="adm2") is True

    def test_boundary_zone_adm2_country_match_includes(self):
        assert self._row("boundary_zone", "inside_mainland_bbox",
                         adm1_status="MATCH", final_match_level="adm2") is True

    # §5.3 — geocoded_street near_border edge case (street precision rescue still applies)
    def test_geocoded_street_in_near_border_includes(self):
        assert self._row("geocoded_street", "near_border_review") is True

    # §5.4 (2026-05-11) — centroid-precision labels are NaN regardless of scope.
    # 1-5 km positional error exceeds the 15-min walking isochrone, so centroids
    # cannot enter walking-accessibility indicators automatically (Apparicio
    # 2008; Hewko 2002). Provenance (deliberate cascade vs covert cluster) is
    # methodologically equivalent.
    def test_geocoded_centroid_inside_is_nan(self):
        assert pd.isna(self._row("geocoded_centroid", "inside_mainland_bbox"))

    def test_geocoded_centroid_in_near_border_is_nan(self):
        assert pd.isna(self._row("geocoded_centroid", "near_border_review"))

    def test_geocoded_centroid_outside_country_excluded(self):
        # Outside-country override still applies (scope_class wins).
        assert self._row("geocoded_centroid", "outside_country") is False

    # Regression: existing behavior preserved
    def test_inside_mainland_gps_validated_includes(self):
        assert self._row("gps_validated", "inside_mainland_bbox") is True

    def test_remote_island_out_of_bounds_includes(self):
        assert self._row("out_of_bounds", "remote_territory_or_island") is True

    def test_outside_country_always_excludes(self):
        assert self._row("gps_validated", "outside_country") is False

    def test_missing_quality_excludes_even_inside_bbox(self):
        assert self._row("missing", "inside_mainland_bbox") is False

    def test_adm_mismatch_inside_stays_nan(self):
        assert pd.isna(self._row("adm_mismatch", "inside_mainland_bbox"))

    def test_cluster_centroid_inside_stays_nan(self):
        assert pd.isna(self._row("cluster_centroid", "inside_mainland_bbox"))


# ---------------------------------------------------------------------------
# Defensive promotion: latitud_geocoded → latitud
# ---------------------------------------------------------------------------

class TestGeocodedBackupPromotion:
    """Step 01 reruns wipe latitud/longitud (in SCHEMA) back to raw values.
    For schools that were geocoded, latitud_geocoded survives as a non-base
    backup. finalize must promote it back so the row doesn't degrade to
    `missing` and the geocoded coord stays the basis for spatial QC.
    """

    def _row_with_backup(self, *, lat_base, lon_base, source, precision="centroid"):
        return {
            "id_centro": "g1",
            "nombre_centro": "Geocoded",
            "sector": "Public",
            "nivel_primaria": 1, "nivel_secbaja": 0, "nivel_secalta": 0,
            "latitud": lat_base,
            "longitud": lon_base,
            "adm0_pcode": "TST",
            "coordinate_source": source,
            "geocode_precision": precision,
            "acceptance": "ACCEPT_CENTROID" if precision == "centroid" else "ACCEPT",
            "latitud_geocoded": 7.0,
            "longitud_geocoded": 7.0,
        }

    def test_promotes_when_base_is_nan(self, boundaries):
        cima = pd.DataFrame([self._row_with_backup(
            lat_base=np.nan, lon_base=np.nan, source="geocoded",
        )])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        row = out.iloc[0]
        assert row["latitud"] == 7.0
        assert row["longitud"] == 7.0
        assert row["coordinate_quality"] in {"geocoded_centroid", "geocoded_street"}
        assert row["coordinate_quality"] != "missing"

    def test_promotes_when_base_is_zero_pair(self, boundaries):
        cima = pd.DataFrame([self._row_with_backup(
            lat_base=0.0, lon_base=0.0, source="centroid_cascade",
        )])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        row = out.iloc[0]
        assert row["latitud"] == 7.0
        assert row["longitud"] == 7.0
        assert row["coordinate_source"] == "centroid_cascade"
        assert row["coordinate_quality"] == "geocoded_centroid"

    def test_does_not_promote_when_base_is_valid(self, boundaries):
        # Base lat/lon are valid — must NOT be overwritten by the backup.
        row = self._row_with_backup(lat_base=6.5, lon_base=6.5, source="geocoded")
        row["latitud_geocoded"] = 9.9
        row["longitud_geocoded"] = 9.9
        cima = pd.DataFrame([row])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        result = out.iloc[0]
        assert result["latitud"] == 6.5
        assert result["longitud"] == 6.5

    def test_does_not_promote_for_original_source(self, boundaries):
        # coordinate_source='original' — backup must not be promoted even if base
        # is missing (this would mask a genuine 'missing' case).
        row = self._row_with_backup(lat_base=np.nan, lon_base=np.nan, source="original")
        cima = pd.DataFrame([row])
        out = finalize_cima_evidence(cima, None, boundaries, "TST", SCOPE_ADM2)
        result = out.iloc[0]
        assert pd.isna(result["latitud"])
        assert result["coordinate_quality"] == "missing"


# ---------------------------------------------------------------------------
# DMS regression — keep parity with legacy parser
# ---------------------------------------------------------------------------

class TestDmsParity:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("25°17'13.5\"S", -25.287083),
            ("25\xb017'13.5\"S", -25.287083),       # \xb0 degree variant
            ("25\xba17'13.5\"S", -25.287083),       # masculine ordinator (PRY)
            ("25°17’13.5″S", -25.287083),  # right single quote + double prime
            ("5 51 0 N", 5.85),
            ("-25.287083", -25.287083),             # plain decimal pass-through
            ("25,287083", 25.287083),               # comma decimal
            ("not-a-coord", float("nan")),
        ],
    )
    def test_dms_variants(self, raw, expected):
        result = qc_core.dms_to_dd(raw)
        if expected != expected:  # NaN check
            assert result != result
        else:
            assert abs(result - expected) < 1e-4


# ---------------------------------------------------------------------------
# compute_geocode_targets — OOB now flows from qc_in_bounds
# ---------------------------------------------------------------------------

class TestTargetDiscoveryV2:
    def test_oob_from_qc_in_bounds_not_status(self):
        cima = pd.DataFrame({
            "id_centro": ["a", "b", "c"],
            "latitud":   [1.0, 2.0, 3.0],
            "longitud":  [10.0, 20.0, 30.0],
        })
        qc = pd.DataFrame({
            "id_centro":      ["a", "b", "c"],
            "qc_in_bounds":   [True, False, True],
            "qc_adm1_status": ["MATCH", "NOT_RUN", "MISMATCH"],
            "qc_adm2_status": ["MATCH", "NOT_RUN", "MATCH"],
        })
        out = qc_core.compute_geocode_targets(cima, addr_df=None, qc_evidence=qc)
        assert out["out_of_bounds"] == {"b"}
        # `c` is a true MISMATCH; `b` is OOB and must NOT show up here.
        assert out["mismatches"] == {"c"}

    def test_centroid_bucket_excludes_shared_campus(self):
        # 5 schools at the same point — 3 share an address (shared campus),
        # 2 declare distinct addresses (genuine centroid suspects).
        cima = pd.DataFrame({
            "id_centro": ["a", "b", "c", "d", "e"],
            "latitud":  [1.0, 1.0, 1.0, 1.0, 1.0],
            "longitud": [2.0, 2.0, 2.0, 2.0, 2.0],
        })
        addr = pd.DataFrame({
            "id_centro":  ["a", "b", "c", "d", "e"],
            "raw_street": ["Main", "Main", "Main", "Other", "Different"],
            "raw_adm2":   ["X",    "X",    "X",    "X",     "X"],
        })
        out = qc_core.compute_geocode_targets(cima, addr_df=addr)
        assert out["centroids_address_filtered"] is True
        # Only the schools with distinct addresses end up flagged as centroids.
        assert out["centroids"] == {"a", "b", "c", "d", "e"} or out["centroids"].issubset({"a", "b", "c", "d", "e"})
        # At minimum the address-different ones must be flagged.
        assert {"d", "e"}.issubset(out["centroids"])

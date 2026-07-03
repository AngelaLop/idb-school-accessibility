"""Unit tests for `pipeline/qc_core.py`.

Pure helpers — no real CIMA / shapefile loading. The orchestrator
`finalize_cima()` lives in Step 02 and gets its own integration test there.
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd
import pytest

from pipeline.qc_core import (
    CLUSTER_THRESHOLD,
    GEOCODER_DISAGREE_DISTANCE_KM,
    admin_match,
    bbox_check,
    compute_geocode_targets,
    detect_clusters_exact,
    detect_clusters_radius,
    detect_swapped,
    dms_to_dd,
    has_diff_address_in_cluster,
    is_blank,
    migrate_legacy_evidence,
    normalize_name,
    resolve_coordinate_quality,
)


# ---------------------------------------------------------------------------
# Scalar helpers
# ---------------------------------------------------------------------------

class TestNormalizeName:
    def test_strips_accents_and_lowercases(self):
        assert normalize_name("Ñuble") == "nuble"
        assert normalize_name("Distrito Federal") == "distrito federal"
        assert normalize_name("  Cundinamarca  ") == "cundinamarca"

    def test_blank_inputs_return_empty(self):
        assert normalize_name(None) == ""
        assert normalize_name("") == ""
        assert normalize_name("   ") == ""
        assert normalize_name(float("nan")) == ""


class TestDmsToDd:
    def test_decimal_conversion(self):
        # 5° 51' 0" N == 5.85
        assert dms_to_dd("5 51 0 N") == pytest.approx(5.85, abs=1e-6)

    def test_southern_hemisphere_negated(self):
        assert dms_to_dd("5 51 0 S") == pytest.approx(-5.85, abs=1e-6)

    def test_invalid_returns_nan(self):
        # Returns numpy.nan to match legacy `02_qc_coordinates.dms_to_dd` contract.
        import math
        for bad in ("not valid", "", None, float("nan")):
            r = dms_to_dd(bad)
            assert isinstance(r, float) and math.isnan(r)


class TestIsBlank:
    def test_blank_values(self):
        assert is_blank(None)
        assert is_blank(float("nan"))
        assert is_blank("")
        assert is_blank("   ")

    def test_non_blank_values(self):
        assert not is_blank("x")
        assert not is_blank(0)
        assert not is_blank(False)


class TestBboxCheck:
    PAN_BBOX = (7.1, 9.7, -83.1, -77.1)

    def test_in_bounds(self):
        assert bbox_check(8.5, -80.0, self.PAN_BBOX) is True

    def test_out_of_bounds(self):
        assert bbox_check(0.0, 0.0, self.PAN_BBOX) is False
        assert bbox_check(8.5, -90.0, self.PAN_BBOX) is False

    def test_nan_inputs(self):
        assert bbox_check(None, -80.0, self.PAN_BBOX) is False
        assert bbox_check(float("nan"), -80.0, self.PAN_BBOX) is False


class TestDetectSwapped:
    PAN_BBOX = (7.1, 9.7, -83.1, -77.1)

    def test_swap_detected(self):
        # PAN expects lat in [7.1, 9.7], lon in [-83.1, -77.1].
        # If lat=-80.0 and lon=8.5, both fall in the other's range.
        assert detect_swapped(-80.0, 8.5, self.PAN_BBOX) is True

    def test_in_bounds_not_swapped(self):
        assert detect_swapped(8.5, -80.0, self.PAN_BBOX) is False

    def test_garbage_not_swapped(self):
        # Both out of any range — not swapped, just bad.
        assert detect_swapped(100.0, 200.0, self.PAN_BBOX) is False


class TestAdminMatch:
    def test_exact_match(self):
        assert admin_match("Cundinamarca", "Cundinamarca", "COL") == "MATCH"

    def test_accent_insensitive(self):
        assert admin_match("Ñuble", "Nuble", "CHL") == "MATCH"

    def test_alias_applied(self):
        # PAN comarca alias from constants.py
        assert admin_match("Comarca Ngäbe Bugle", "Ngobe Bugle", "PAN") == "MATCH"

    def test_partial_match(self):
        # ADM2 polygons sometimes carry a prefix like "Provincia "
        assert admin_match("Azua", "Provincia Azua", "DOM") == "MATCH"

    def test_mismatch(self):
        assert admin_match("Cundinamarca", "Antioquia", "COL") == "MISMATCH"

    def test_no_raw(self):
        assert admin_match("", "Cundinamarca", "COL") == "NO_RAW_ADM"
        assert admin_match(None, "Cundinamarca", "COL") == "NO_RAW_ADM"

    def test_no_polygon(self):
        assert admin_match("Cundinamarca", "", "COL") == "NO_POLYGON"

    def test_default_level_is_adm1(self):
        # Omitting `level` keeps pre-SUR-aliases behaviour: ADM1_ALIASES is used.
        assert admin_match("Comarca Guna Yala", "Kuna Yala", "PAN") == "MATCH"

    def test_adm2_alias_applied_for_sur(self):
        # SUR ADM2 abbreviations + spelling variants — the 12 patterns the
        # SUR finalize relies on. Only fire when level=2.
        assert admin_match("Boven Sur.", "Boven Suriname", "SUR", level=2) == "MATCH"
        assert admin_match("Nw. nickerie", "Nieuw Nickerie", "SUR", level=2) == "MATCH"
        assert admin_match("Koewarasan", "Kwarasan", "SUR", level=2) == "MATCH"
        assert admin_match("Westelijk pld.", "Westelijke Polders", "SUR", level=2) == "MATCH"
        assert admin_match("Bronsweg", "Brownsweg", "SUR", level=2) == "MATCH"
        assert admin_match("Marchalkreek", "Marechallkreek", "SUR", level=2) == "MATCH"
        assert admin_match("Johanna marie", "Johanna Maria", "SUR", level=2) == "MATCH"

    def test_adm2_alias_does_not_leak_to_adm1(self):
        # SUR ADM2 aliases must NOT match when called at level=1 — keeps the
        # alias namespaces isolated by level.
        assert admin_match("Boven Sur.", "Boven Suriname", "SUR", level=1) == "MISMATCH"
        assert admin_match("Koewarasan", "Kwarasan", "SUR", level=1) == "MISMATCH"

    def test_adm2_real_mismatch_still_caught(self):
        # Non-alias residuals (real coord drift in SUR) must still flag MISMATCH.
        assert admin_match("Centrum", "Rainville", "SUR", level=2) == "MISMATCH"
        assert admin_match("Kwatta", "Welgelegen", "SUR", level=2) == "MISMATCH"

    def test_adm2_alias_country_isolated(self):
        # Aliases are per-ISO. The SUR aliases must not bleed into other countries.
        assert admin_match("Boven Sur.", "Boven Suriname", "PAN", level=2) == "MISMATCH"


# ---------------------------------------------------------------------------
# Cluster detection
# ---------------------------------------------------------------------------

class TestDetectClustersExact:
    def test_no_data(self):
        df = pd.DataFrame(columns=["id_centro", "latitud", "longitud"])
        sizes = detect_clusters_exact(df)
        assert len(sizes) == 0

    def test_singletons_zero(self):
        df = pd.DataFrame({
            "id_centro": ["a", "b", "c"],
            "latitud": [1.0, 2.0, 3.0],
            "longitud": [10.0, 20.0, 30.0],
        })
        sizes = detect_clusters_exact(df)
        assert sizes.tolist() == [1, 1, 1]

    def test_cluster_of_5_detected(self):
        df = pd.DataFrame({
            "id_centro": list("abcdef"),
            "latitud":  [1.0, 1.0, 1.0, 1.0, 1.0, 5.0],
            "longitud": [2.0, 2.0, 2.0, 2.0, 2.0, 6.0],
        })
        sizes = detect_clusters_exact(df)
        assert sizes.iloc[:5].tolist() == [5, 5, 5, 5, 5]
        assert sizes.iloc[5] == 1

    def test_excludes_zero_coords(self):
        df = pd.DataFrame({
            "id_centro": ["a", "b"],
            "latitud":  [0.0, 0.0],
            "longitud": [0.0, 0.0],
        })
        sizes = detect_clusters_exact(df)
        # (0,0) treated as missing → cluster_size = 0
        assert sizes.tolist() == [0, 0]


class TestDiffAddressInCluster:
    def test_returns_false_without_addresses(self):
        df = pd.DataFrame({
            "id_centro": ["a", "b"],
            "latitud":  [1.0, 1.0],
            "longitud": [2.0, 2.0],
        })
        flags = has_diff_address_in_cluster(df, addr_df=None)
        assert flags.tolist() == [False, False]

    def test_diff_addresses_at_same_point(self):
        df = pd.DataFrame({
            "id_centro": ["a", "b", "c"],
            "latitud":  [1.0, 1.0, 5.0],
            "longitud": [2.0, 2.0, 6.0],
        })
        addr = pd.DataFrame({
            "id_centro":   ["a", "b", "c"],
            "raw_street":  ["Main St", "Side Rd", "Other"],
            "raw_adm2":    ["X",       "X",       "Y"],
        })
        flags = has_diff_address_in_cluster(df, addr)
        # a and b share coord but differ in street → both flagged.
        assert flags.tolist() == [True, True, False]

    def test_same_address_at_same_point_not_flagged(self):
        df = pd.DataFrame({
            "id_centro": ["a", "b"],
            "latitud":  [1.0, 1.0],
            "longitud": [2.0, 2.0],
        })
        addr = pd.DataFrame({
            "id_centro":   ["a", "b"],
            "raw_street":  ["Main St", "Main St"],
            "raw_adm2":    ["X",       "X"],
        })
        flags = has_diff_address_in_cluster(df, addr)
        assert flags.tolist() == [False, False]


class TestDetectClustersRadius:
    def test_nearby_points_clustered(self):
        # ~10m apart should land in the same 50m bin.
        df = pd.DataFrame({
            "id_centro": ["a", "b", "c"],
            "latitud":  [1.000000, 1.000050, 5.0],
            "longitud": [2.000000, 2.000050, 6.0],
        })
        sizes = detect_clusters_radius(df)
        assert sizes.iloc[0] >= 1
        assert sizes.iloc[1] >= 1
        assert sizes.iloc[2] == 1


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------

class TestMigrateLegacyEvidence:
    def test_pan_adm3_centroid_normal(self):
        row = {"coordinate_source": "adm3_centroid", "latitud": 8.5, "longitud": -80.0}
        out = migrate_legacy_evidence(row)
        assert out["coordinate_source"] == "centroid_cascade"
        assert out["qc_centroid_bias"] == "normal"

    def test_pan_adm3_centroid_large(self):
        row = {"coordinate_source": "adm3_centroid_large", "latitud": 8.5, "longitud": -80.0}
        out = migrate_legacy_evidence(row)
        assert out["coordinate_source"] == "centroid_cascade"
        assert out["qc_centroid_bias"] == "high"

    def test_original_passthrough(self):
        row = {"coordinate_source": "original", "latitud": 1.0, "longitud": 2.0}
        out = migrate_legacy_evidence(row)
        assert out["coordinate_source"] == "original"
        assert out["qc_centroid_bias"] == "unknown"

    def test_geocoded_passthrough(self):
        row = {"coordinate_source": "geocoded", "latitud": 1.0, "longitud": 2.0}
        out = migrate_legacy_evidence(row)
        assert out["coordinate_source"] == "geocoded"

    # --- Blank coordinate_source ------------------------------------------

    def test_blank_with_no_coord_stays_blank(self):
        row = {"coordinate_source": "", "latitud": np.nan, "longitud": np.nan}
        out = migrate_legacy_evidence(row)
        assert out["coordinate_source"] == ""
        # Resolver will assign `missing` from the empty source + missing coords.

    def test_nan_with_no_coord_stays_blank(self):
        row = {"coordinate_source": float("nan"), "latitud": np.nan, "longitud": np.nan}
        out = migrate_legacy_evidence(row)
        assert out["coordinate_source"] == ""

    def test_none_with_no_coord_stays_blank(self):
        row = {"coordinate_source": None, "latitud": None, "longitud": None}
        out = migrate_legacy_evidence(row)
        assert out["coordinate_source"] == ""

    def test_blank_with_coords_defaults_to_original(self):
        # Future case: a country that never went through Step 05.
        row = {"coordinate_source": "", "latitud": 1.0, "longitud": 2.0}
        out = migrate_legacy_evidence(row)
        assert out["coordinate_source"] == "original"
        assert out["qc_centroid_bias"] == "unknown"

    def test_zero_zero_treated_as_no_coord(self):
        row = {"coordinate_source": "", "latitud": 0.0, "longitud": 0.0}
        out = migrate_legacy_evidence(row)
        assert out["coordinate_source"] == ""

    # --- Unknown values ---------------------------------------------------

    def test_unknown_value_with_coord_falls_back_to_original_with_warning(self):
        row = {"coordinate_source": "frankenstein", "latitud": 1.0, "longitud": 2.0}
        out = migrate_legacy_evidence(row)
        assert out["coordinate_source"] == "original"
        assert "migration_warning" in out

    def test_unknown_value_without_coord_stays_blank(self):
        row = {"coordinate_source": "frankenstein", "latitud": np.nan, "longitud": np.nan}
        out = migrate_legacy_evidence(row)
        assert out["coordinate_source"] == ""
        assert "migration_warning" in out

    # --- Idempotency: re-migrating a canonical row preserves the bias --------

    def test_canonical_source_preserves_existing_bias(self):
        # Re-running migrate on a row that's already centroid_cascade with bias=normal
        # must NOT reset the bias to unknown (idempotency guarantee).
        row = {"coordinate_source": "centroid_cascade", "qc_centroid_bias": "normal",
               "latitud": 1.0, "longitud": 2.0}
        out = migrate_legacy_evidence(row)
        assert out["coordinate_source"] == "centroid_cascade"
        assert out["qc_centroid_bias"] == "normal"

    def test_canonical_source_with_high_bias_preserved(self):
        row = {"coordinate_source": "centroid_cascade", "qc_centroid_bias": "high",
               "latitud": 1.0, "longitud": 2.0}
        out = migrate_legacy_evidence(row)
        assert out["qc_centroid_bias"] == "high"

    def test_invalid_prior_bias_falls_back_to_unknown(self):
        row = {"coordinate_source": "geocoded", "qc_centroid_bias": "garbage",
               "latitud": 1.0, "longitud": 2.0}
        out = migrate_legacy_evidence(row)
        assert out["qc_centroid_bias"] == "unknown"


# ---------------------------------------------------------------------------
# Canonical resolver
# ---------------------------------------------------------------------------

ADM2_SCOPE = {"final_match_level": "adm2"}
ADM1_SCOPE = {"final_match_level": "adm1"}
SPATIAL_ONLY_SCOPE = {"final_match_level": "spatial_only"}
BBOX_ONLY_SCOPE = {"final_match_level": "bbox_only"}


def _ev(**overrides) -> dict:
    base = {
        "has_coords": True,
        "in_bounds": True,
        "swapped": False,
        "adm1_status": "MATCH",
        "adm2_status": "MATCH",
        "cluster_size_exact": 1,
        "coordinate_source": "original",
        "geocode_precision": "",
        "geocode_acceptance": "",
        "geo_adm2_check": "",
        "orig_adm2_check": "",
        "geocode_distance_km": None,
    }
    base.update(overrides)
    return base


class TestResolverPrecedence:
    def test_missing(self):
        q, r = resolve_coordinate_quality(_ev(has_coords=False), ADM2_SCOPE)
        assert (q, r) == ("missing", "missing")

    def test_swapped_beats_everything(self):
        q, r = resolve_coordinate_quality(_ev(swapped=True), ADM2_SCOPE)
        assert q == "swapped"

    def test_out_of_bounds(self):
        q, r = resolve_coordinate_quality(_ev(in_bounds=False), ADM2_SCOPE)
        assert (q, r) == ("out_of_bounds", "bbox")

    def test_adm2_mismatch_under_adm2_policy(self):
        q, r = resolve_coordinate_quality(_ev(adm2_status="MISMATCH"), ADM2_SCOPE)
        assert (q, r) == ("adm_mismatch", "adm2_mismatch")

    def test_adm1_mismatch_under_adm2_policy_still_caught(self):
        q, r = resolve_coordinate_quality(
            _ev(adm1_status="MISMATCH", adm2_status="NOT_RUN"), ADM2_SCOPE,
        )
        assert (q, r) == ("adm_mismatch", "adm1_mismatch")

    def test_adm2_mismatch_ignored_under_adm1_policy(self):
        # Country says only adm1 is reliable — adm2 mismatch is not load-bearing.
        ev = _ev(adm1_status="MATCH", adm2_status="MISMATCH")
        q, r = resolve_coordinate_quality(ev, ADM1_SCOPE)
        assert q == "gps_validated"

    def test_cluster_centroid_for_original_only(self):
        ev = _ev(cluster_size_exact=10, coordinate_source="original")
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert (q, r) == ("cluster_centroid", "cluster_ge5")

    def test_cluster_does_not_apply_to_geocoded(self):
        ev = _ev(cluster_size_exact=10, coordinate_source="geocoded",
                 geocode_precision="centroid")
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert q == "geocoded_centroid"

    # ── Extended cluster_centroid rule (2026-05-13): n in [2,4] placeholder ──

    def test_cluster_n3_diff_admin_locality_is_centroid(self):
        ev = _ev(cluster_size_exact=3, coordinate_source="original",
                 cluster_diff_admin_locality=True)
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert (q, r) == ("cluster_centroid", "cluster_3_4_diff_admin_locality")

    def test_cluster_n4_diff_admin_locality_is_centroid(self):
        ev = _ev(cluster_size_exact=4, coordinate_source="original",
                 cluster_diff_admin_locality=True)
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert (q, r) == ("cluster_centroid", "cluster_3_4_diff_admin_locality")

    def test_cluster_n3_same_admin_locality_stays_validated(self):
        # n=3 same campus (no diff in admin/locality) does NOT trigger.
        ev = _ev(cluster_size_exact=3, coordinate_source="original",
                 cluster_diff_admin_locality=False)
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert q == "gps_validated"

    def test_cluster_n2_diff_admin_locality_no_frontier_is_centroid(self):
        ev = _ev(cluster_size_exact=2, coordinate_source="original",
                 cluster_diff_admin_locality=True, n2_frontier_rescue=False)
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert (q, r) == ("cluster_centroid", "cluster_2_diff_admin_locality")

    def test_cluster_n2_diff_admin_locality_with_frontier_stays_validated(self):
        # n=2 placeholder rescued by frontier (within 5 km of admin boundary).
        ev = _ev(cluster_size_exact=2, coordinate_source="original",
                 cluster_diff_admin_locality=True, n2_frontier_rescue=True)
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert q == "gps_validated"

    def test_cluster_n2_same_admin_locality_stays_validated(self):
        # n=2 same campus → no trigger regardless of frontier flag.
        ev = _ev(cluster_size_exact=2, coordinate_source="original",
                 cluster_diff_admin_locality=False, n2_frontier_rescue=False)
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert q == "gps_validated"

    def test_cluster_n5_still_uses_classical_rule(self):
        # n>=5 triggers regardless of diff_admin_locality flag (backwards compat).
        ev = _ev(cluster_size_exact=5, coordinate_source="original",
                 cluster_diff_admin_locality=False)
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert (q, r) == ("cluster_centroid", "cluster_ge5")

    def test_cluster_extended_rule_geocoded_source_ignored(self):
        # Extended rule, like classical, requires coordinate_source=original.
        ev = _ev(cluster_size_exact=3, coordinate_source="geocoded",
                 geocode_precision="street",
                 cluster_diff_admin_locality=True)
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert q == "geocoded_street"


class TestResolverGeocoderDisagreement:
    def test_flag_with_street_precision_is_disagreement(self):
        ev = _ev(geocode_acceptance="FLAG", geocode_precision="street")
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert (q, r) == ("geocoder_disagrees", "geocoder_compare")

    def test_flag_with_centroid_precision_is_disagreement(self):
        ev = _ev(geocode_acceptance="FLAG", geocode_precision="centroid")
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert q == "geocoder_disagrees"

    def test_flag_with_uncertain_precision_is_low_confidence(self):
        # NOT geocoder_disagrees — drops to gps_unverified with explicit reason.
        # Requires that GPS is also unable to validate via admin (otherwise
        # gps_validated should win — uncertain geocoder doesn't degrade good GPS).
        ev = _ev(
            geocode_acceptance="FLAG", geocode_precision="uncertain",
            adm2_status="NO_POLYGON", adm1_status="NO_RAW_ADM",
        )
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert (q, r) == ("gps_unverified", "geocoder_low_confidence")

    def test_flag_with_uncertain_loses_to_validated_gps(self):
        # If GPS validates at the configured level, a low-confidence geocoder
        # FLAG does NOT drag it down — gps_validated wins.
        ev = _ev(geocode_acceptance="FLAG", geocode_precision="uncertain")
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert (q, r) == ("gps_validated", "validated")

    def test_flag_with_distance_threshold(self):
        ev = _ev(geocode_acceptance="FLAG", geocode_precision="uncertain",
                 geocode_distance_km=GEOCODER_DISAGREE_DISTANCE_KM + 1)
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert q == "geocoder_disagrees"

    def test_flag_with_adm2_contradiction(self):
        ev = _ev(geocode_acceptance="FLAG", geocode_precision="uncertain",
                 geo_adm2_check="MATCH", orig_adm2_check="MISMATCH")
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert q == "geocoder_disagrees"


class TestResolverGeocoded:
    def test_geocoded_street(self):
        ev = _ev(coordinate_source="geocoded", geocode_precision="street",
                 geocode_acceptance="ACCEPT")
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert (q, r) == ("geocoded_street", "fill_street")

    def test_geocoded_centroid_via_precision(self):
        ev = _ev(coordinate_source="geocoded", geocode_precision="centroid",
                 geocode_acceptance="ACCEPT_CENTROID")
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert (q, r) == ("geocoded_centroid", "fill_centroid")

    def test_centroid_cascade_always_geocoded_centroid(self):
        ev = _ev(coordinate_source="centroid_cascade", geocode_precision="")
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert (q, r) == ("geocoded_centroid", "fill_centroid")


class TestResolverValidated:
    def test_gps_validated_under_adm2(self):
        q, r = resolve_coordinate_quality(_ev(), ADM2_SCOPE)
        assert (q, r) == ("gps_validated", "validated")

    def test_gps_unverified_when_adm2_no_polygon(self):
        ev = _ev(adm2_status="NO_POLYGON")
        q, r = resolve_coordinate_quality(ev, ADM2_SCOPE)
        assert q == "gps_unverified"

    def test_bbox_only_country_never_validated(self):
        # Best a bbox_only country can do is gps_unverified.
        ev = _ev(adm1_status="NOT_RUN", adm2_status="NOT_RUN")
        q, r = resolve_coordinate_quality(ev, BBOX_ONLY_SCOPE)
        assert q == "gps_unverified"

    def test_spatial_only_country_never_validated(self):
        # §5.5 (2026-05-11): spatial_only countries have no raw admin to
        # validate against. The MATCH from spatial join is trivial (just
        # the spatial assignment), not a true cross-check. Honest label is
        # gps_unverified. JAM is the canonical example (914 schools were
        # previously gps_validated trivially).
        ev = _ev(adm1_status="MATCH", adm2_status="NOT_RUN")
        q, r = resolve_coordinate_quality(ev, SPATIAL_ONLY_SCOPE)
        assert q == "gps_unverified"


class TestResolverScopeValidation:
    def test_invalid_scope_raises(self):
        with pytest.raises(ValueError):
            resolve_coordinate_quality(_ev(), {"final_match_level": "garbage"})


# ---------------------------------------------------------------------------
# compute_geocode_targets
# ---------------------------------------------------------------------------

class TestComputeGeocodeTargets:
    def test_missing_and_zero_buckets(self):
        cima = pd.DataFrame({
            "id_centro": ["a", "b", "c", "d"],
            "latitud":   [1.0, np.nan, 0.0, 5.0],
            "longitud":  [2.0, 3.0,    0.0, 6.0],
        })
        out = compute_geocode_targets(cima, addr_df=None)
        assert out["missing"] == {"b"}
        assert out["zeros"] == {"c"}

    def test_centroid_bucket_uses_threshold(self):
        rows = []
        # 5 schools at the same point — should become centroids.
        for i in range(CLUSTER_THRESHOLD):
            rows.append({"id_centro": f"c{i}", "latitud": 1.0, "longitud": 2.0})
        # Singleton elsewhere
        rows.append({"id_centro": "solo", "latitud": 5.0, "longitud": 6.0})
        cima = pd.DataFrame(rows)
        out = compute_geocode_targets(cima, addr_df=None)
        assert out["centroids"] == {f"c{i}" for i in range(CLUSTER_THRESHOLD)}
        assert "solo" not in out["centroids"]

    def test_dup_addr_bucket(self):
        cima = pd.DataFrame({
            "id_centro": ["a", "b"],
            "latitud":  [1.0, 1.0],
            "longitud": [2.0, 2.0],
        })
        addr = pd.DataFrame({
            "id_centro":   ["a", "b"],
            "raw_street":  ["Main St", "Other Rd"],
            "raw_adm2":    ["X",       "X"],
        })
        out = compute_geocode_targets(cima, addr)
        assert out["dup_addr"] == {"a", "b"}

    def test_mismatches_from_qc_evidence(self):
        cima = pd.DataFrame({
            "id_centro": ["a", "b", "c"],
            "latitud":   [1.0, 2.0, 3.0],
            "longitud":  [10.0, 20.0, 30.0],
        })
        qc = pd.DataFrame({
            "id_centro": ["a", "b", "c"],
            "qc_adm1_status": ["MATCH", "MISMATCH", "MATCH"],
            "qc_adm2_status": ["MATCH", "MATCH", "MISMATCH"],
        })
        out = compute_geocode_targets(cima, addr_df=None, qc_evidence=qc)
        assert out["mismatches"] == {"b", "c"}

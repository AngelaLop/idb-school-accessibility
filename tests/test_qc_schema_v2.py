"""Contract tests for the schema v2 enums and final_match_level policy.

Locks the answers given by the user on 2026-04-25 for the QC unification work.
If any of these tests fails, the change must be intentional and explicitly
re-approved before propagating to qc_core / Step 02 finalize.
"""

from pipeline.constants import (
    CIMA_ENRICHED_COLUMNS,
    COORDINATE_QUALITY,
    COORDINATE_QUALITY_PRECEDENCE,
    COORDINATE_QUALITY_REASONS,
    COORDINATE_QUALITY_REASON_VALUES,
    COORDINATE_QUALITY_VALUES_V2,
    COORDINATE_SOURCES,
    COUNTRY_SCOPE,
    FINAL_MATCH_LEVELS,
    LEGACY_COORDINATE_SOURCE_MIGRATION,
    QC_ADM_STATUSES,
    QC_CENTROID_BIASES,
    QC_EVIDENCE_VERSION,
    QC_MATCH_LEVELS,
    QC_SCOPE_CLASS,
    QC_SCOPE_CLASS_VALUES,
    SCHEMA,
)


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

def test_qc_evidence_version_is_2():
    assert QC_EVIDENCE_VERSION == 2


# ---------------------------------------------------------------------------
# coordinate_quality enum
# ---------------------------------------------------------------------------

def test_coordinate_quality_enum_locked():
    expected = {
        "missing", "out_of_bounds", "swapped", "adm_mismatch",
        "cluster_centroid", "geocoder_disagrees", "boundary_zone",
        "geocoded_centroid", "geocoded_street",
        "gps_validated", "gps_unverified",
    }
    assert set(COORDINATE_QUALITY) == expected
    assert COORDINATE_QUALITY_VALUES_V2 == frozenset(expected)


def test_precedence_covers_every_quality_value():
    assert set(COORDINATE_QUALITY_PRECEDENCE) == set(COORDINATE_QUALITY)
    assert len(COORDINATE_QUALITY_PRECEDENCE) == len(COORDINATE_QUALITY)


def test_precedence_worst_first():
    # Worst flag wins — these must come before the validated/unverified buckets.
    bad = ("missing", "out_of_bounds", "swapped", "adm_mismatch",
           "cluster_centroid", "geocoder_disagrees")
    good_tail = ("gps_validated", "gps_unverified")
    for b in bad:
        for g in good_tail:
            assert COORDINATE_QUALITY_PRECEDENCE.index(b) < COORDINATE_QUALITY_PRECEDENCE.index(g)


# ---------------------------------------------------------------------------
# coordinate_quality_reason enum
# ---------------------------------------------------------------------------

def test_reason_enum_locked():
    expected = {
        "bbox", "swapped", "adm2_mismatch", "adm1_mismatch",
        "cluster_ge5", "geocoder_compare", "boundary_zone_<5km",
        "geocoder_low_confidence", "fill_centroid", "fill_street",
        "validated", "default_unverified", "missing",
    }
    assert set(COORDINATE_QUALITY_REASONS) == expected
    assert COORDINATE_QUALITY_REASON_VALUES == frozenset(expected)


def test_reason_includes_geocoder_low_confidence():
    # Explicit guard: distinguishes uncertain-geocoder from default unverified.
    assert "geocoder_low_confidence" in COORDINATE_QUALITY_REASONS


# ---------------------------------------------------------------------------
# coordinate_source enum (canonicalized)
# ---------------------------------------------------------------------------

def test_coordinate_source_canonicalized():
    assert COORDINATE_SOURCES == frozenset({"original", "geocoded", "centroid_cascade"})


# ---------------------------------------------------------------------------
# qc_centroid_bias / qc_match_level / qc_adm_status
# ---------------------------------------------------------------------------

def test_centroid_bias_enum():
    assert QC_CENTROID_BIASES == frozenset({"normal", "high", "unknown"})


def test_match_level_enum():
    assert QC_MATCH_LEVELS == frozenset({"ADM2", "ADM1", "SPATIAL_ONLY", "NONE"})


def test_adm_status_enum():
    assert QC_ADM_STATUSES == frozenset({
        "MATCH", "MISMATCH", "NO_RAW_ADM", "NO_POLYGON", "NO_DATA", "NOT_RUN",
    })


def test_scope_class_enum():
    expected = {
        "missing",
        "inside_mainland_bbox",
        "remote_territory_or_island",
        "near_border_review",
        "outside_country",
        "invalid_numeric",
    }
    assert set(QC_SCOPE_CLASS) == expected
    assert QC_SCOPE_CLASS_VALUES == frozenset(expected)


# ---------------------------------------------------------------------------
# Legacy migration map
# ---------------------------------------------------------------------------

def test_legacy_coordinate_source_migration_covers_pan_cascade():
    for legacy in ("adm3_centroid", "adm3_centroid_large"):
        assert legacy in LEGACY_COORDINATE_SOURCE_MIGRATION
        mapped = LEGACY_COORDINATE_SOURCE_MIGRATION[legacy]
        assert mapped["coordinate_source"] == "centroid_cascade"
    assert LEGACY_COORDINATE_SOURCE_MIGRATION["adm3_centroid"]["qc_centroid_bias"] == "normal"
    assert LEGACY_COORDINATE_SOURCE_MIGRATION["adm3_centroid_large"]["qc_centroid_bias"] == "high"


def test_legacy_migration_targets_only_canonical_sources():
    for legacy, mapped in LEGACY_COORDINATE_SOURCE_MIGRATION.items():
        assert mapped["coordinate_source"] in COORDINATE_SOURCES
        assert mapped["qc_centroid_bias"] in QC_CENTROID_BIASES


# ---------------------------------------------------------------------------
# CIMA enriched column contract
# ---------------------------------------------------------------------------

def test_cima_enriched_columns_includes_base_schema():
    for col in SCHEMA:
        assert col in CIMA_ENRICHED_COLUMNS


def test_cima_enriched_columns_includes_v2_derived():
    for col in (
        "adm1_pcode", "adm2_pcode",
        "qc_in_bounds", "qc_scope_class", "include_in_spatial_indicators", "qc_swapped",
        "qc_adm1_status", "qc_adm2_status", "qc_match_level",
        "qc_cluster_size_exact", "qc_cluster_diff_addr_exact",
        "qc_cluster_size_50m", "qc_cluster_diff_addr_50m",
        "coordinate_source", "coordinate_quality", "coordinate_quality_reason",
        "qc_centroid_bias", "qc_evidence_version",
    ):
        assert col in CIMA_ENRICHED_COLUMNS, f"missing canonical column: {col}"


def test_cima_enriched_columns_unique():
    assert len(CIMA_ENRICHED_COLUMNS) == len(set(CIMA_ENRICHED_COLUMNS))


# ---------------------------------------------------------------------------
# final_match_level policy locked on 2026-04-25
# ---------------------------------------------------------------------------

EXPECTED_FINAL_MATCH_LEVEL: dict[str, str] = {
    # adm2 (15 countries — ECU promoted 2026-05 once Cod_Cantón → EC{:04d}
    # code-based match landed; 111 cantón mismatches now route to step-04)
    "ARG": "adm2", "BRA": "adm2", "CHL": "adm2", "COL": "adm2",
    "CRI": "adm2", "DOM": "adm2", "ECU": "adm2", "GTM": "adm2", "HND": "adm2",
    "MEX": "adm2", "PAN": "adm2", "PER": "adm2", "PRY": "adm2", "SLV": "adm2",
    "SUR": "adm2",
    # adm1 (5 countries — BHS promoted 2026-05-13 at onboarding:
    # ADM1_AGGREGATIONS["BHS"] maps raw island families to BID polygons)
    "BHS": "adm1", "BLZ": "adm1", "BOL": "adm1", "GUY": "adm1", "URY": "adm1",
    # spatial_only (1 country)
    "JAM": "spatial_only",
    # bbox_only (2 countries)
    "BRB": "bbox_only", "HTI": "bbox_only",
}


def test_final_match_level_values_are_in_enum():
    for iso, scope in COUNTRY_SCOPE.items():
        assert scope["final_match_level"] in FINAL_MATCH_LEVELS, (
            f"{iso}: {scope['final_match_level']} not in {FINAL_MATCH_LEVELS}"
        )


def test_final_match_level_per_country_locked():
    # Locks the user's 2026-04-25 decision. Changing any of these requires
    # explicit re-approval (and a corresponding update to the spec doc).
    for iso, expected in EXPECTED_FINAL_MATCH_LEVEL.items():
        actual = COUNTRY_SCOPE[iso]["final_match_level"]
        assert actual == expected, f"{iso}: expected {expected}, got {actual}"


def test_final_match_level_country_count_by_bucket():
    counts: dict[str, int] = {}
    for scope in COUNTRY_SCOPE.values():
        counts[scope["final_match_level"]] = counts.get(scope["final_match_level"], 0) + 1
    assert counts == {"adm2": 15, "adm1": 5, "spatial_only": 1, "bbox_only": 2}


# ---------------------------------------------------------------------------
# validation_tier consistency (limited tier reserved for explicit cases)
# ---------------------------------------------------------------------------

def test_limited_validation_set():
    limited = {iso for iso, s in COUNTRY_SCOPE.items() if s["validation_tier"] == "limited"}
    # BRB, JAM and BHS (onboarded 2026-05-13, n=138) are the analysis-included
    # countries with limited validation.
    assert {"BHS", "BRB", "JAM"}.issubset(limited)


def test_not_ready_set_matches_excluded():
    not_ready = {iso for iso, s in COUNTRY_SCOPE.items() if s["validation_tier"] == "not_ready"}
    excluded = {iso for iso, s in COUNTRY_SCOPE.items() if not s["analysis_included"]}
    # BHS moved out of this set at onboarding (2026-05-13); HTI remains
    # excluded (raw .xls unreadable).
    assert not_ready == excluded == {"HTI"}


# ---------------------------------------------------------------------------
# indicator_levels — published aggregation ladder (separate from QC level)
# ---------------------------------------------------------------------------

def test_indicator_levels_for_analysis_countries():
    expected = ("adm0", "adm1", "adm2")
    for iso, scope in COUNTRY_SCOPE.items():
        if scope["analysis_included"]:
            assert scope["indicator_levels"] == expected, (
                f"{iso}: expected {expected}, got {scope['indicator_levels']}"
            )


def test_indicator_levels_empty_for_excluded_countries():
    for iso, scope in COUNTRY_SCOPE.items():
        if not scope["analysis_included"]:
            assert scope["indicator_levels"] == (), (
                f"{iso}: excluded country must have indicator_levels=(), "
                f"got {scope['indicator_levels']}"
            )


def test_indicator_levels_independent_from_final_match_level():
    # JAM publishes ADM0/1/2 indicators even though its QC is spatial_only —
    # validation level and publication level are separate concerns.
    jam = COUNTRY_SCOPE["JAM"]
    assert jam["analysis_included"] is True
    assert jam["final_match_level"] == "spatial_only"
    assert jam["indicator_levels"] == ("adm0", "adm1", "adm2")

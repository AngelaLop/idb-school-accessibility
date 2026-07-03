"""Contract tests for operational vs analytical country scope."""

from pipeline.constants import (
    ALL_ISOS,
    ANALYSIS_EXCLUDED_ISOS,
    ANALYSIS_ISOS,
    COUNTRY_SCOPE,
    PIPELINE_ISOS,
)


def test_operational_and_analysis_scope_counts():
    assert len(PIPELINE_ISOS) == 23
    # 2026-05-13: BHS onboarded after the country specialist delivered an
    # authoritative MoE xlsx (161 schools, 138 K-12, 91% gps_validated after
    # snap-to-nearest). HTI remains excluded — raw is unreadable .xls.
    assert len(ANALYSIS_ISOS) == 22
    assert set(ANALYSIS_EXCLUDED_ISOS) == {"HTI"}


def test_backward_compatibility_alias_tracks_analysis_scope():
    assert ALL_ISOS == ANALYSIS_ISOS


def test_hti_remains_operational_but_not_counted():
    scope = COUNTRY_SCOPE["HTI"]
    assert scope["pipeline_enabled"] is True
    assert scope["analysis_included"] is False


def test_country_scope_has_required_keys():
    required = {
        "pipeline_enabled",
        "analysis_included",
        "final_match_level",
        "indicator_levels",
        "validation_tier",
        "data_status",
    }
    for iso, scope in COUNTRY_SCOPE.items():
        assert required.issubset(scope), f"{iso}: missing scope keys"

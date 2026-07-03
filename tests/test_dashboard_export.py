from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.export_dashboard_data import (
    build_country_summary,
    build_geocoding_country_summary,
    build_geocoding_overview,
    build_source_checks,
)


def test_build_country_summary_derives_raw_georef_and_step01_status(tmp_path: Path):
    base = tmp_path / "data" / "schools" / "AR"
    iso = "TST"
    processed = base / iso / "processed"
    processed.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(
        [
            {
                "id_centro": "1",
                "sector": "Public",
                "latitud": 1.0,
                "longitud": -1.0,
                "coordinate_source": "original",
                "coordinate_quality": "gps_validated",
            },
            {
                "id_centro": "2",
                "sector": "Private",
                "latitud": 2.0,
                "longitud": -2.0,
                "coordinate_source": "geocoded",
                "coordinate_quality": "geocoded_street",
            },
            {
                "id_centro": "3",
                "sector": "Public",
                "latitud": 3.0,
                "longitud": -3.0,
                "coordinate_source": "original",
                "coordinate_quality": "adm_mismatch",
            },
            {
                "id_centro": "4",
                "sector": "Unknown",
                "latitud": None,
                "longitud": None,
                "coordinate_source": "",
                "coordinate_quality": "missing",
            },
        ]
    ).to_csv(processed / f"{iso}_total_cima.csv", index=False, encoding="utf-8")

    coverage_df = pd.DataFrame(
        [
            {
                "country_iso": iso,
                "data_year": "2024",
                "sector_scope": "public",
                "public_universe": 10,
                "total_universe_est": 12,
                "pct_coverage_vs_public": 40.0,
                "pct_coverage_vs_total": 33.3,
            }
        ]
    )
    trace_df = pd.DataFrame(
        [
            {
                "iso": iso,
                "trace_bucket": "internal_status_policy_review",
                "needs_source_owner_followup": False,
                "trace_note": "Closure explained by internal status policy.",
                "legacy_iso_total": 9,
                "legacy_iso_georef": 8,
                "legacy_iso_k12_public_total": 8,
                "delta_dashboard_vs_legacy_total": -5,
                "delta_public_vs_legacy_total": -4,
                "delta_public_k12_vs_legacy_k12_total": -3,
            }
        ]
    )
    scope = {
        iso: {
            "pipeline_enabled": True,
            "analysis_included": True,
            "final_match_level": "adm2",
            "validation_tier": "standard",
            "data_status": "ready",
        }
    }

    out = build_country_summary(base, scope, coverage_df, trace_df, isos=[iso])
    row = out.iloc[0]

    assert row["current_total"] == 4
    assert row["current_public"] == 2
    assert row["current_private"] == 1
    assert row["current_unknown"] == 1
    assert row["n_georef_current"] == 3
    assert row["n_georef_raw"] == 2
    assert row["n_gap_filled"] == 1
    assert row["q_gps_validated"] == 1
    assert row["q_geocoded_street"] == 1
    assert row["q_adm_mismatch"] == 1
    assert row["q_missing"] == 1
    assert row["qg_flag_ambiguo"] == 1
    assert row["step01_reconciliation_status"] == "methodological_difference"
    assert row["step01_root_cause"] == "status_policy_difference"
    assert row["step01_justification_text"] == "Closure explained by internal status policy."


def test_build_source_checks_flags_stale_summary_files():
    country_df = pd.DataFrame(
        [
            {
                "iso": "TST",
                "current_total": 4,
                "n_georef_current": 3,
                "q_missing": 1,
                "q_gps_validated": 1,
                "q_adm_mismatch": 1,
                "q_geocoded_street": 1,
                "q_geocoded_centroid": 0,
                "q_cluster_centroid": 0,
                "q_gps_unverified": 0,
                "q_geocoder_disagrees": 0,
                "q_out_of_bounds": 0,
                "q_swapped": 0,
            }
        ]
    )
    coverage_df = pd.DataFrame(
        [{"country_iso": "TST", "n_schools_in_file": 4, "n_georef": 3}]
    )
    published_summary_df = pd.DataFrame(
        [{"iso": "TST", "total_k12": 5, "georef": 2}]
    )
    finalize_summary_df = pd.DataFrame(
        [{"iso": "TST", "total": 4, "q_gps_validated": 0, "q_missing": 1}]
    )

    checks = build_source_checks(country_df, coverage_df, published_summary_df, finalize_summary_df)

    coverage_row = checks[checks["dataset"] == "school_coverage_assessment"].iloc[0]
    summary_row = checks[checks["dataset"] == "cima_v2_summary"].iloc[0]
    finalize_row = checks[checks["dataset"] == "qc_finalize_summary"].iloc[0]

    assert bool(coverage_row["any_mismatch"]) is False
    assert bool(summary_row["any_mismatch"]) is True
    assert "total_k12" in summary_row["mismatch_fields"]
    assert "georef" in summary_row["mismatch_fields"]
    assert bool(finalize_row["any_mismatch"]) is True
    assert "q_gps_validated" in finalize_row["mismatch_fields"]


def test_build_geocoding_views_summarize_compare_fill_and_ground_truth():
    geocode_df = pd.DataFrame(
        [
            {"iso": "AAA", "target_type": "compare", "acceptance": "KEEP_ORIGINAL", "geocode_precision": "street"},
            {"iso": "AAA", "target_type": "compare", "acceptance": "FLAG", "geocode_precision": "uncertain"},
            {"iso": "AAA", "target_type": "fill", "acceptance": "ACCEPT", "geocode_precision": "street"},
            {"iso": "AAA", "target_type": "fill", "acceptance": "REJECT", "geocode_precision": "uncertain"},
            {"iso": "BBB", "target_type": "fill", "acceptance": "ACCEPT_CENTROID", "geocode_precision": "centroid"},
            {"iso": "BBB", "target_type": "fill", "acceptance": "", "geocode_precision": "centroid"},
        ]
    )
    ground_truth_df = pd.DataFrame(
        [
            {"iso": "AAA", "n": 50, "score_median": 94.0, "dist_median": 1.2, "pct_lt5km": 70.0},
            {"iso": "BBB", "n": 50, "score_median": 88.0, "dist_median": 3.5, "pct_lt5km": 60.0},
        ]
    )

    overview = build_geocoding_overview(geocode_df, total_schools=100)
    country = build_geocoding_country_summary(geocode_df, ground_truth_df)

    assert overview["geocoder_universe_total"] == 6
    assert overview["outside_geocoder_universe"] == 94
    assert overview["compare_universe"] == 2
    assert overview["fill_universe"] == 4
    assert overview["compare_keep_original"] == 1
    assert overview["compare_flag"] == 1
    assert overview["fill_accept"] == 2
    assert overview["fill_accept_street"] == 1
    assert overview["fill_accept_centroid"] == 1
    assert overview["fill_reject"] == 1
    assert overview["fill_pending"] == 1

    aaa = country[country["iso"] == "AAA"].iloc[0]
    bbb = country[country["iso"] == "BBB"].iloc[0]

    assert aaa["processed_total"] == 4
    assert aaa["compare_total"] == 2
    assert aaa["fill_total"] == 2
    assert aaa["precision_street"] == 2
    assert aaa["precision_uncertain"] == 2
    assert aaa["compare_keep_original"] == 1
    assert aaa["fill_accept_street"] == 1
    assert aaa["fill_reject"] == 1
    assert aaa["gt_dist_median"] == 1.2
    assert aaa["gt_pct_lt_5km"] == 70.0

    assert bbb["processed_total"] == 2
    assert bbb["precision_centroid"] == 2
    assert bbb["fill_accept_centroid"] == 1
    assert bbb["fill_pending"] == 1

"""Build canonical dashboard exports from current pipeline outputs.

This script creates a local SQLite database plus JSON/CSV payloads for the
dashboard. It is intentionally conservative about source-of-truth:

1. Current per-country `*_total_cima.csv` files are canonical for school counts
   and coordinate-quality counts.
2. `results/school_coverage_assessment.csv` provides official-universe context.
3. `results/QC/dashboard_total_trace.csv` provides Step 01 reconciliation notes.
4. Legacy summary files such as `results/cima_v2_summary.csv` and
   `results/qc_finalize_summary.csv` are treated as diagnostics only and checked
   for drift against the current CIMA files.

Usage:
    uv run python pipeline/export_dashboard_data.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from pipeline.constants import (
        ANALYSIS_EXCLUDED_ISOS,
        ANALYSIS_ISOS,
        COORDINATE_QUALITY,
        COUNTRY_SCOPE,
        PIPELINE_ISOS,
    )
    from pipeline.country_info_levels import build_country_info_levels
except ImportError:
    from constants import (  # type: ignore[no-redef]
        ANALYSIS_EXCLUDED_ISOS,
        ANALYSIS_ISOS,
        COORDINATE_QUALITY,
        COUNTRY_SCOPE,
        PIPELINE_ISOS,
    )
    from country_info_levels import build_country_info_levels  # type: ignore[no-redef]


BASE = Path("data/schools/AR")
RESULTS = Path("results")
OUT_DIR = RESULTS / "dashboard"
ADM1_BOUNDARY_PATH = Path("data/bounderys/LAC/level 1/lac-level-1.shp")

COVERAGE_PATH = RESULTS / "school_coverage_assessment.csv"
PUBLISHED_SUMMARY_PATH = RESULTS / "cima_v2_summary.csv"
FINALIZE_SUMMARY_PATH = RESULTS / "qc_finalize_summary.csv"
STEP01_TRACE_PATH = RESULTS / "QC" / "dashboard_total_trace.csv"
GEOCODE_RESULTS_PATH = RESULTS / "geocode_results.csv"
GROUND_TRUTH_SUMMARY_PATH = RESULTS / "geocoder_ground_truth_summary.csv"
GROUND_TRUTH_SCORE_ANALYSIS_PATH = RESULTS / "geocoder_ground_truth_score_analysis.csv"
GEOCODE_TARGETS_PATH = OUT_DIR / "dashboard_geocode_targets.csv"
STEP03_CANDIDATES_PATH = OUT_DIR / "dashboard_step03_candidates.csv"
QC_BASELINE_PATH = OUT_DIR / "dashboard_qc_baseline.csv"
LAC_K12_CLEAN_PATH = Path("data/schools/AR/LAC_schools_k12_clean.csv")
POP_VALIDATION_PATH = Path("data/population/WorldPop/processed/_validation_2023.csv")
RWI_VS_POVERTY_COL_PATH = Path("results/exploratory/rwi_vs_poverty/COL_rwi_vs_poverty_merged.csv")
RWI_SCHOOLS_COL_PATH = Path("results/exploratory/rwi_vs_poverty/COL_schools_rwi.csv")
RWI_RAW_COL_PATH = Path("data/Poverty Rates/meta-rwi/COL/col_relative_wealth_index.csv")
RWI_EXPLORATORY_DIR = Path("results/exploratory/rwi_vs_poverty")
RWI_DATA_DIR = Path("data/Poverty Rates/meta-rwi")
ADM2_BOUNDARY_PATH = Path("data/bounderys/LAC/level 2/lac-level-2.shp")

# Bogota bbox (CO11001 = D.C.) for RWI cell + schools filtering on the dashboard map
BOGOTA_BBOX = (-74.45, 3.73, -73.99, 4.84)  # (min_lon, min_lat, max_lon, max_lat)

QUALITY_KEYS: tuple[str, ...] = tuple(COORDINATE_QUALITY.keys())
QUALITY_GROUPS: dict[str, tuple[str, ...]] = {
    "gps_preciso": ("gps_validated",),
    "gps_pendiente_validacion": ("gps_unverified",),
    "centroide_detectado": ("cluster_centroid", "geocoded_centroid"),
    "limite_administrativo": ("boundary_zone",),
    "flag_ambiguo": ("adm_mismatch", "geocoder_disagrees", "out_of_bounds", "swapped"),
    "street_geocoder": ("geocoded_street",),
    "sin_coordenadas": ("missing",),
}
FILL_ACCEPTANCE_STATUSES: tuple[str, ...] = ("ACCEPT", "ACCEPT_CENTROID", "ACCEPT_WITH_FLAG")

STEP01_STATUS_DEFINITIONS: dict[str, dict[str, str]] = {
    "match": {
        "label_es": "Match",
        "description_es": "La base actual y el baseline legacy cierran sin brechas relevantes.",
    },
    "resolved_internal": {
        "label_es": "Resuelto internamente",
        "description_es": "La diferencia principal ya fue explicada o corregida dentro del pipeline actual.",
    },
    "legacy_output_bug": {
        "label_es": "Bug legacy",
        "description_es": "La diferencia proviene de un bug o limitacion conocida del output legacy.",
    },
    "methodological_difference": {
        "label_es": "Diferencia metodologica",
        "description_es": "La brecha responde a una diferencia de alcance, filtro o politica metodologica.",
    },
    "source_difference": {
        "label_es": "Diferencia de fuente",
        "description_es": "Mismo metodo de calculo, pero el raw que CIMA procesa es una version distinta (ano academico mas reciente o actualizacion del archivo).",
    },
    "source_review_needed": {
        "label_es": "Requiere revision con fuente",
        "description_es": "Queda una duda que amerita confirmacion con el responsable de la base o la fuente original.",
    },
    "baseline_limited": {
        "label_es": "Base legacy limitada",
        "description_es": "No existe un baseline legacy comparable o la base legacy no permite una comparacion completa.",
    },
    "needs_review": {
        "label_es": "Revisar",
        "description_es": "Caso no clasificado automaticamente; requiere revision manual.",
    },
}

STEP01_ROOT_CAUSE_DEFINITIONS: dict[str, dict[str, str]] = {
    "legacy_scope_exclusion": {
        "label_es": "Alcance legacy distinto",
        "description_es": "El baseline legacy incluye o excluye universos que hoy se tratan distinto.",
    },
    "private_inclusion": {
        "label_es": "Inclusion de privadas",
        "description_es": "El total actual incorpora privadas que el baseline legacy no incorporaba.",
    },
    "source_year_mismatch": {
        "label_es": "Fuente o anio distinto",
        "description_es": "La diferencia parece explicarse por cambio de fuente, anio o ambos.",
    },
    "internal_ingestion": {
        "label_es": "Ingestion interna",
        "description_es": "La discrepancia principal vino de un problema de parseo o join interno ya corregido.",
    },
    "status_policy_difference": {
        "label_es": "Politica de estado",
        "description_es": "La brecha responde a una politica distinta sobre escuelas activas, cerradas o temporales.",
    },
    "prebasic_exclusion_policy": {
        "label_es": "Exclusion de prebasica",
        "description_es": "La diferencia viene de excluir prebasica del universo K-12 actual.",
    },
    "unit_of_analysis_difference": {
        "label_es": "Unidad de analisis distinta",
        "description_es": "Legacy y CIMA actual no cuentan la misma unidad institucional o fisica.",
    },
    "aligned_to_legacy": {
        "label_es": "Alineado al legado",
        "description_es": "CIMA replica explicitamente las decisiones metodologicas de la primera version de la base. Match exacto en publicas K-12.",
    },
    "internal_mapping_fix": {
        "label_es": "Fix interno de mapeo",
        "description_es": "La discrepancia se resolvio al alinear un mapping interno con la logica legacy.",
    },
    "internal_id_fix": {
        "label_es": "Fix interno de identificador",
        "description_es": "La discrepancia se resolvio al corregir el identificador usado por Step 01.",
    },
    "legacy_level_bug": {
        "label_es": "Bug legacy de niveles",
        "description_es": "El baseline legacy tenia un bug en la construccion de niveles educativos.",
    },
    "legacy_private_filter_bug": {
        "label_es": "Filtro legacy limitado",
        "description_es": "El baseline legacy tenia un filtro sectorial o de niveles que limita la comparacion.",
    },
    "legacy_missing_levels": {
        "label_es": "Legacy sin niveles comparables",
        "description_es": "La base legacy no trae columnas de nivel comparables para este pais.",
    },
    "legacy_only_source": {
        "label_es": "Sin raw alternativo",
        "description_es": "La base actual depende del output legacy porque no hay pipeline raw independiente.",
    },
    "baseline_missing": {
        "label_es": "Sin baseline legacy",
        "description_es": "No existe un baseline legacy usable para este pais.",
    },
    "unmapped": {
        "label_es": "Sin clasificar",
        "description_es": "Causa no clasificada automaticamente.",
    },
}

TRACE_BUCKET_MAP: dict[str, dict[str, str]] = {
    "initial_scope_exclusion": {
        "status": "methodological_difference",
        "root_cause": "legacy_scope_exclusion",
    },
    "initial_exclusion_plus_private_addition": {
        "status": "methodological_difference",
        "root_cause": "legacy_scope_exclusion",
    },
    "private_addition": {
        "status": "methodological_difference",
        "root_cause": "private_inclusion",
    },
    "private_addition_or_scope_shift": {
        "status": "methodological_difference",
        "root_cause": "private_inclusion",
    },
    "k12_filter_exclusion": {
        "status": "methodological_difference",
        "root_cause": "legacy_scope_exclusion",
    },
    "prebasic_exclusion": {
        "status": "methodological_difference",
        "root_cause": "prebasic_exclusion_policy",
    },
    "internal_status_policy_review": {
        "status": "methodological_difference",
        "root_cause": "status_policy_difference",
    },
    "legacy_private_filter_bug": {
        "status": "legacy_output_bug",
        "root_cause": "legacy_private_filter_bug",
    },
    "legacy_private_filter_and_level_limit": {
        "status": "source_review_needed",
        "root_cause": "legacy_private_filter_bug",
    },
    "legacy_level_bug": {
        "status": "legacy_output_bug",
        "root_cause": "legacy_level_bug",
    },
    "scope_and_year_mismatch": {
        "status": "source_review_needed",
        "root_cause": "source_year_mismatch",
    },
    "aligned_to_legacy": {
        "status": "match",
        "root_cause": "aligned_to_legacy",
    },
    "year_mismatch": {
        "status": "source_difference",
        "root_cause": "source_year_mismatch",
    },
    "unit_of_analysis_difference": {
        "status": "methodological_difference",
        "root_cause": "unit_of_analysis_difference",
    },
    "internal_ingestion_fix_mostly_resolved": {
        "status": "resolved_internal",
        "root_cause": "internal_ingestion",
    },
    "internal_key_reconciliation": {
        "status": "resolved_internal",
        "root_cause": "unit_of_analysis_difference",
    },
    "resolved_internal_crosswalk": {
        "status": "resolved_internal",
        "root_cause": "unit_of_analysis_difference",
    },
    "internal_mapping_fix_resolved": {
        "status": "resolved_internal",
        "root_cause": "internal_mapping_fix",
    },
    "partially_resolved_internal_mapping_fix": {
        "status": "resolved_internal",
        "root_cause": "internal_mapping_fix",
    },
    "internal_id_field_fix_resolved": {
        "status": "resolved_internal",
        "root_cause": "internal_id_fix",
    },
    "legacy_missing_level_columns": {
        "status": "baseline_limited",
        "root_cause": "legacy_missing_levels",
    },
    "legacy_only_source": {
        "status": "baseline_limited",
        "root_cause": "legacy_only_source",
    },
    "no_legacy_iso": {
        "status": "baseline_limited",
        "root_cause": "baseline_missing",
    },
}


def _read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, **kwargs)


def _to_number(value: Any) -> int | float | None:
    if value is None or pd.isna(value):
        return None
    num = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(num):
        return None
    if float(num).is_integer():
        return int(num)
    return float(num)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    return str(value).strip().lower() == "true"


def _pct(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(float(numerator) / float(denominator) * 100.0, 1)


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return json.loads(df.to_json(orient="records", force_ascii=False))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _current_coord_mask(df: pd.DataFrame) -> pd.Series:
    lat = (
        pd.to_numeric(df["latitud"], errors="coerce")
        if "latitud" in df.columns
        else pd.Series(index=df.index, dtype="float64")
    )
    lon = (
        pd.to_numeric(df["longitud"], errors="coerce")
        if "longitud" in df.columns
        else pd.Series(index=df.index, dtype="float64")
    )
    return lat.notna() & lon.notna()


def _raw_coord_mask(df: pd.DataFrame) -> pd.Series | None:
    if "coordinate_source" not in df.columns:
        return None
    source = df["coordinate_source"].fillna("").astype(str).str.strip()
    return _current_coord_mask(df) & source.isin(["", "original"])


def _quality_counts(df: pd.DataFrame) -> dict[str, int]:
    counts = {key: 0 for key in QUALITY_KEYS}
    if "coordinate_quality" not in df.columns:
        return counts
    quality = df["coordinate_quality"].fillna("").astype(str).str.strip()
    vc = quality.value_counts()
    for key in QUALITY_KEYS:
        counts[key] = int(vc.get(key, 0))
    return counts


def _quality_rollup(counts: dict[str, int]) -> dict[str, int]:
    return {
        bucket: int(sum(counts.get(key, 0) for key in members))
        for bucket, members in QUALITY_GROUPS.items()
    }


def map_step01_trace(trace_row: dict[str, Any] | None) -> dict[str, Any]:
    if not trace_row:
        return {
            "step01_trace_bucket": None,
            "step01_reconciliation_status": "needs_review",
            "step01_reconciliation_status_label_es": STEP01_STATUS_DEFINITIONS["needs_review"]["label_es"],
            "step01_root_cause": "unmapped",
            "step01_root_cause_label_es": STEP01_ROOT_CAUSE_DEFINITIONS["unmapped"]["label_es"],
            "step01_needs_source_owner_followup": False,
            "step01_justification_text": None,
        }

    bucket = trace_row.get("trace_bucket")
    mapping = TRACE_BUCKET_MAP.get(bucket, {"status": "needs_review", "root_cause": "unmapped"})
    needs_followup = _to_bool(trace_row.get("needs_source_owner_followup"))
    status = "source_review_needed" if needs_followup else mapping["status"]
    root_cause = mapping["root_cause"]
    return {
        "step01_trace_bucket": bucket,
        "step01_reconciliation_status": status,
        "step01_reconciliation_status_label_es": STEP01_STATUS_DEFINITIONS[status]["label_es"],
        "step01_root_cause": root_cause,
        "step01_root_cause_label_es": STEP01_ROOT_CAUSE_DEFINITIONS[root_cause]["label_es"],
        "step01_needs_source_owner_followup": needs_followup,
        "step01_justification_text": trace_row.get("trace_note"),
    }


def build_country_summary(
    base_dir: Path,
    country_scope: dict[str, dict[str, object]],
    coverage_df: pd.DataFrame,
    trace_df: pd.DataFrame,
    isos: list[str] | None = None,
) -> pd.DataFrame:
    """Build canonical dashboard summary rows from current CIMA files."""
    rows: list[dict[str, Any]] = []
    isos = isos or list(country_scope)

    coverage_lookup = {}
    if not coverage_df.empty and "country_iso" in coverage_df.columns:
        coverage_lookup = coverage_df.set_index("country_iso").to_dict(orient="index")

    trace_lookup = {}
    if not trace_df.empty and "iso" in trace_df.columns:
        trace_lookup = trace_df.set_index("iso").to_dict(orient="index")

    for iso in isos:
        scope = country_scope[iso]
        cima_path = base_dir / iso / "processed" / f"{iso}_total_cima.csv"
        coverage_row = coverage_lookup.get(iso, {})
        trace_row = trace_lookup.get(iso, {})
        step01 = map_step01_trace(trace_row if trace_row else None)

        if not cima_path.exists():
            rows.append(
                {
                    "iso": iso,
                    "file_exists": False,
                    "pipeline_enabled": bool(scope.get("pipeline_enabled", True)),
                    "analysis_included": bool(scope.get("analysis_included", True)),
                    "final_match_level": scope.get("final_match_level"),
                    "validation_tier": scope.get("validation_tier"),
                    "data_status": scope.get("data_status"),
                    "data_year": coverage_row.get("data_year"),
                    "sector_scope": coverage_row.get("sector_scope"),
                    "current_total": None,
                    "current_public": None,
                    "current_private": None,
                    "current_unknown": None,
                    "n_georef_current": None,
                    "pct_georef_current": None,
                    "n_georef_raw": None,
                    "pct_georef_raw": None,
                    "n_gap_filled": None,
                    "n_verified_like": None,
                    "pct_verified_like": None,
                    "pct_gps_validated": None,
                    "n_high_precision": None,
                    "pct_high_precision": None,
                    "n_centroid_precision": None,
                    "pct_centroid_precision": None,
                    "n_unverified": None,
                    "pct_unverified": None,
                    "n_include_spatial": None,
                    "pct_include_spatial": None,
                    "n_exclude_spatial": None,
                    "pct_exclude_spatial": None,
                    "n_review_spatial": None,
                    "pct_review_spatial": None,
                    "n_georef_public": _to_number(coverage_row.get("n_georef_public")),
                    "pct_georef_public": _to_number(coverage_row.get("pct_georef_public")),
                    "n_georef_private": _to_number(coverage_row.get("n_georef_private")),
                    "pct_georef_private": _to_number(coverage_row.get("pct_georef_private")),
                    "public_universe": _to_number(coverage_row.get("public_universe")),
                    "total_universe_est": _to_number(coverage_row.get("total_universe_est")),
                    "private_universe": _to_number(coverage_row.get("private_universe")),
                    "pct_coverage_vs_public": _to_number(coverage_row.get("pct_coverage_vs_public")),
                    "pct_coverage_vs_total": _to_number(coverage_row.get("pct_coverage_vs_total")),
                    "legacy_iso_total": _to_number(trace_row.get("legacy_iso_total")),
                    "legacy_iso_georef": _to_number(trace_row.get("legacy_iso_georef")),
                    "legacy_iso_k12_public_total": _to_number(trace_row.get("legacy_iso_k12_public_total")),
                    "legacy_iso_k12_public_prim": _to_number(trace_row.get("legacy_iso_k12_public_prim")),
                    "legacy_iso_k12_public_sbaj": _to_number(trace_row.get("legacy_iso_k12_public_sbaj")),
                    "legacy_iso_k12_public_salt": _to_number(trace_row.get("legacy_iso_k12_public_salt")),
                    "comparable_scope": trace_row.get("comparable_scope") or trace_row.get("comp_against"),
                    "current_comparable_total": _to_number(trace_row.get("current_comparable_total")),
                    "current_comparable_prim": _to_number(trace_row.get("current_comparable_prim")),
                    "current_comparable_sbaj": _to_number(trace_row.get("current_comparable_sbaj")),
                    "current_comparable_salt": _to_number(trace_row.get("current_comparable_salt")),
                    "delta_dashboard_vs_legacy_total": _to_number(trace_row.get("delta_dashboard_vs_legacy_total")),
                    "delta_public_vs_legacy_total": _to_number(trace_row.get("delta_public_vs_legacy_total")),
                    "delta_public_k12_vs_legacy_k12_total": _to_number(trace_row.get("delta_public_k12_vs_legacy_k12_total")),
                    "delta_public_k12_vs_legacy_k12_prim": _to_number(trace_row.get("delta_public_k12_vs_legacy_k12_prim")),
                    "delta_public_k12_vs_legacy_k12_sbaj": _to_number(trace_row.get("delta_public_k12_vs_legacy_k12_sbaj")),
                    "delta_public_k12_vs_legacy_k12_salt": _to_number(trace_row.get("delta_public_k12_vs_legacy_k12_salt")),
                    **{f"q_{key}": None for key in QUALITY_KEYS},
                    **{f"qg_{key}": None for key in QUALITY_GROUPS},
                    **step01,
                }
            )
            continue

        df = pd.read_csv(cima_path, dtype={"id_centro": str}, low_memory=False)
        total = int(len(df))
        current_mask = _current_coord_mask(df)
        raw_mask = _raw_coord_mask(df)
        quality_counts = _quality_counts(df)
        rollup = _quality_rollup(quality_counts)

        public = int((df.get("sector") == "Public").sum()) if "sector" in df.columns else 0
        private = int((df.get("sector") == "Private").sum()) if "sector" in df.columns else 0
        unknown = total - public - private
        n_georef_current = int(current_mask.sum())
        n_georef_raw = int(raw_mask.sum()) if raw_mask is not None else None
        n_verified_like = int(
            quality_counts.get("gps_validated", 0)
            + quality_counts.get("geocoded_street", 0)
            + quality_counts.get("geocoded_centroid", 0)
        )

        # Precision decomposition (Geurs & van Wee 2004; Castro/Giambruno/
        # Ortega Amazonia). pct_georef collapses precision tiers that differ
        # by 1-2 orders of magnitude — split into high (street/GPS-validated),
        # centroid (cluster + geocoded centroid), and unverified.
        n_high_precision = int(
            quality_counts.get("gps_validated", 0)
            + quality_counts.get("geocoded_street", 0)
        )
        n_centroid_precision = int(
            quality_counts.get("geocoded_centroid", 0)
            + quality_counts.get("cluster_centroid", 0)
        )
        n_unverified = int(
            quality_counts.get("gps_unverified", 0)
            + quality_counts.get("adm_mismatch", 0)
            + quality_counts.get("geocoder_disagrees", 0)
            + quality_counts.get("out_of_bounds", 0)
            + quality_counts.get("swapped", 0)
        )

        if "include_in_spatial_indicators" in df.columns:
            include_col = df["include_in_spatial_indicators"]
            n_include_spatial = int((include_col == True).sum())
            n_exclude_spatial = int((include_col == False).sum())
            n_review_spatial = int(include_col.isna().sum())
        else:
            n_include_spatial = None
            n_exclude_spatial = None
            n_review_spatial = None

        row = {
            "iso": iso,
            "file_exists": True,
            "pipeline_enabled": bool(scope.get("pipeline_enabled", True)),
            "analysis_included": bool(scope.get("analysis_included", True)),
            "final_match_level": scope.get("final_match_level"),
            "validation_tier": scope.get("validation_tier"),
            "data_status": scope.get("data_status"),
            "data_year": coverage_row.get("data_year"),
            "sector_scope": coverage_row.get("sector_scope"),
            "current_total": total,
            "current_public": public,
            "current_private": private,
            "current_unknown": unknown,
            "n_georef_current": n_georef_current,
            "pct_georef_current": _pct(n_georef_current, total),
            "n_georef_raw": n_georef_raw,
            "pct_georef_raw": _pct(n_georef_raw, total),
            "n_gap_filled": None if n_georef_raw is None else int(n_georef_current - n_georef_raw),
            "n_verified_like": n_verified_like,
            "pct_verified_like": _pct(n_verified_like, total),
            "pct_gps_validated": _pct(quality_counts.get("gps_validated", 0), total),
            "n_high_precision": n_high_precision,
            "pct_high_precision": _pct(n_high_precision, total),
            "n_centroid_precision": n_centroid_precision,
            "pct_centroid_precision": _pct(n_centroid_precision, total),
            "n_unverified": n_unverified,
            "pct_unverified": _pct(n_unverified, total),
            "n_include_spatial": n_include_spatial,
            "pct_include_spatial": _pct(n_include_spatial, total),
            "n_exclude_spatial": n_exclude_spatial,
            "pct_exclude_spatial": _pct(n_exclude_spatial, total),
            "n_review_spatial": n_review_spatial,
            "pct_review_spatial": _pct(n_review_spatial, total),
            "n_georef_public": _to_number(coverage_row.get("n_georef_public")),
            "pct_georef_public": _to_number(coverage_row.get("pct_georef_public")),
            "n_georef_private": _to_number(coverage_row.get("n_georef_private")),
            "pct_georef_private": _to_number(coverage_row.get("pct_georef_private")),
            "public_universe": _to_number(coverage_row.get("public_universe")),
            "total_universe_est": _to_number(coverage_row.get("total_universe_est")),
            "private_universe": _to_number(coverage_row.get("private_universe")),
            "pct_coverage_vs_public": _to_number(coverage_row.get("pct_coverage_vs_public")),
            "pct_coverage_vs_total": _to_number(coverage_row.get("pct_coverage_vs_total")),
            "legacy_iso_total": _to_number(trace_row.get("legacy_iso_total")),
            "legacy_iso_georef": _to_number(trace_row.get("legacy_iso_georef")),
            "legacy_iso_k12_public_total": _to_number(trace_row.get("legacy_iso_k12_public_total")),
            "legacy_iso_k12_public_prim": _to_number(trace_row.get("legacy_iso_k12_public_prim")),
            "legacy_iso_k12_public_sbaj": _to_number(trace_row.get("legacy_iso_k12_public_sbaj")),
            "legacy_iso_k12_public_salt": _to_number(trace_row.get("legacy_iso_k12_public_salt")),
            "comparable_scope": trace_row.get("comparable_scope") or trace_row.get("comp_against"),
            "current_comparable_total": _to_number(trace_row.get("current_comparable_total")),
            "current_comparable_prim": _to_number(trace_row.get("current_comparable_prim")),
            "current_comparable_sbaj": _to_number(trace_row.get("current_comparable_sbaj")),
            "current_comparable_salt": _to_number(trace_row.get("current_comparable_salt")),
            "delta_dashboard_vs_legacy_total": _to_number(trace_row.get("delta_dashboard_vs_legacy_total")),
            "delta_public_vs_legacy_total": _to_number(trace_row.get("delta_public_vs_legacy_total")),
            "delta_public_k12_vs_legacy_k12_total": _to_number(trace_row.get("delta_public_k12_vs_legacy_k12_total")),
            "delta_public_k12_vs_legacy_k12_prim": _to_number(trace_row.get("delta_public_k12_vs_legacy_k12_prim")),
            "delta_public_k12_vs_legacy_k12_sbaj": _to_number(trace_row.get("delta_public_k12_vs_legacy_k12_sbaj")),
            "delta_public_k12_vs_legacy_k12_salt": _to_number(trace_row.get("delta_public_k12_vs_legacy_k12_salt")),
            **{f"q_{key}": int(quality_counts[key]) for key in QUALITY_KEYS},
            **{f"qg_{key}": int(rollup[key]) for key in QUALITY_GROUPS},
            **step01,
        }
        rows.append(row)

    return pd.DataFrame(rows).sort_values("iso").reset_index(drop=True)


def build_reconciliation_view(country_df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "iso",
        "analysis_included",
        "comparable_scope",
        "current_public",
        "current_comparable_total",
        "current_comparable_prim",
        "current_comparable_sbaj",
        "current_comparable_salt",
        "legacy_iso_k12_public_total",
        "legacy_iso_k12_public_prim",
        "legacy_iso_k12_public_sbaj",
        "legacy_iso_k12_public_salt",
        "delta_public_k12_vs_legacy_k12_total",
        "delta_public_k12_vs_legacy_k12_prim",
        "delta_public_k12_vs_legacy_k12_sbaj",
        "delta_public_k12_vs_legacy_k12_salt",
        "delta_public_vs_legacy_total",
        "step01_trace_bucket",
        "step01_reconciliation_status",
        "step01_reconciliation_status_label_es",
        "step01_root_cause",
        "step01_root_cause_label_es",
        "step01_needs_source_owner_followup",
        "step01_justification_text",
    ]
    available = [col for col in columns if col in country_df.columns]
    return country_df[available].copy()


def build_geocoding_overview(geocode_df: pd.DataFrame, total_schools: int) -> dict[str, Any]:
    if geocode_df.empty:
        return {
            "total_schools": total_schools,
            "geocoder_universe_total": 0,
            "outside_geocoder_universe": total_schools,
            "outside_geocoder_universe_pct": _pct(total_schools, total_schools),
            "compare_universe": 0,
            "compare_universe_pct": _pct(0, total_schools),
            "fill_universe": 0,
            "fill_universe_pct": _pct(0, total_schools),
            "processed_country_isos": [],
            "processed_countries": 0,
            "compare_countries": 0,
            "fill_countries": 0,
            "compare_keep_original": 0,
            "compare_flag": 0,
            "compare_reject": 0,
            "compare_pending": 0,
            "fill_accept": 0,
            "fill_accept_street": 0,
            "fill_accept_centroid": 0,
            "fill_accept_with_flag": 0,
            "fill_reject": 0,
            "fill_pending": 0,
        }

    work = geocode_df.copy()
    work["target_type"] = work.get("target_type", "").fillna("").astype(str).str.strip().str.lower()
    work["acceptance"] = work.get("acceptance", "").fillna("").astype(str).str.strip().str.upper()

    compare_mask = work["target_type"].eq("compare")
    fill_mask = work["target_type"].eq("fill")

    compare_acceptance = work.loc[compare_mask, "acceptance"]
    fill_acceptance = work.loc[fill_mask, "acceptance"]
    processed_country_isos = sorted(work["iso"].dropna().astype(str).unique().tolist())

    geocoder_universe_total = int(len(work))
    compare_universe = int(compare_mask.sum())
    fill_universe = int(fill_mask.sum())
    outside_geocoder_universe = max(total_schools - geocoder_universe_total, 0)

    return {
        "total_schools": total_schools,
        "geocoder_universe_total": geocoder_universe_total,
        "outside_geocoder_universe": outside_geocoder_universe,
        "outside_geocoder_universe_pct": _pct(outside_geocoder_universe, total_schools),
        "compare_universe": compare_universe,
        "compare_universe_pct": _pct(compare_universe, total_schools),
        "fill_universe": fill_universe,
        "fill_universe_pct": _pct(fill_universe, total_schools),
        "processed_country_isos": processed_country_isos,
        "processed_countries": len(processed_country_isos),
        "compare_countries": int(work.loc[compare_mask, "iso"].dropna().astype(str).nunique()),
        "fill_countries": int(work.loc[fill_mask, "iso"].dropna().astype(str).nunique()),
        "compare_keep_original": int(compare_acceptance.eq("KEEP_ORIGINAL").sum()),
        "compare_flag": int(compare_acceptance.eq("FLAG").sum()),
        "compare_reject": int(compare_acceptance.eq("REJECT").sum()),
        "compare_pending": int(compare_acceptance.eq("").sum()),
        "fill_accept": int(fill_acceptance.isin(FILL_ACCEPTANCE_STATUSES).sum()),
        "fill_accept_street": int(fill_acceptance.eq("ACCEPT").sum()),
        "fill_accept_centroid": int(fill_acceptance.eq("ACCEPT_CENTROID").sum()),
        "fill_accept_with_flag": int(fill_acceptance.eq("ACCEPT_WITH_FLAG").sum()),
        "fill_reject": int(fill_acceptance.eq("REJECT").sum()),
        "fill_pending": int(fill_acceptance.eq("").sum()),
    }


def build_geocoding_country_summary(
    geocode_df: pd.DataFrame,
    ground_truth_df: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    gt_lookup = {}
    if not ground_truth_df.empty and "iso" in ground_truth_df.columns:
        gt_lookup = ground_truth_df.set_index("iso").to_dict(orient="index")

    if geocode_df.empty:
        return pd.DataFrame(rows)

    work = geocode_df.copy()
    work["iso"] = work["iso"].astype(str)
    work["target_type"] = work.get("target_type", "").fillna("").astype(str).str.strip().str.lower()
    work["acceptance"] = work.get("acceptance", "").fillna("").astype(str).str.strip().str.upper()
    work["geocode_precision"] = work.get("geocode_precision", "").fillna("").astype(str).str.strip().str.lower()

    for iso, iso_df in work.groupby("iso", sort=True):
        compare_df = iso_df[iso_df["target_type"] == "compare"]
        fill_df = iso_df[iso_df["target_type"] == "fill"]
        gt_row = gt_lookup.get(iso, {})

        rows.append(
            {
                "iso": iso,
                "processed_total": int(len(iso_df)),
                "compare_total": int(len(compare_df)),
                "fill_total": int(len(fill_df)),
                "precision_street": int(iso_df["geocode_precision"].eq("street").sum()),
                "precision_centroid": int(iso_df["geocode_precision"].eq("centroid").sum()),
                "precision_uncertain": int(iso_df["geocode_precision"].eq("uncertain").sum()),
                "compare_keep_original": int(compare_df["acceptance"].eq("KEEP_ORIGINAL").sum()),
                "compare_flag": int(compare_df["acceptance"].eq("FLAG").sum()),
                "compare_reject": int(compare_df["acceptance"].eq("REJECT").sum()),
                "compare_pending": int(compare_df["acceptance"].eq("").sum()),
                "fill_accept": int(fill_df["acceptance"].isin(FILL_ACCEPTANCE_STATUSES).sum()),
                "fill_accept_street": int(fill_df["acceptance"].eq("ACCEPT").sum()),
                "fill_accept_centroid": int(fill_df["acceptance"].eq("ACCEPT_CENTROID").sum()),
                "fill_accept_with_flag": int(fill_df["acceptance"].eq("ACCEPT_WITH_FLAG").sum()),
                "fill_reject": int(fill_df["acceptance"].eq("REJECT").sum()),
                "fill_pending": int(fill_df["acceptance"].eq("").sum()),
                "gt_sample_size": _to_number(gt_row.get("n")),
                "gt_score_median": _to_number(gt_row.get("score_median")),
                "gt_dist_median": _to_number(gt_row.get("dist_median")),
                "gt_pct_lt_5km": _to_number(gt_row.get("pct_lt5km")),
            }
        )

    return pd.DataFrame(rows).sort_values("iso").reset_index(drop=True)


def build_ground_truth_country_summary(summary_df: pd.DataFrame) -> pd.DataFrame:
    if summary_df.empty:
        return pd.DataFrame()

    out = summary_df.rename(
        columns={
            "n": "sample_size",
            "score_median": "score_median",
            "dist_median": "dist_median_km",
            "dist_mean": "dist_mean_km",
            "dist_p90": "dist_p90_km",
            "dist_max": "dist_max_km",
            "pct_lt1km": "pct_lt_1km",
            "pct_lt5km": "pct_lt_5km",
            "pct_lt15km": "pct_lt_15km",
            "pct_gt30km": "pct_gt_30km",
        }
    ).copy()
    return out.sort_values("iso").reset_index(drop=True)


def build_ground_truth_score_buckets(score_df: pd.DataFrame) -> pd.DataFrame:
    if score_df.empty:
        return pd.DataFrame()

    out = score_df.rename(
        columns={
            "score_range": "range",
            "dist_median_km": "median_km",
            "dist_mean_km": "mean_km",
            "dist_p90_km": "p90_km",
            "pct_lt1km": "pct_lt_1km",
            "pct_lt5km": "pct_lt_5km",
            "pct_lt15km": "pct_lt_15km",
            "pct_gt30km": "pct_gt_30km",
        }
    ).copy()
    return out.reset_index(drop=True)


def build_quality_totals(country_df: pd.DataFrame, scope_name: str, scope_mask: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    total_schools = int(country_df.loc[scope_mask, "current_total"].fillna(0).sum())

    quality_rows = []
    for key in QUALITY_KEYS:
        count = int(country_df.loc[scope_mask, f"q_{key}"].fillna(0).sum())
        quality_rows.append(
            {
                "scope": scope_name,
                "quality": key,
                "count": count,
                "pct_of_total": _pct(count, total_schools),
            }
        )

    rollup_rows = []
    for key in QUALITY_GROUPS:
        count = int(country_df.loc[scope_mask, f"qg_{key}"].fillna(0).sum())
        rollup_rows.append(
            {
                "scope": scope_name,
                "bucket": key,
                "count": count,
                "pct_of_total": _pct(count, total_schools),
            }
        )

    classified = int(sum(row["count"] for row in quality_rows))
    if classified != total_schools:
        quality_rows.append(
            {
                "scope": scope_name,
                "quality": "unclassified",
                "count": total_schools - classified,
                "pct_of_total": _pct(total_schools - classified, total_schools),
            }
        )

    return pd.DataFrame(quality_rows), pd.DataFrame(rollup_rows)


def build_source_checks(
    country_df: pd.DataFrame,
    coverage_df: pd.DataFrame,
    published_summary_df: pd.DataFrame,
    finalize_summary_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compare current CIMA-derived metrics against summary files."""
    checks: list[dict[str, Any]] = []

    coverage_lookup = {}
    if not coverage_df.empty and "country_iso" in coverage_df.columns:
        coverage_lookup = coverage_df.set_index("country_iso").to_dict(orient="index")

    summary_lookup = {}
    if not published_summary_df.empty and "iso" in published_summary_df.columns:
        summary_lookup = published_summary_df.set_index("iso").to_dict(orient="index")

    finalize_lookup = {}
    if not finalize_summary_df.empty and "iso" in finalize_summary_df.columns:
        finalize_lookup = finalize_summary_df.set_index("iso").to_dict(orient="index")

    for _, row in country_df.iterrows():
        iso = row["iso"]
        current_total = _to_number(row.get("current_total"))
        current_georef = _to_number(row.get("n_georef_current"))

        coverage_row = coverage_lookup.get(iso)
        if coverage_row:
            mismatch_fields = []
            if current_total != _to_number(coverage_row.get("n_schools_in_file")):
                mismatch_fields.append("n_schools_in_file")
            if current_georef != _to_number(coverage_row.get("n_georef")):
                mismatch_fields.append("n_georef")
            checks.append(
                {
                    "iso": iso,
                    "dataset": "school_coverage_assessment",
                    "any_mismatch": bool(mismatch_fields),
                    "mismatch_fields": ", ".join(mismatch_fields),
                    "current_total": current_total,
                    "source_total": _to_number(coverage_row.get("n_schools_in_file")),
                    "current_georef": current_georef,
                    "source_georef": _to_number(coverage_row.get("n_georef")),
                }
            )

        summary_row = summary_lookup.get(iso)
        if summary_row:
            mismatch_fields = []
            if current_total != _to_number(summary_row.get("total_k12")):
                mismatch_fields.append("total_k12")
            if current_georef != _to_number(summary_row.get("georef")):
                mismatch_fields.append("georef")
            checks.append(
                {
                    "iso": iso,
                    "dataset": "cima_v2_summary",
                    "any_mismatch": bool(mismatch_fields),
                    "mismatch_fields": ", ".join(mismatch_fields),
                    "current_total": current_total,
                    "source_total": _to_number(summary_row.get("total_k12")),
                    "current_georef": current_georef,
                    "source_georef": _to_number(summary_row.get("georef")),
                }
            )

        finalize_row = finalize_lookup.get(iso)
        if finalize_row:
            mismatch_fields = []
            if current_total != _to_number(finalize_row.get("total")):
                mismatch_fields.append("total")
            for key in QUALITY_KEYS:
                current_value = _to_number(row.get(f"q_{key}")) or 0
                source_value = _to_number(finalize_row.get(f"q_{key}")) or 0
                if current_value != source_value:
                    mismatch_fields.append(f"q_{key}")
            checks.append(
                {
                    "iso": iso,
                    "dataset": "qc_finalize_summary",
                    "any_mismatch": bool(mismatch_fields),
                    "mismatch_fields": ", ".join(mismatch_fields),
                    "current_total": current_total,
                    "source_total": _to_number(finalize_row.get("total")),
                    "current_georef": current_georef,
                    "source_georef": None,
                }
            )

    return pd.DataFrame(checks).sort_values(["dataset", "iso"]).reset_index(drop=True)


def _load_adm1_names(path: Path = ADM1_BOUNDARY_PATH) -> dict[str, str]:
    """Build {ADM1_PCODE: ADM1_EN} from the BID admin level-1 shapefile."""
    if not path.exists():
        return {}
    import geopandas as gpd

    gdf = gpd.read_file(path)
    if "ADM1_PCODE" not in gdf.columns or "ADM1_EN" not in gdf.columns:
        return {}
    return {
        str(row["ADM1_PCODE"]): str(row["ADM1_EN"])
        for _, row in gdf.iterrows()
        if pd.notna(row["ADM1_PCODE"]) and pd.notna(row["ADM1_EN"])
    }


def build_admin_concentration(
    base_dir: Path,
    isos: tuple[str, ...],
    country_scope: dict[str, dict[str, Any]],
    adm1_names: dict[str, str],
    top_n: int = 5,
) -> pd.DataFrame:
    """For each analysis country, list the top-N ADM1 with the highest count
    of include_in_spatial_indicators ∈ {False, NaN} (problems concentration).

    Ordered by absolute count of problems descending. Helps surface where the
    pipeline still owes coordinates: e.g., MEX/Chiapas vs problems evenly
    distributed across the country.
    """
    rows: list[dict[str, Any]] = []
    for iso in isos:
        scope = country_scope.get(iso, {})
        if not scope.get("analysis_included"):
            continue
        cima_path = base_dir / iso / "processed" / f"{iso}_total_cima.csv"
        if not cima_path.exists():
            continue
        df = pd.read_csv(cima_path, dtype={"id_centro": str}, low_memory=False)
        if "include_in_spatial_indicators" not in df.columns or "adm1_pcode" not in df.columns:
            continue

        include_col = df["include_in_spatial_indicators"]
        adm1_col = df["adm1_pcode"].fillna("__NULL__").astype(str)

        agg = pd.DataFrame({
            "adm1_pcode": adm1_col,
            "is_include": (include_col == True),
            "is_exclude": (include_col == False),
            "is_review": include_col.isna(),
        }).groupby("adm1_pcode", as_index=False).agg(
            n_total=("is_include", "size"),
            n_include=("is_include", "sum"),
            n_exclude=("is_exclude", "sum"),
            n_review=("is_review", "sum"),
        )
        agg["n_problems"] = agg["n_exclude"] + agg["n_review"]
        agg = (
            agg[agg["n_problems"] > 0]
            .sort_values("n_problems", ascending=False)
            .head(top_n)
        )

        for _, ra in agg.iterrows():
            pcode = ra["adm1_pcode"]
            pcode_str = None if pcode == "__NULL__" else str(pcode)
            rows.append({
                "iso": iso,
                "adm1_pcode": pcode_str,
                "adm1_name": adm1_names.get(pcode_str, None) if pcode_str else None,
                "n_total": int(ra["n_total"]),
                "n_include": int(ra["n_include"]),
                "n_exclude": int(ra["n_exclude"]),
                "n_review": int(ra["n_review"]),
                "n_problems": int(ra["n_problems"]),
                "pct_problems": (
                    round(float(ra["n_problems"]) / float(ra["n_total"]) * 100, 1)
                    if ra["n_total"] > 0 else None
                ),
            })

    return pd.DataFrame(rows)


def build_id_edificio_summary(lac_k12_path: Path = LAC_K12_CLEAN_PATH) -> pd.DataFrame:
    """Per-country split of id_edificio assignments into 'real' (matched a
    canonical building in LAC_merged via identity/cast/spatial bridge) vs
    'synthetic' (no match found, assigned {ISO}_SYN_{N:05d}).

    Synthetic ids signal schools that the BID's existing LAC_merged universe
    did not contain — typically newer schools, edge-case identifiers, or
    countries with high churn in the school registry.
    """
    if not lac_k12_path.exists():
        return pd.DataFrame(columns=[
            "iso", "n_total", "n_real", "n_synthetic", "pct_real", "pct_synthetic"
        ])

    df = pd.read_csv(
        lac_k12_path,
        dtype={"id_centro": str, "id_edificio": str},
        usecols=["adm0_pcode", "id_edificio"],
        low_memory=False,
    )
    df["kind"] = df["id_edificio"].apply(
        lambda s: "synthetic" if pd.notna(s) and "_SYN_" in str(s) else "real"
    )
    grouped = df.groupby(["adm0_pcode", "kind"]).size().unstack(fill_value=0)
    if "synthetic" not in grouped.columns:
        grouped["synthetic"] = 0
    if "real" not in grouped.columns:
        grouped["real"] = 0
    grouped["total"] = grouped["real"] + grouped["synthetic"]
    grouped["pct_real"] = (grouped["real"] / grouped["total"] * 100).round(1)
    grouped["pct_synthetic"] = (grouped["synthetic"] / grouped["total"] * 100).round(1)
    grouped = grouped.reset_index().rename(columns={
        "adm0_pcode": "iso",
        "real": "n_real",
        "synthetic": "n_synthetic",
        "total": "n_total",
    })
    return grouped[["iso", "n_total", "n_real", "n_synthetic", "pct_real", "pct_synthetic"]]


def build_id_edificio_outliers(
    lac_k12_path: Path = LAC_K12_CLEAN_PATH,
    threshold: int = 20,
    top_n: int = 30,
) -> pd.DataFrame:
    """Surface id_edificio values that group an implausibly high number of
    schools in the CIMA K-12 base — almost certainly centroide errors that
    BID propagated through LAC_merged.csv.

    The cascade C in pipeline/05_base_k_12_clean.py uses 'one id_edificio per
    coord = same building' as the heuristic, but LAC_merged itself was built
    without addresses to verify same-building claims (the file lacks an
    address column). BID appears to have used coord proximity, which collapses
    centroide-municipal locations (frontier zones, indigenous territories,
    rural ADM2 with no GPS capture) into a single false id_edificio that
    aggregates dozens to hundreds of physically distinct schools.

    This function returns the top-N id_edificio with the highest count of
    schools in the current CIMA K-12 base, for surface in the dashboard +
    methodology review.
    """
    if not lac_k12_path.exists():
        return pd.DataFrame(columns=[
            "id_edificio", "iso", "n_schools", "lat", "lon",
            "dominant_coordinate_quality", "dominant_quality_pct",
        ])

    df = pd.read_csv(
        lac_k12_path,
        dtype={"id_centro": str, "id_edificio": str},
        usecols=["adm0_pcode", "id_edificio", "latitud", "longitud", "coordinate_quality"],
        low_memory=False,
    )

    def _dominant(s: pd.Series) -> str:
        vc = s.value_counts(dropna=False)
        return str(vc.index[0]) if len(vc) > 0 else ""

    def _dominant_pct(s: pd.Series) -> float:
        vc = s.value_counts(dropna=False)
        if len(vc) == 0 or len(s) == 0:
            return 0.0
        return round(float(vc.iloc[0]) / float(len(s)) * 100, 1)

    grouped = df.groupby("id_edificio").agg(
        n_schools=("id_edificio", "size"),
        iso=("adm0_pcode", "first"),
        lat=("latitud", "first"),
        lon=("longitud", "first"),
        dominant_coordinate_quality=("coordinate_quality", _dominant),
        dominant_quality_pct=("coordinate_quality", _dominant_pct),
    ).reset_index()

    outliers = (
        grouped[grouped["n_schools"] >= threshold]
        .sort_values("n_schools", ascending=False)
        .head(top_n)
    )
    outliers["is_synthetic"] = outliers["id_edificio"].str.contains("_SYN_", na=False)
    return outliers.reset_index(drop=True)


def build_rwi_scatter_col(path: Path = RWI_VS_POVERTY_COL_PATH) -> pd.DataFrame:
    """Per-ADM2 RWI vs poverty rate data for the COL scatter plot.

    Source: pipeline/06_pop_exploratory.py output. 1,056 ADM2 with ≥5 RWI cells
    and pop>0. Used by the dashboard step-06 to render an interactive scatter
    that lets the user see the moderate correlation (Spearman ρ = -0.51) +
    discordances (Caribe vs Andes).
    """
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    out = df[[
        "ADM1_PCODE", "ADM1_EN", "ADM2_PCODE", "ADM2_EN",
        "rwi_pop_weighted_mean", "POVERTY_RATE", "NBI_RATE", "pop_total",
    ]].copy()
    out.columns = [
        "adm1_pcode", "adm1_en", "adm2_pcode", "adm2_en",
        "rwi", "poverty_rate", "nbi_rate", "pop_total",
    ]
    out["pop_total"] = out["pop_total"].round(0)
    return out.reset_index(drop=True)


def build_rwi_decile_heatmap_col(path: Path = RWI_VS_POVERTY_COL_PATH) -> pd.DataFrame:
    """10×10 decile crosstab of RWI (inverted, so 1=richest…10=poorest)
    vs poverty_rate decile (1=lowest pov…10=highest). Counts per cell.
    """
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path).dropna(subset=["rwi_pop_weighted_mean", "POVERTY_RATE"])
    df["rwi_decile_inverted"] = pd.qcut(
        -df["rwi_pop_weighted_mean"], 10, labels=False, duplicates="drop",
    ) + 1
    df["poverty_decile"] = pd.qcut(
        df["POVERTY_RATE"], 10, labels=False, duplicates="drop",
    ) + 1
    grid = df.groupby(["rwi_decile_inverted", "poverty_decile"]).size().reset_index(name="n")
    return grid.rename(columns={
        "rwi_decile_inverted": "rwi_decile",
        "poverty_decile": "pov_decile",
    })


def build_rwi_decile_heatmap_multi(
    exploratory_dir: Path = RWI_EXPLORATORY_DIR,
) -> pd.DataFrame:
    """Per-country 10×10 decile crosstab for every analysis ISO that has a tests.csv.

    For each country, pick the target with the strongest |ρ| (matches the
    "principal" used by build_rwi_validation_summary) and build the crosstab
    against that target's column in the merged file. Long format with
    [iso, target, rwi_decile, pov_decile, n].
    """
    rows: list[dict] = []
    for tests_path in sorted(exploratory_dir.glob("*_rwi_vs_poverty_tests.csv")):
        iso = tests_path.stem.split("_")[0]
        merged_path = exploratory_dir / f"{iso}_rwi_vs_poverty_merged.csv"
        if not merged_path.exists() or tests_path.stat().st_size == 0:
            continue
        try:
            tests = pd.read_csv(tests_path)
        except pd.errors.EmptyDataError:
            continue
        if tests.empty or "spearman_rho" not in tests.columns:
            continue
        tests = tests[tests["spearman_rho"].notna()].copy()
        if tests.empty:
            continue
        tests["abs_rho"] = tests["spearman_rho"].abs()
        principal = tests.sort_values("abs_rho", ascending=False).iloc[0]
        target_col = principal["target"]
        df = pd.read_csv(merged_path).dropna(subset=["rwi_pop_weighted_mean", target_col])
        if df.empty:
            continue
        try:
            rwi_dec = pd.qcut(
                -df["rwi_pop_weighted_mean"], 10, labels=False, duplicates="drop",
            ) + 1
            target_dec = pd.qcut(
                df[target_col], 10, labels=False, duplicates="drop",
            ) + 1
        except ValueError:
            continue
        grid = (
            pd.DataFrame({"rwi_decile": rwi_dec, "pov_decile": target_dec})
            .groupby(["rwi_decile", "pov_decile"]).size().reset_index(name="n")
        )
        grid["iso"] = iso
        grid["target"] = target_col
        rows.append(grid[["iso", "target", "rwi_decile", "pov_decile", "n"]])
    if not rows:
        return pd.DataFrame(columns=["iso", "target", "rwi_decile", "pov_decile", "n"])
    return pd.concat(rows, ignore_index=True)


def build_rwi_validation_summary(
    exploratory_dir: Path = RWI_EXPLORATORY_DIR,
    rwi_data_dir: Path = RWI_DATA_DIR,
    analysis_isos: tuple[str, ...] = (
        "ARG", "BLZ", "BOL", "BRA", "BRB", "CHL", "COL", "CRI", "DOM", "ECU",
        "GTM", "GUY", "HND", "JAM", "MEX", "PAN", "PER", "PRY", "SLV", "SUR", "URY",
    ),
) -> pd.DataFrame:
    """Multi-country RWI vs poverty validation table.

    For each analysis country, classify:
      - 'completed': has *_rwi_vs_poverty_tests.csv with Spearman ρ / decile match
      - 'pending': RWI data exists on disk but exploratory hasn't run yet
      - 'blocked': RWI data exists but exploratory can't run (BRA: memory)
      - 'no-rwi-data': no RWI csv from Meta in data/Poverty Rates/meta-rwi/

    Returns one row per country with: iso, status, n, spearman_rho, ci_lo, ci_hi,
    decile_exact, decile_within_one, r2.
    """
    rows = []
    # Pre-known blockers — countries where the local pipeline can't run:
    BLOCKED_ISOS = {
        # 100m WorldPop raster too large for in-memory load on local machine
        "BRA": "raster_too_large_local",
        "MEX": "raster_too_large_local",
        "ARG": "raster_too_large_local",
    }
    # Countries with no ADM2 polygons in BID lac-level-2.shp (admin level-1 only)
    NO_ADM2_BID = {"BLZ", "JAM"}
    # Countries with no POVERTY or NBI rate in BID lac-level-2.csv
    NO_POVERTY_DATA = {"GUY", "SUR"}

    for iso in sorted(analysis_isos):
        rwi_data = rwi_data_dir / iso / f"{iso.lower()}_relative_wealth_index.csv"
        tests_path = exploratory_dir / f"{iso}_rwi_vs_poverty_tests.csv"
        if not rwi_data.exists():
            rows.append({
                "iso": iso, "status": "no_rwi_data",
                "n": None, "spearman_rho": None, "spearman_ci_lo": None, "spearman_ci_hi": None,
                "decile_exact": None, "decile_within_one": None, "r2": None,
                "target_used": None,
            })
            continue
        if iso in NO_ADM2_BID:
            rows.append({
                "iso": iso, "status": "no_adm2_bid",
                "n": None, "spearman_rho": None, "spearman_ci_lo": None, "spearman_ci_hi": None,
                "decile_exact": None, "decile_within_one": None, "r2": None,
                "target_used": None,
            })
            continue
        if iso in NO_POVERTY_DATA:
            rows.append({
                "iso": iso, "status": "no_poverty_data",
                "n": None, "spearman_rho": None, "spearman_ci_lo": None, "spearman_ci_hi": None,
                "decile_exact": None, "decile_within_one": None, "r2": None,
                "target_used": None,
            })
            continue
        if iso in BLOCKED_ISOS:
            # If the Colab notebook produced the tests file, treat as completed.
            # Otherwise, surface as blocked with the reason.
            if not tests_path.exists() or pd.read_csv(tests_path).empty:
                rows.append({
                    "iso": iso, "status": "blocked",
                    "blocked_reason": BLOCKED_ISOS[iso],
                    "n": None, "spearman_rho": None, "spearman_ci_lo": None, "spearman_ci_hi": None,
                    "decile_exact": None, "decile_within_one": None, "r2": None,
                    "target_used": None,
                })
                continue
            # else: fall through and read the Colab-produced tests CSV below
        if not tests_path.exists():
            rows.append({
                "iso": iso, "status": "pending",
                "n": None, "spearman_rho": None, "spearman_ci_lo": None, "spearman_ci_hi": None,
                "decile_exact": None, "decile_within_one": None, "r2": None,
                "target_used": None,
            })
            continue
        tests = pd.read_csv(tests_path)
        if tests.empty:
            # Country ran the script but has no POVERTY or NBI rate to test against
            rows.append({
                "iso": iso, "status": "no_poverty_data",
                "n": None, "spearman_rho": None, "spearman_ci_lo": None, "spearman_ci_hi": None,
                "decile_exact": None, "decile_within_one": None, "r2": None,
                "target_used": None,
                "secondary_target": None, "secondary_rho": None, "secondary_n": None,
                "secondary_decile_within_one": None,
            })
            continue
        # Both POVERTY_RATE and NBI_RATE are tested when available. We pick the
        # one with the *strongest* correlation (highest |ρ|) as primary and
        # expose the other as secondary, so the dashboard shows the best-case
        # for each country with transparency about what wasn't picked.
        # Example: BOL has POVERTY ρ=-0.42 and NBI ρ=-0.84 → NBI primary,
        # POVERTY secondary. COL has POVERTY -0.51 and NBI -0.44 → POVERTY
        # primary, NBI secondary.
        pov = tests[tests["target"] == "POVERTY_RATE"]
        nbi = tests[tests["target"] == "NBI_RATE"]
        candidates = []
        if not pov.empty:
            candidates.append(("POVERTY_RATE", pov.iloc[0]))
        if not nbi.empty:
            candidates.append(("NBI_RATE", nbi.iloc[0]))
        if not candidates:
            rows.append({
                "iso": iso, "status": "pending",
                "n": None, "spearman_rho": None, "spearman_ci_lo": None, "spearman_ci_hi": None,
                "decile_exact": None, "decile_within_one": None, "r2": None,
                "target_used": None,
                "secondary_target": None, "secondary_rho": None, "secondary_n": None,
                "secondary_decile_within_one": None,
            })
            continue
        # Sort by |ρ| descending; pick first as primary, second (if exists) as secondary
        candidates.sort(key=lambda c: abs(float(c[1]["spearman_rho"])), reverse=True)
        target_used, r = candidates[0]
        rho = float(r["spearman_rho"])
        secondary_target, secondary_rho, secondary_n, secondary_decile = (None, None, None, None)
        if len(candidates) > 1:
            sec_target, sec_r = candidates[1]
            secondary_target = sec_target
            secondary_rho = float(sec_r["spearman_rho"])
            secondary_n = int(sec_r["n"])
            secondary_decile = round(float(sec_r["decile_within_one"]) * 100, 1)
        rows.append({
            "iso": iso,
            "status": "completed",
            "n": int(r["n"]),
            "spearman_rho": rho,
            "spearman_ci_lo": float(r["spearman_ci_lo"]),
            "spearman_ci_hi": float(r["spearman_ci_hi"]),
            "decile_exact": round(float(r["decile_exact_match"]) * 100, 1),
            "decile_within_one": round(float(r["decile_within_one"]) * 100, 1),
            "r2": round(rho * rho, 3),
            "target_used": target_used,
            "secondary_target": secondary_target,
            "secondary_rho": secondary_rho,
            "secondary_n": secondary_n,
            "secondary_decile_within_one": secondary_decile,
        })
    return pd.DataFrame(rows)


def build_bogota_rwi_cells(path: Path = RWI_RAW_COL_PATH, bbox: tuple[float, ...] = BOGOTA_BBOX) -> pd.DataFrame:
    """RWI cells within Bogotá bbox (CO11001) for the interactive map.
    Returns lat/lon/rwi/error columns, ~185 rows.
    """
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    min_lon, min_lat, max_lon, max_lat = bbox
    sub = df[
        (df["latitude"] >= min_lat) & (df["latitude"] <= max_lat)
        & (df["longitude"] >= min_lon) & (df["longitude"] <= max_lon)
    ].copy()
    return sub.rename(columns={"latitude": "lat", "longitude": "lon"}).reset_index(drop=True)


def build_bogota_schools(path: Path = RWI_SCHOOLS_COL_PATH) -> pd.DataFrame:
    """Bogotá schools (adm2_pcode=CO11001) with their RWI assignment for the
    interactive map.
    """
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, dtype={"id_centro": str}, low_memory=False)
    sub = df[df["adm2_pcode"] == "CO11001"].copy()
    keep = [
        "id_centro", "nombre_centro", "sector", "nivel_primaria", "nivel_secbaja",
        "nivel_secalta", "latitud", "longitud", "rwi", "coordinate_quality",
    ]
    keep = [c for c in keep if c in sub.columns]
    return sub[keep].reset_index(drop=True)


def build_bogota_boundary(path: Path = ADM2_BOUNDARY_PATH) -> dict:
    """Bogotá ADM2 polygon as simplified GeoJSON for the dashboard map outline.
    Tolerance 0.001 ≈ 100 m, reduces vertex count without losing the city shape.
    """
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    import geopandas as gpd
    gdf = gpd.read_file(path)
    bog = gdf[gdf["ADM2_PCODE"] == "CO11001"].copy()
    if bog.empty:
        return {"type": "FeatureCollection", "features": []}
    bog["geometry"] = bog.geometry.simplify(0.001, preserve_topology=True)
    bog = bog[["ADM2_PCODE", "ADM2_EN", "geometry"]]
    return json.loads(bog.to_json())


def build_pop_validation_summary(validation_path: Path = POP_VALIDATION_PATH) -> pd.DataFrame:
    """Per-country population grid validation against World Bank 2023 totals
    + urban % comparison + Meta RWI / IDB poverty / NBI grid coverage."""
    if not validation_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(validation_path)
    # Normalize numeric columns to JSON-friendly types
    for col in ["pop_total", "pop_wb_2023", "diff_abs", "diff_pct",
                "pct_pop_urbana", "urb_pct_wb_2023", "urb_pct_diff",
                "n_cells_with_rwi", "n_cells_with_poverty", "n_cells_with_nbi"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def build_scope_totals(country_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    scope_masks = {
        "pipeline": country_df["pipeline_enabled"].fillna(False).astype(bool),
        "analysis": country_df["analysis_included"].fillna(False).astype(bool),
    }

    for scope_name, mask in scope_masks.items():
        total = int(country_df.loc[mask, "current_total"].fillna(0).sum())
        georef_current = int(country_df.loc[mask, "n_georef_current"].fillna(0).sum())
        georef_raw = _to_number(country_df.loc[mask, "n_georef_raw"].fillna(0).sum())
        public = int(country_df.loc[mask, "current_public"].fillna(0).sum())
        private = int(country_df.loc[mask, "current_private"].fillna(0).sum())
        verified_like = int(country_df.loc[mask, "n_verified_like"].fillna(0).sum())
        gps_validated = int(country_df.loc[mask, "q_gps_validated"].fillna(0).sum())
        high_precision = int(country_df.loc[mask, "n_high_precision"].fillna(0).sum())
        centroid_precision = int(country_df.loc[mask, "n_centroid_precision"].fillna(0).sum())
        unverified = int(country_df.loc[mask, "n_unverified"].fillna(0).sum())
        include_spatial = int(country_df.loc[mask, "n_include_spatial"].fillna(0).sum())
        exclude_spatial = int(country_df.loc[mask, "n_exclude_spatial"].fillna(0).sum())
        review_spatial = int(country_df.loc[mask, "n_review_spatial"].fillna(0).sum())

        rows.append(
            {
                "scope": scope_name,
                "countries": int(mask.sum()),
                "total_schools": total,
                "public_schools": public,
                "private_schools": private,
                "n_georef_current": georef_current,
                "pct_georef_current": _pct(georef_current, total),
                "n_georef_raw": georef_raw,
                "pct_georef_raw": _pct(georef_raw, total),
                "n_verified_like": verified_like,
                "pct_verified_like": _pct(verified_like, total),
                "n_gps_validated": gps_validated,
                "pct_gps_validated": _pct(gps_validated, total),
                "n_high_precision": high_precision,
                "pct_high_precision": _pct(high_precision, total),
                "n_centroid_precision": centroid_precision,
                "pct_centroid_precision": _pct(centroid_precision, total),
                "n_unverified": unverified,
                "pct_unverified": _pct(unverified, total),
                "n_include_spatial": include_spatial,
                "pct_include_spatial": _pct(include_spatial, total),
                "n_exclude_spatial": exclude_spatial,
                "pct_exclude_spatial": _pct(exclude_spatial, total),
                "n_review_spatial": review_spatial,
                "pct_review_spatial": _pct(review_spatial, total),
            }
        )

    return pd.DataFrame(rows)


def _definitions_df(definitions: dict[str, dict[str, str]], key_name: str) -> pd.DataFrame:
    rows = [{key_name: key, **meta} for key, meta in definitions.items()]
    return pd.DataFrame(rows)


def write_sqlite_bundle(db_path: Path, tables: dict[str, pd.DataFrame], metadata: dict[str, Any]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    with sqlite3.connect(db_path) as conn:
        for table_name, df in tables.items():
            df.to_sql(table_name, conn, if_exists="replace", index=False)

        metadata_rows = [
            {"section": key, "value_json": json.dumps(value, ensure_ascii=False)}
            for key, value in metadata.items()
        ]
        pd.DataFrame(metadata_rows).to_sql("build_metadata", conn, if_exists="replace", index=False)


def export_dashboard_bundle(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    coverage_df = _read_csv(COVERAGE_PATH)
    published_summary_df = _read_csv(PUBLISHED_SUMMARY_PATH)
    finalize_summary_df = _read_csv(FINALIZE_SUMMARY_PATH)
    trace_df = _read_csv(STEP01_TRACE_PATH)
    geocode_results_df = _read_csv(GEOCODE_RESULTS_PATH)
    geocode_targets_df = _read_csv(GEOCODE_TARGETS_PATH)
    step03_candidates_df = _read_csv(STEP03_CANDIDATES_PATH)
    qc_baseline_df = _read_csv(QC_BASELINE_PATH)
    ground_truth_summary_df = _read_csv(GROUND_TRUTH_SUMMARY_PATH)
    ground_truth_score_df = _read_csv(GROUND_TRUTH_SCORE_ANALYSIS_PATH)

    country_df = build_country_summary(
        base_dir=BASE,
        country_scope=COUNTRY_SCOPE,
        coverage_df=coverage_df,
        trace_df=trace_df,
        isos=PIPELINE_ISOS,
    )
    reconciliation_df = build_reconciliation_view(country_df)
    source_checks_df = build_source_checks(country_df, coverage_df, published_summary_df, finalize_summary_df)
    scope_totals_df = build_scope_totals(country_df)
    adm1_names = _load_adm1_names()
    admin_concentration_df = build_admin_concentration(
        base_dir=BASE,
        isos=PIPELINE_ISOS,
        country_scope=COUNTRY_SCOPE,
        adm1_names=adm1_names,
    )
    id_edificio_df = build_id_edificio_summary()
    id_edificio_outliers_df = build_id_edificio_outliers()
    pop_validation_df = build_pop_validation_summary()
    rwi_scatter_col_df = build_rwi_scatter_col()
    rwi_decile_heatmap_col_df = build_rwi_decile_heatmap_col()
    rwi_decile_heatmap_multi_df = build_rwi_decile_heatmap_multi()
    rwi_validation_summary_df = build_rwi_validation_summary()
    bogota_rwi_cells_df = build_bogota_rwi_cells()
    bogota_schools_df = build_bogota_schools()
    bogota_boundary_geojson = build_bogota_boundary()
    ground_truth_country_df = build_ground_truth_country_summary(ground_truth_summary_df)
    ground_truth_score_buckets_df = build_ground_truth_score_buckets(ground_truth_score_df)

    # Step-03 "Información disponible" + funnel inputs.
    # Joins the structured per-country info with target counts (from Step 02
    # finalize) and school totals (from country_summary).
    country_info_levels_df = build_country_info_levels(
        targets_df=geocode_targets_df,
        schools_df=country_df[["iso", "current_total"]].copy(),
    )

    # Attach pipeline/analysis flags to geocode_targets so the frontend can
    # filter to the analysis universe (21) the same way it does for the
    # country_summary table.
    # Helper: scope-flag column names + their pandas-suffixed variants from
    # earlier broken merges (`_x`, `_y`, `_z`). Rebuilds the flags from
    # country_df cleanly each export so the CSV has exactly one canonical
    # column per flag, regardless of prior corruption.
    def _attach_scope_flags(df: pd.DataFrame, country_df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        flag_prefixes = ("pipeline_enabled", "analysis_included")
        cols_to_drop = [
            c for c in df.columns
            if c in flag_prefixes
            or any(c.startswith(p + "_") for p in flag_prefixes)
        ]
        if cols_to_drop:
            df = df.drop(columns=cols_to_drop)
        scope_flags = country_df[["iso", "pipeline_enabled", "analysis_included"]].copy()
        return df.merge(scope_flags, on="iso", how="left")

    geocode_targets_df = _attach_scope_flags(geocode_targets_df, country_df)
    qc_baseline_df = _attach_scope_flags(qc_baseline_df, country_df)

    pipeline_mask = country_df["pipeline_enabled"].fillna(False).astype(bool)
    analysis_mask = country_df["analysis_included"].fillna(False).astype(bool)
    quality_pipeline_df, rollup_pipeline_df = build_quality_totals(country_df, "pipeline", pipeline_mask)
    quality_analysis_df, rollup_analysis_df = build_quality_totals(country_df, "analysis", analysis_mask)

    quality_totals_df = pd.concat([quality_pipeline_df, quality_analysis_df], ignore_index=True)
    rollup_totals_df = pd.concat([rollup_pipeline_df, rollup_analysis_df], ignore_index=True)

    analysis_total = int(scope_totals_df.loc[scope_totals_df["scope"] == "analysis", "total_schools"].iloc[0])

    # Reconcile step-03 ↔ step-04: the geocoder ledger CSV is append-only and
    # carries everything step-04 ever queried (compare/fill targets, including
    # `dup_addr` schools that are NOT in the step-03 funnel and historical rows
    # for schools whose state has since changed). Filter the ledger to schools
    # the current step-03 funnel considers candidates, so `geocoding_overview`
    # and `geocoding_country_summary` reconcile with the per-country pending +
    # already-fixed counts shown in `country_info_levels`.
    if (
        not step03_candidates_df.empty
        and {"iso", "id_centro"}.issubset(step03_candidates_df.columns)
    ):
        funnel_keys = set(
            zip(
                step03_candidates_df["iso"].astype(str),
                step03_candidates_df["id_centro"].astype(str),
            )
        )
    else:
        funnel_keys = set()

    if not geocode_results_df.empty and funnel_keys:
        iso_arr = geocode_results_df["iso"].astype(str)
        id_arr = geocode_results_df["id_centro"].astype(str)
        in_funnel_mask = pd.Series(
            [(i, c) in funnel_keys for i, c in zip(iso_arr, id_arr)],
            index=geocode_results_df.index,
        )
        funnel_geocode_df = geocode_results_df[in_funnel_mask].copy()
    else:
        funnel_geocode_df = geocode_results_df.copy()

    geocoding_overview = build_geocoding_overview(funnel_geocode_df, analysis_total)
    geocoding_overview["ledger_rows_total"] = int(len(geocode_results_df))
    geocoding_overview["ledger_rows_excluded_from_funnel"] = int(
        len(geocode_results_df) - len(funnel_geocode_df)
    )

    # Step-03 funnel totals (single source of truth for the dashboard
    # GeocodingFunnel headline). Reconciles with `country_info_levels` rollup.
    #
    # Three-way classification per school (mutually exclusive):
    #   - RECOVERED  : step-04 wrote new coordinates (coordinate_source ∈
    #                  {geocoded, centroid_cascade}). Phase B-1 + B-2.
    #   - VALIDATED  : step-04 ran the geocoder but did NOT replace coords.
    #                  Compare flow keeps the original GPS by design (the
    #                  acceptance label records the verdict). For fill rows
    #                  this also covers REJECT outcomes (geocoder couldn't
    #                  find a result that passes the score threshold).
    #   - UNPROCESSED: school is in step-03 funnel but step-04 has no ledger
    #                  entry yet (countries not in PHASE_B1_ISOS, partial runs).
    #
    # The 'recovered + validated + unprocessed' sum equals funnel_total. This
    # is the breakdown the user-facing dashboard headline reports, replacing
    # the earlier misleading "recovered/pending" framing — schools step-04
    # validated via compare are NOT pending, they are processed-but-GPS-
    # preserved, which is the correct policy outcome.
    if not step03_candidates_df.empty:
        # Restrict the funnel to analysis-included ISOs (BHS/HTI ship in
        # the candidates CSV but are excluded from the published universe).
        step03_candidates_df = step03_candidates_df[
            step03_candidates_df["iso"].isin(ANALYSIS_ISOS)
        ].reset_index(drop=True)
        status = step03_candidates_df["funnel_status"].fillna("")
        cand_keys = list(zip(
            step03_candidates_df["iso"].astype(str),
            step03_candidates_df["id_centro"].astype(str),
        ))
        ledger_keys = (
            set(zip(
                geocode_results_df["iso"].astype(str),
                geocode_results_df["id_centro"].astype(str),
            ))
            if not geocode_results_df.empty else set()
        )

        recovered_mask = status.isin(["already_geocoded", "already_cascade"])
        attempted_mask = pd.Series(
            [k in ledger_keys for k in cand_keys],
            index=step03_candidates_df.index,
        )
        validated_mask = (~recovered_mask) & attempted_mask
        unprocessed_mask = (~recovered_mask) & (~attempted_mask)

        geocoding_overview["funnel_total"] = int(len(step03_candidates_df))
        geocoding_overview["funnel_already_geocoded"] = int(
            (status == "already_geocoded").sum()
        )
        geocoding_overview["funnel_already_cascade"] = int(
            (status == "already_cascade").sum()
        )
        geocoding_overview["funnel_recovered"] = int(recovered_mask.sum())
        geocoding_overview["funnel_validated"] = int(validated_mask.sum())
        geocoding_overview["funnel_unprocessed"] = int(unprocessed_mask.sum())

        # Per-route candidate totals (match step-03 funnel routing). Schools
        # are mutually exclusive across routes.
        compare_status = status.isin([
            "pending_mismatches",
            "pending_centroids",
            "pending_out_of_bounds",
        ])
        fill_status = status.isin([
            "pending_missing",
            "already_geocoded",
            "already_cascade",
        ])

        geocoding_overview["funnel_compare_candidates"] = int(compare_status.sum())
        geocoding_overview["funnel_compare_processed"] = int(
            (compare_status & attempted_mask).sum()
        )
        geocoding_overview["funnel_compare_unprocessed"] = int(
            (compare_status & ~attempted_mask).sum()
        )

        geocoding_overview["funnel_fill_candidates"] = int(fill_status.sum())
        geocoding_overview["funnel_fill_recovered"] = int(
            (fill_status & recovered_mask).sum()
        )
        geocoding_overview["funnel_fill_attempted_failed"] = int(
            (fill_status & attempted_mask & ~recovered_mask).sum()
        )
        geocoding_overview["funnel_fill_unprocessed"] = int(
            (fill_status & ~attempted_mask & ~recovered_mask).sum()
        )

        # Per-(iso, sector) candidate counts grouped by route, with each
        # school counted exactly once (no bucket overlap). Used by
        # pipeline-data.ts to drive the per-country funnel without
        # double-counting schools that qualify for multiple buckets.
        # Sums to funnel_total across all rows.
        candidates_route = step03_candidates_df.copy()
        candidates_route["route_bucket"] = candidates_route[
            "funnel_status"
        ].map(
            {
                "already_geocoded": "fill_recovered",
                "already_cascade": "fill_recovered",
                "pending_missing": "fill_pending",
                "pending_out_of_bounds": "compare",
                "pending_mismatches": "compare",
                "pending_centroids": "compare",
            }
        ).fillna("other")
        # Attach attempted flag from ledger
        candidates_route["attempted"] = [
            (i, c) in ledger_keys
            for i, c in zip(
                candidates_route["iso"].astype(str),
                candidates_route["id_centro"].astype(str),
            )
        ]
        # Normalize sector to dashboard QCSector vocabulary (lowercase).
        # "Unknown" sector schools (rare — BHS) collapse into "total" only.
        candidates_route["sector_norm"] = candidates_route["sector"].map(
            {"Public": "public", "Private": "private"}
        ).fillna("unknown")

        # Aggregate per-sector first (public/private/unknown), then add a
        # synthetic "total" row that sums every school per (iso, route_bucket,
        # attempted). The dashboard's `total` toggle reads the synthetic row.
        per_sector_df = (
            candidates_route.groupby(
                ["iso", "sector_norm", "route_bucket", "attempted"],
                dropna=False,
            )
            .size()
            .reset_index(name="n_schools")
        )
        total_sector_df = (
            candidates_route.groupby(
                ["iso", "route_bucket", "attempted"],
                dropna=False,
            )
            .size()
            .reset_index(name="n_schools")
        )
        total_sector_df["sector_norm"] = "total"
        funnel_by_iso_sector_route_df = pd.concat(
            [per_sector_df, total_sector_df[per_sector_df.columns]],
            ignore_index=True,
        ).rename(columns={"sector_norm": "sector"})

        # Backwards-compat aliases (older dashboard code reads these)
        geocoding_overview["funnel_already_fixed"] = geocoding_overview["funnel_recovered"]
        geocoding_overview["funnel_pending_total"] = (
            geocoding_overview["funnel_validated"]
            + geocoding_overview["funnel_unprocessed"]
        )
        # Per-bucket pending breakdown (still useful as a route hint, but
        # most are validated rather than truly unprocessed — the headline
        # uses the three-way split above)
        geocoding_overview["funnel_pending_missing"] = int(
            (status == "pending_missing").sum()
        )
        geocoding_overview["funnel_pending_out_of_bounds"] = int(
            (status == "pending_out_of_bounds").sum()
        )
        geocoding_overview["funnel_pending_mismatches"] = int(
            (status == "pending_mismatches").sum()
        )
        geocoding_overview["funnel_pending_centroids"] = int(
            (status == "pending_centroids").sum()
        )
    else:
        funnel_by_iso_sector_route_df = pd.DataFrame(
            columns=["iso", "sector", "route_bucket", "attempted", "n_schools"]
        )
        for k in (
            "funnel_total",
            "funnel_already_geocoded",
            "funnel_already_cascade",
            "funnel_recovered",
            "funnel_validated",
            "funnel_unprocessed",
            "funnel_already_fixed",
            "funnel_pending_total",
            "funnel_pending_missing",
            "funnel_pending_out_of_bounds",
            "funnel_pending_mismatches",
            "funnel_pending_centroids",
            "funnel_compare_candidates",
            "funnel_compare_processed",
            "funnel_compare_unprocessed",
            "funnel_fill_candidates",
            "funnel_fill_recovered",
            "funnel_fill_attempted_failed",
            "funnel_fill_unprocessed",
        ):
            geocoding_overview[k] = 0

    geocoding_country_df = build_geocoding_country_summary(funnel_geocode_df, ground_truth_summary_df)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "pipeline_isos": PIPELINE_ISOS,
        "analysis_isos": ANALYSIS_ISOS,
        "analysis_excluded_isos": ANALYSIS_EXCLUDED_ISOS,
        "source_policy": {
            "current_school_counts": "Current per-country finalized CIMA files are canonical.",
            "georef_raw_definition": "Rows with current coordinates whose coordinate_source is original or blank.",
            "georef_current_definition": "Rows whose current latitud and longitud are non-null.",
            "verified_like_definition": "gps_validated + geocoded_street + geocoded_centroid.",
            "step01_status_policy": "dashboard_total_trace.csv is canonical for Step 01 reconciliation notes; new status labels are derived from trace_bucket plus needs_source_owner_followup.",
            "step04_policy": "results/geocode_results.csv is canonical for geocoder-universe, compare/fill, and acceptance counts.",
            "ground_truth_policy": "results/geocoder_ground_truth_summary.csv and results/geocoder_ground_truth_score_analysis.csv are canonical for geocoder ground-truth summaries.",
            "diagnostic_files": [
                str(PUBLISHED_SUMMARY_PATH),
                str(FINALIZE_SUMMARY_PATH),
            ],
        },
        "source_files": {
            "coverage": str(COVERAGE_PATH),
            "published_summary": str(PUBLISHED_SUMMARY_PATH),
            "finalize_summary": str(FINALIZE_SUMMARY_PATH),
            "step01_trace": str(STEP01_TRACE_PATH),
            "geocode_results": str(GEOCODE_RESULTS_PATH),
            "geocode_targets": str(GEOCODE_TARGETS_PATH),
            "qc_baseline": str(QC_BASELINE_PATH),
            "ground_truth_summary": str(GROUND_TRUTH_SUMMARY_PATH),
            "ground_truth_score_analysis": str(GROUND_TRUTH_SCORE_ANALYSIS_PATH),
        },
        "scope_totals": _records(scope_totals_df),
        "geocoding_overview": geocoding_overview,
    }

    tables = {
        "country_summary": country_df,
        "step01_reconciliation": reconciliation_df,
        "scope_totals": scope_totals_df,
        "coordinate_quality_totals_v2": quality_totals_df,
        "coordinate_quality_rollup": rollup_totals_df,
        "admin_concentration": admin_concentration_df,
        "id_edificio_summary": id_edificio_df,
        "id_edificio_outliers": id_edificio_outliers_df,
        "pop_validation_summary": pop_validation_df,
        "geocoding_country_summary": geocoding_country_df,
        "ground_truth_country_summary": ground_truth_country_df,
        "ground_truth_score_buckets": ground_truth_score_buckets_df,
        "source_checks": source_checks_df,
        "step01_status_definitions": _definitions_df(STEP01_STATUS_DEFINITIONS, "reconciliation_status"),
        "step01_root_cause_definitions": _definitions_df(STEP01_ROOT_CAUSE_DEFINITIONS, "root_cause"),
        "country_info_levels": country_info_levels_df,
        "geocode_targets": geocode_targets_df,
        "qc_baseline": qc_baseline_df,
        "funnel_by_iso_sector_route": funnel_by_iso_sector_route_df,
    }

    write_sqlite_bundle(out_dir / "dashboard.db", tables, metadata)

    country_df.to_csv(out_dir / "dashboard_country_summary.csv", index=False, encoding="utf-8")
    reconciliation_df.to_csv(out_dir / "dashboard_step01_reconciliation.csv", index=False, encoding="utf-8")
    source_checks_df.to_csv(out_dir / "dashboard_source_checks.csv", index=False, encoding="utf-8")
    quality_totals_df.to_csv(out_dir / "dashboard_coordinate_quality_totals_v2.csv", index=False, encoding="utf-8")
    rollup_totals_df.to_csv(out_dir / "dashboard_coordinate_quality_rollup.csv", index=False, encoding="utf-8")
    admin_concentration_df.to_csv(out_dir / "dashboard_admin_concentration.csv", index=False, encoding="utf-8")
    id_edificio_df.to_csv(out_dir / "dashboard_id_edificio_summary.csv", index=False, encoding="utf-8")
    id_edificio_outliers_df.to_csv(out_dir / "dashboard_id_edificio_outliers.csv", index=False, encoding="utf-8")
    pop_validation_df.to_csv(out_dir / "dashboard_pop_validation_summary.csv", index=False, encoding="utf-8")
    geocoding_country_df.to_csv(out_dir / "dashboard_geocoding_country_summary.csv", index=False, encoding="utf-8")
    ground_truth_country_df.to_csv(out_dir / "dashboard_ground_truth_country_summary.csv", index=False, encoding="utf-8")
    ground_truth_score_buckets_df.to_csv(out_dir / "dashboard_ground_truth_score_buckets.csv", index=False, encoding="utf-8")
    country_info_levels_df.to_csv(out_dir / "dashboard_country_info_levels.csv", index=False, encoding="utf-8")
    # geocode_targets is initially written by Step 02 finalize; we re-write it
    # here after attaching the pipeline_enabled / analysis_included flags so
    # the dashboard payload and the on-disk CSV stay in sync.
    geocode_targets_df.to_csv(out_dir / "dashboard_geocode_targets.csv", index=False, encoding="utf-8")
    # qc_baseline is initially written by Step 02 finalize; we re-write it here
    # after attaching the pipeline_enabled / analysis_included flags so the
    # dashboard payload and the on-disk CSV stay in sync.
    qc_baseline_df.to_csv(out_dir / "dashboard_qc_baseline.csv", index=False, encoding="utf-8")

    _write_json(out_dir / "dashboard_metadata.json", metadata)
    _write_json(out_dir / "dashboard_country_summary.json", _records(country_df))
    _write_json(out_dir / "dashboard_step01_reconciliation.json", _records(reconciliation_df))
    _write_json(out_dir / "dashboard_source_checks.json", _records(source_checks_df))
    _write_json(out_dir / "dashboard_coordinate_quality_totals_v2.json", _records(quality_totals_df))
    _write_json(out_dir / "dashboard_coordinate_quality_rollup.json", _records(rollup_totals_df))
    _write_json(out_dir / "dashboard_admin_concentration.json", _records(admin_concentration_df))
    _write_json(out_dir / "dashboard_id_edificio_summary.json", _records(id_edificio_df))
    _write_json(out_dir / "dashboard_id_edificio_outliers.json", _records(id_edificio_outliers_df))
    _write_json(out_dir / "dashboard_pop_validation_summary.json", _records(pop_validation_df))
    _write_json(out_dir / "dashboard_geocoding_country_summary.json", _records(geocoding_country_df))
    _write_json(out_dir / "dashboard_ground_truth_country_summary.json", _records(ground_truth_country_df))
    _write_json(out_dir / "dashboard_ground_truth_score_buckets.json", _records(ground_truth_score_buckets_df))
    _write_json(out_dir / "dashboard_country_info_levels.json", _records(country_info_levels_df))
    _write_json(out_dir / "dashboard_geocode_targets.json", _records(geocode_targets_df))
    _write_json(out_dir / "dashboard_qc_baseline.json", _records(qc_baseline_df))
    _write_json(
        out_dir / "dashboard_payload.json",
        {
            "metadata": metadata,
            "country_summary": _records(country_df),
            "step01_reconciliation": _records(reconciliation_df),
            "coordinate_quality_totals_v2": _records(quality_totals_df),
            "coordinate_quality_rollup": _records(rollup_totals_df),
            "admin_concentration": _records(admin_concentration_df),
            "id_edificio_summary": _records(id_edificio_df),
            "id_edificio_outliers": _records(id_edificio_outliers_df),
            "pop_validation_summary": _records(pop_validation_df),
            "rwi_scatter_col": _records(rwi_scatter_col_df),
            "rwi_decile_heatmap_col": _records(rwi_decile_heatmap_col_df),
            "rwi_decile_heatmap_multi": _records(rwi_decile_heatmap_multi_df),
            "rwi_validation_summary": _records(rwi_validation_summary_df),
            "bogota_rwi_cells": _records(bogota_rwi_cells_df),
            "bogota_schools": _records(bogota_schools_df),
            "bogota_boundary": bogota_boundary_geojson,
            "geocoding_overview": geocoding_overview,
            "geocoding_country_summary": _records(geocoding_country_df),
            "ground_truth_country_summary": _records(ground_truth_country_df),
            "ground_truth_score_buckets": _records(ground_truth_score_buckets_df),
            "source_checks": _records(source_checks_df),
            "country_info_levels": _records(country_info_levels_df),
            "geocode_targets": _records(geocode_targets_df),
            "qc_baseline": _records(qc_baseline_df),
            "funnel_by_iso_sector_route": _records(funnel_by_iso_sector_route_df),
        },
    )

    return {
        "country_summary": country_df,
        "step01_reconciliation": reconciliation_df,
        "source_checks": source_checks_df,
        "scope_totals": scope_totals_df,
        "coordinate_quality_totals_v2": quality_totals_df,
        "coordinate_quality_rollup": rollup_totals_df,
        "admin_concentration": admin_concentration_df,
        "geocoding_overview": geocoding_overview,
        "geocoding_country_summary": geocoding_country_df,
        "ground_truth_country_summary": ground_truth_country_df,
        "ground_truth_score_buckets": ground_truth_score_buckets_df,
        "metadata": metadata,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default=str(OUT_DIR),
        help="Output directory for SQLite/JSON/CSV dashboard exports.",
    )
    args = parser.parse_args()

    bundle = export_dashboard_bundle(Path(args.out_dir))
    scope_totals_df = bundle["scope_totals"]
    source_checks_df = bundle["source_checks"]

    print("=" * 72)
    print("Dashboard export bundle")
    print("=" * 72)
    for _, row in scope_totals_df.iterrows():
        print(
            f"  {row['scope']}: {int(row['countries'])} countries | "
            f"{int(row['total_schools']):,} schools | "
            f"{row['pct_georef_current']:.1f}% current georef"
        )

    mismatch_count = int(source_checks_df["any_mismatch"].fillna(False).astype(bool).sum()) if not source_checks_df.empty else 0
    print(f"  Source checks with mismatches: {mismatch_count}")
    print(f"  Wrote: {Path(args.out_dir) / 'dashboard.db'}")
    print(f"  Wrote: {Path(args.out_dir) / 'dashboard_payload.json'}")


if __name__ == "__main__":
    main()

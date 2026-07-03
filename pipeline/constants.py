"""Shared constants for the accessibility-platform pipeline.

Single source of truth for values previously duplicated across
`01_build_cima.py`, `02_qc_coordinates.py`, `04_geocode_missing.py`,
and `tests/conftest.py`.

Consumers:
    # Sibling scripts run from repo root as `python pipeline/XX_*.py`
    from constants import SCHEMA, COUNTRY_BBOX, ADM1_ALIASES

    # From tests (pytest adds repo root to sys.path via __init__.py)
    from pipeline.constants import SCHEMA, COUNTRY_BBOX, ALL_ISOS

History:
    2026-04-20  Created during P1.1 refactor. Canonical COUNTRY_BBOX
                values come from the prior `COUNTRY_BOUNDS` in
                `02_qc_coordinates.py` (those produced the current
                `results/qc_coordinate_summary.csv`). This simultaneously
                fixes the wider ECU bbox that lived in `04_geocode_missing.py`
                (was `(-92, -75)` — included Galápagos and open Pacific;
                now `(-81.1, -75.2)` — mainland only) and adds the PAN
                Comarca aliases that were missing from `05`'s local
                `ADM1_ALIASES`.
"""

from __future__ import annotations


SCHEMA: list[str] = [
    "id_centro",
    "nombre_centro",
    "sector",
    "nivel_primaria",
    "nivel_secbaja",
    "nivel_secalta",
    "latitud",
    "longitud",
    "adm0_pcode",
]

REQUIRED_COLUMNS: list[str] = SCHEMA


COUNTRY_SCOPE: dict[str, dict[str, object]] = {
    "ARG": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "BHS": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm1",
        "validation_tier": "limited",
        "data_status": "ready",
    },
    "BLZ": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm1",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "BOL": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm1",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "BRA": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "BRB": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "bbox_only",
        "validation_tier": "limited",
        "data_status": "limited_source",
    },
    "CHL": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "COL": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "CRI": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "DOM": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "ECU": {
        "pipeline_enabled": True,
        "analysis_included": True,
        # Promoted from adm1 to adm2 once Cod_Cantón -> EC{:04d} code-based
        # match landed (2026-05). At adm1 the resolver was ignoring ADM2
        # mismatches, leaving 111 schools labeled gps_validated even when
        # their coord fell in a different cantón than declared. Now those
        # 111 surface as adm_mismatch and route to step-04 compare.
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "GTM": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "GUY": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm1",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "HND": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "HTI": {
        "pipeline_enabled": True,
        "analysis_included": False,
        "final_match_level": "bbox_only",
        "validation_tier": "not_ready",
        "data_status": "limited_source",
    },
    "JAM": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "spatial_only",
        "validation_tier": "limited",
        "data_status": "no_raw_ministry_file",
    },
    "MEX": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "PAN": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "PER": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "PRY": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "SLV": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "SUR": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm2",
        "validation_tier": "standard",
        "data_status": "ready",
    },
    "URY": {
        "pipeline_enabled": True,
        "analysis_included": True,
        "final_match_level": "adm1",
        "validation_tier": "standard",
        "data_status": "ready",
    },
}

# Final accessibility indicators are intended to publish a harmonized
# aggregation ladder (ADM0, ADM1, ADM2) across the analysis scope.
# This is separate from `final_match_level`, which only controls how strict
# the coordinate QC comparison can be for each country.
DEFAULT_INDICATOR_LEVELS: tuple[str, ...] = ("adm0", "adm1", "adm2")
for _iso, _meta in COUNTRY_SCOPE.items():
    _meta.setdefault(
        "indicator_levels",
        DEFAULT_INDICATOR_LEVELS if _meta["analysis_included"] else tuple(),
    )

PIPELINE_ISOS: list[str] = [iso for iso, meta in COUNTRY_SCOPE.items() if meta["pipeline_enabled"]]
ANALYSIS_ISOS: list[str] = [iso for iso, meta in COUNTRY_SCOPE.items() if meta["analysis_included"]]
ANALYSIS_EXCLUDED_ISOS: list[str] = [iso for iso, meta in COUNTRY_SCOPE.items() if not meta["analysis_included"]]
LIMITED_VALIDATION_ISOS: list[str] = [
    iso
    for iso, meta in COUNTRY_SCOPE.items()
    if meta["analysis_included"] and meta["validation_tier"] != "standard"
]

# Backward-compatible alias used by existing tests and scripts.
# Semantically this is the published analytical universe (currently 22 countries
# after BHS onboarding 2026-05-13; HTI remains excluded — raw is unreadable .xls).
ALL_ISOS: list[str] = ANALYSIS_ISOS

# Countries reported at admin1 ONLY. Their adm2 are unnamed census sections
# (placeholder names "n.aX"), not real municipalities, and the education budget
# is national (URY: ANEP) — so subnational reporting stops at Departamento.
# Indicator aggregation (Step 10/10b) must NOT emit adm2 rows for these ISOs.
# NOTE: BOL/GUY are final_match_level=adm1 but DO have real, named adm2, so they
# are intentionally NOT here. See project_ury_adm_level_decision.
ADM1_ONLY_ISOS: set[str] = {"URY"}


COUNTRY_BBOX: dict[str, tuple[float, float, float, float]] = {
    "ARG": (-56.0, -21.0, -74.0, -53.0),
    "BHS": ( 20.9,  27.3, -79.4, -72.7),  # widened west from -79.0 to include Bimini chain (Bimini schools at ~-79.30)
    "BLZ": ( 15.5,  18.6, -89.5, -87.3),
    "BOL": (-23.0,  -9.0, -70.0, -57.0),
    "BRA": (-34.0,   6.0, -74.0, -34.0),
    "BRB": ( 13.0,  13.4, -59.7, -59.4),
    "CHL": (-56.0, -17.5, -76.0, -66.0),
    "COL": ( -4.3,  12.5, -79.1, -66.8),  # mainland; San Andrés y Providencia (lat>12.5, lon~-81.7) classified as remote_territory_or_island
    "CRI": (  8.0,  11.3, -86.0, -82.5),
    "DOM": ( 17.5,  20.0, -72.2, -68.2),
    "ECU": ( -5.1,   1.5, -81.1, -75.2),
    "GTM": ( 13.5,  18.0, -92.5, -88.0),
    "GUY": (  1.0,   9.0, -62.0, -56.0),
    "HND": ( 12.5,  16.5, -90.0, -83.0),
    "HTI": ( 17.9,  20.3, -74.5, -71.6),
    "JAM": ( 17.7,  18.6, -78.4, -76.1),
    "MEX": ( 14.0,  33.0,-118.0, -86.0),
    "PAN": (  7.1,   9.7, -83.1, -77.1),
    "PER": (-18.5,   0.5, -82.0, -68.0),
    "PRY": (-28.0, -19.0, -63.0, -54.0),
    "SLV": ( 13.0,  14.5, -90.2, -87.6),
    "SUR": (  1.8,   6.1, -58.1, -53.9),
    "URY": (-35.2, -30.0, -58.5, -53.0),
}


ADM1_ALIASES: dict[str, dict[str, str]] = {
    "MEX": {
        "ciudad de mexico": "distrito federal",
        "queretaro": "queretaro de arteaga",
    },
    "GTM": {"ciudad capital": "guatemala"},
    "PRY": {"capital": "asuncion"},
    "GUY": {
        "region 1": "barima-waini",
        "region 2": "pomeroon-supenaam",
        "region 3": "essequibo islands-west demerara",
        "region 4": "demerara-mahaica",
        "region 5": "mahaica berbice",
        "region 6": "east berbice-corentyne",
        "region 7": "cuyuni-mazaruni",
        "region 8": "potaro-siparuni",
        "region 9": "upper takutu-upper essequibo",
        "region 10": "upper demerara-berbice",
        "region 11": "demerara-mahaica",
    },
    "PAN": {
        "comarca ngabe bugle": "ngobe bugle",
        "comarca ngäbe bugle": "ngobe bugle",
        "comarca guna yala": "kuna yala",
        "comarca embera": "embera",
        "comarca embera-wounaan": "embera",
    },
}


# Aggregation maps — used when the raw MoE admin column is COARSER than the
# BID polygon set (1-to-many). admin_match treats `raw_name -> any polygon in
# the listed set` as MATCH (not MISMATCH). Distinct from ADM1_ALIASES (1-to-1
# spelling correction): aggregations cover real definitional differences
# between MoE groupings and BID polygon granularity.
#
# Keys MUST match the output of qc_core.normalize_name (lowercase, accents
# stripped, whitespace collapsed, apostrophes preserved).
#
# BHS: the Ministry of Education groups schools by island family (ABACOS,
# ANDROS, ELEUTHERA, GRAND BAHAMA, EXUMA AND CAYS) but BID's lac-level-1
# polygon set splits each family into multiple districts. A school whose GPS
# lands in any listed child of its raw aggregator is a valid MATCH; coords
# outside the set stay flagged as adm_mismatch for review (genuine errors).
ADM1_AGGREGATIONS: dict[str, dict[str, set[str]]] = {
    "BHS": {
        "abacos": {
            "central abaco", "north abaco", "south abaco",
            "hope town", "moore's island", "grand cay",
        },
        "andros": {
            "central andros", "north andros", "south andros", "mangrove cay",
        },
        "eleuthera": {
            "central eleuthera", "north eleuthera", "south eleuthera",
            "harbour island", "spanish wells",
        },
        "exuma and cays": {"exuma", "black point", "ragged island"},
        "grand bahama": {
            "city of freeport", "east grand bahama", "west grand bahama",
        },
        "sweetings cay": {"east grand bahama"},  # SC in BID is part of E Grand Bahama
    },
}

ADM2_AGGREGATIONS: dict[str, dict[str, set[str]]] = {
    # No countries currently require ADM2 aggregation. Reserved for future
    # ressort/distrito groupings that don't match BID's level-2 partition.
}


# ADM2 aliases — same shape as ADM1_ALIASES (per ISO, normalized lowercase
# ASCII keys -> canonical polygon name). Keys MUST match the output of
# qc_core.normalize_name (lowercase, accents stripped, whitespace collapsed,
# punctuation preserved as-is).
#
# SUR aliases recover ~83 ressort mismatches that are pure abbreviations or
# spelling variants between the MEDOWS Suriname School List and BID's
# lac-level-2 polygons. They are NOT general spelling-correction rules — only
# patterns observed in the raw data. The remaining ~210 SUR ressort mismatches
# are real coord-vs-polygon disagreements (school coords fall in a different
# ressort than the raw declares, usually within the same district).
ADM2_ALIASES: dict[str, dict[str, str]] = {
    "SUR": {
        # Abbreviations
        "boven sur.":      "boven suriname",
        "nw. nickerie":    "nieuw nickerie",
        "westelijk pld.":  "westelijke polders",
        "saramacca pld.":  "saramacca polder",
        "oostelijk pld.":  "oostelijke polders",
        "nw. amsterdam":   "nieuw amsterdam",
        "brok. centrum":   "centrum",
        # Spelling variants
        "koewarasan":      "kwarasan",
        "bronsweg":        "brownsweg",
        "marchalkreek":    "marechallkreek",
        "johanna marie":   "johanna maria",
        "nieuwe grond":    "de nieuwe grond",
    },
    # ECU aliases — DEFENSIVE FALLBACK only. Primary ADM2 matching now uses
    # code-based path (Cod_Cantón → EC{:04d} via COUNTRY_CONFIG, code-based
    # always wins in qc_core._status_for_level). These aliases bridge the
    # legacy name-based path for any row missing the code. Mechanism: BID's
    # lac-level-2.shp has inconsistent encoding for some ECU cantón names —
    # `ñ` was substituted with `ð` (U+00F0, byte 0xF0 vs 0xF1) in entries
    # like 'Rumiðahui', 'Logroðo', 'Caðar'. `normalize_name` decomposes ñ→n
    # via NFKD but cannot decompose ð, so ASCII-strip drops it entirely:
    # raw 'RUMIÑAHUI' → 'ruminahui'; polygon 'Rumiðahui' → 'rumiahui'.
    # The aliases also cover two abbreviation pairs MINEDUC writes in long
    # form (CORONEL, GENERAL) but BID stores abbreviated (Crnel., Gnral.).
    "ECU": {
        # ñ ↔ ð byte-encoding pairs (raw normalized → polygon normalized)
        "ruminahui":                       "rumiahui",
        "logrono":                         "logroo",
        "canar":                           "caar",
        # Abbreviation + ñ↔ð combos
        "coronel marcelino mariduena":     "crnel. marcelino mariduea",
        "general antonio elizalde":        "gnral. antonio elizalde",
    },
}


# ---------------------------------------------------------------------------
# Schema v1 (legacy) — produced by `04_geocode_missing.py` directly.
# Kept for reference and migration. New code MUST use the v2 enums below.
# ---------------------------------------------------------------------------
COORDINATE_QUALITY_VALUES: dict[str, str] = {
    "gps": "Confirmed GPS from ministry, QC-validated (spatial match)",
    "street": "Geocoded at street level (score >= 95, error < 5 km)",
    "centroid": "Admin centroid (corregimiento/municipio, area <= 314 km², error ~2-5 km)",
    "centroid_flag": "Admin centroid with high bias (area > 314 km², error ~5-25 km) — flag for QC review",
    "flag": "GPS preserved but spatial mismatch with declared address",
    "empty": "No coordinate available",
}


# ---------------------------------------------------------------------------
# Schema v2 — canonical CIMA enrichment after Step 02 finalize.
# Step 02 is the SOLE owner of `coordinate_quality`, `coordinate_quality_reason`,
# `qc_centroid_bias`, `adm1_pcode`, `adm2_pcode` and the `qc_*` evidence columns.
# Step 05 only writes geocoder evidence; it does NOT set the final label.
# ---------------------------------------------------------------------------
QC_EVIDENCE_VERSION: int = 2

# Final label — mutually exclusive, worst flag wins (precedence below).
COORDINATE_QUALITY: dict[str, str] = {
    "missing":            "No usable coordinate after fill",
    "out_of_bounds":      "Coordinate outside country bbox",
    "swapped":            "lat/lon likely inverted",
    "adm_mismatch":       "Coordinate falls outside declared admin polygon (best level available)",
    "cluster_centroid":   "Original GPS sits in a cluster of >=5 schools at the same point — covert centroid",
    "geocoder_disagrees": "Strong geocoder evidence contradicts the original GPS location",
    "boundary_zone":      "Original GPS and geocoder both fall in same admin opposite to the raw declaration, within 5 km of the raw polygon edge — likely a boundary-overlap case (raw admin assignment off-by-one)",
    "geocoded_centroid":  "Coordinate filled by geocoder/cascade at centroid precision",
    "geocoded_street":    "Coordinate filled by geocoder at street precision (score >= 95)",
    "gps_validated":      "Original GPS confirmed at best admin level available, no centroid pattern",
    "gps_unverified":     "Coordinate present but no evidence strong enough to validate further",
}

# Resolution order for finalize — first match wins (worst flag wins).
# `boundary_zone` is a softened sibling of `adm_mismatch`: when a school
# qualifies for adm_mismatch BUT geocoder and GPS agree on the same wrong-
# per-raw admin AND the school sits within 5 km of the raw-declared polygon
# edge, the milder boundary_zone label is emitted instead.
COORDINATE_QUALITY_PRECEDENCE: tuple[str, ...] = (
    "missing",
    "out_of_bounds",
    "swapped",
    "adm_mismatch",
    "cluster_centroid",
    "geocoder_disagrees",
    "boundary_zone",
    "geocoded_centroid",
    "geocoded_street",
    "gps_validated",
    "gps_unverified",
)

COORDINATE_QUALITY_VALUES_V2: frozenset[str] = frozenset(COORDINATE_QUALITY)

# Reason that drove the final label — for auditability in dashboard / reports.
COORDINATE_QUALITY_REASONS: dict[str, str] = {
    "bbox":                    "Coordinate failed bounding-box check",
    "swapped":                 "lat/lon swap detected",
    "adm2_mismatch":           "Polygon ADM2 mismatch vs raw declared ADM2",
    "adm1_mismatch":           "Polygon ADM1 mismatch vs raw declared ADM1",
    "cluster_ge5":             "Coordinate sits in a cluster of >=5 schools at the same point",
    "geocoder_compare":        "Geocoder evidence (street/centroid + ADM2 mismatch or distance >= 10 km)",
    "boundary_zone_<5km":      "GPS and geocoder concur on opposite admin to the raw declaration, within 5 km of raw polygon edge — boundary-zone flag",
    "geocoder_low_confidence": "Geocoder returned uncertain match — not enough to flag GPS but not validating it either",
    "fill_centroid":           "Filled with centroid (geocoder ACCEPT_CENTROID or PAN-style cascade)",
    "fill_street":             "Filled with street-level geocoded coordinate",
    "validated":               "GPS confirmed at best admin level available + no cluster signal",
    "default_unverified":      "Default when no other rule fired and no validation evidence available",
    "missing":                 "No coordinate available",
}
COORDINATE_QUALITY_REASON_VALUES: frozenset[str] = frozenset(COORDINATE_QUALITY_REASONS)

# Provenance of the current (latitud, longitud) in CIMA.
COORDINATE_SOURCES: frozenset[str] = frozenset({
    "original",          # straight from ministry raw
    "geocoded",          # from Step 05 ArcGIS/Photon/Nominatim
    "centroid_cascade",  # from PAN-style ADM3 GADM centroid cascade
})

# Sub-classification for centroid coordinates without inflating the main taxonomy.
QC_CENTROID_BIASES: frozenset[str] = frozenset({
    "normal",   # centroid of a small admin unit (e.g. PAN ADM3 area <= 314 km²)
    "high",     # centroid of a large admin unit (e.g. PAN ADM3 area > 314 km²)
    "unknown",  # cluster_centroid origin — bias magnitude not measured directly
})

# Highest admin level the QC actually validated against for each school.
QC_MATCH_LEVELS: frozenset[str] = frozenset({"ADM2", "ADM1", "SPATIAL_ONLY", "NONE"})

# Per-level admin status — written for both ADM1 and ADM2 even when only one drives the label.
QC_ADM_STATUSES: frozenset[str] = frozenset({
    "MATCH",
    "MISMATCH",
    "NO_RAW_ADM",   # raw didn't declare an admin name to compare against
    "NO_POLYGON",   # coord didn't fall in any polygon at this level
    "NO_DATA",      # no coordinate to test
    "NOT_RUN",      # this level wasn't evaluated for this country (per final_match_level policy)
})

# Scope/use classification emitted alongside `coordinate_quality`.
# This is intentionally additive: `coordinate_quality` remains the canonical
# QC label, while `qc_scope_class` answers whether an out-of-bbox point is
# still inside the country's ADM0 territory (e.g. island / remote territory).
QC_SCOPE_CLASS: dict[str, str] = {
    "missing":                    "No usable coordinate after normalization",
    "inside_mainland_bbox":       "Coordinate is inside COUNTRY_BBOX and inside national territory",
    "remote_territory_or_island": "Coordinate is outside COUNTRY_BBOX but still inside the national ADM0 polygon",
    "near_border_review":         "Coordinate is outside the national ADM0 polygon but within 5 km of its boundary",
    "outside_country":            "Coordinate is outside the national ADM0 polygon and not near the border",
    "invalid_numeric":            "Coordinate is numerically impossible in EPSG:4326 (|lat|>90 or |lon|>180)",
}
QC_SCOPE_CLASS_VALUES: frozenset[str] = frozenset(QC_SCOPE_CLASS)

# The BID ADM0 shapefile does not use the same 3-letter codes as the pipeline
# contract (e.g. BR vs BRA, EC vs ECU). Use this map for any ADM0 territory
# containment test instead of assuming shapefile codes match `adm0_pcode`.
ADM0_BOUNDARY_PCODE_MAP: dict[str, str] = {
    "ARG": "ARG",
    "BHS": "BHS",
    "BLZ": "BZ",
    "BOL": "BO",
    "BRA": "BR",
    "BRB": "BB",
    "CHL": "CL",
    "COL": "CO",
    "CRI": "CR",
    "DOM": "DO",
    "ECU": "EC",
    "GTM": "GT",
    "GUY": "GY",
    "HND": "HN",
    "HTI": "HT",
    "JAM": "JAM",
    "MEX": "MX",
    "NIC": "NI",
    "PAN": "PA",
    "PER": "PE",
    "PRY": "PY",
    "SLV": "SV",
    "SUR": "SR",
    "TTO": "TT",
    "URY": "UY",
    "VEN": "VE",
}

# Allowed values for COUNTRY_SCOPE["final_match_level"] — keep in sync with COUNTRY_SCOPE.
FINAL_MATCH_LEVELS: frozenset[str] = frozenset({"adm2", "adm1", "spatial_only", "bbox_only"})

# Canonical CIMA column order AFTER Step 02 finalize.
# `save_cima` in Step 01 preserves any non-SCHEMA columns via id_centro join,
# so this list documents the contract; downstream code should consume by name.
CIMA_ENRICHED_COLUMNS: tuple[str, ...] = (
    # base schema (Step 01)
    "id_centro", "nombre_centro", "sector",
    "nivel_primaria", "nivel_secbaja", "nivel_secalta",
    "latitud", "longitud", "adm0_pcode", "id_national", "year",
    # Step 05 evidence — filled/geocoded coordinate + geocoder metadata
    "latitud_geocoded", "longitud_geocoded", "geocode_distance_km",
    "arcgis_score", "geocode_source", "geocode_precision", "acceptance",
    # Step 02 enrichment — admin pcodes from spatial join
    "adm1_pcode", "adm2_pcode",
    # Step 02 evidence — spatial QC + territory scope classification
    "qc_in_bounds", "qc_scope_class", "include_in_spatial_indicators", "qc_swapped",
    "qc_adm1_status", "qc_adm2_status", "qc_match_level",
    # Step 02 evidence — distance from GPS to raw-declared admin polygon edge.
    # Populated only for rows where the GPS falls outside the raw-declared
    # polygon AND the polygon is resolvable. Used by the boundary_zone label
    # to identify near-border cases (< 5 km) where GPS and geocoder concur
    # but disagree with the raw admin assignment.
    "qc_distance_to_raw_polygon_km",
    # Step 02 evidence — clusters
    "qc_cluster_size_exact", "qc_cluster_diff_addr_exact",
    "qc_cluster_size_50m", "qc_cluster_diff_addr_50m",
    # Extended cluster_centroid signals (2026-05-13): n in [2,4] placeholder
    # detection via categorical raw admin/locality + n=2 frontier rescue.
    "qc_cluster_diff_admin_locality", "qc_n2_frontier_rescue",
    # Step 05 evidence — compare/admin checks (preserved as-is when present)
    "geocoded_in_adm1", "geocoded_in_adm2", "geo_adm1_check", "geo_adm2_check",
    "original_in_adm1", "original_in_adm2", "orig_adm1_check", "orig_adm2_check",
    # Step 01 / Step 02 admin-code evidence preserved for deterministic matching
    "raw_adm1_code", "raw_adm2_code",
    # Step 02 derived final
    "coordinate_source", "coordinate_quality", "coordinate_quality_reason",
    "qc_centroid_bias", "qc_evidence_version",
)

# ---------------------------------------------------------------------------
# Migration: legacy CIMA columns → schema v2
# Used by the finalize resolver in pipeline/qc_core.py to read CIMAs already
# enriched by the v1 Step 05 (BRA, COL, HND, MEX, PAN, SUR).
# ---------------------------------------------------------------------------
LEGACY_COORDINATE_SOURCE_MIGRATION: dict[str, dict[str, str]] = {
    # PAN cascade (B-2) used these source values; collapse to centroid_cascade,
    # carry the area-size signal in qc_centroid_bias.
    "adm3_centroid":       {"coordinate_source": "centroid_cascade", "qc_centroid_bias": "normal"},
    "adm3_centroid_large": {"coordinate_source": "centroid_cascade", "qc_centroid_bias": "high"},
    # Pass-through (already canonical or trivially mapped)
    "original":            {"coordinate_source": "original",         "qc_centroid_bias": "unknown"},
    "geocoded":            {"coordinate_source": "geocoded",         "qc_centroid_bias": "unknown"},
}


__all__ = [
    "SCHEMA",
    "REQUIRED_COLUMNS",
    "COUNTRY_SCOPE",
    "DEFAULT_INDICATOR_LEVELS",
    "PIPELINE_ISOS",
    "ANALYSIS_ISOS",
    "ANALYSIS_EXCLUDED_ISOS",
    "LIMITED_VALIDATION_ISOS",
    "ALL_ISOS",
    "COUNTRY_BBOX",
    "ADM1_ALIASES",
    "ADM1_AGGREGATIONS",
    "ADM2_ALIASES",
    "ADM2_AGGREGATIONS",
    # v1 legacy
    "COORDINATE_QUALITY_VALUES",
    # v2 schema
    "QC_EVIDENCE_VERSION",
    "COORDINATE_QUALITY",
    "COORDINATE_QUALITY_PRECEDENCE",
    "COORDINATE_QUALITY_VALUES_V2",
    "COORDINATE_QUALITY_REASONS",
    "COORDINATE_QUALITY_REASON_VALUES",
    "COORDINATE_SOURCES",
    "QC_CENTROID_BIASES",
    "QC_MATCH_LEVELS",
    "QC_ADM_STATUSES",
    "QC_SCOPE_CLASS",
    "QC_SCOPE_CLASS_VALUES",
    "ADM0_BOUNDARY_PCODE_MAP",
    "FINAL_MATCH_LEVELS",
    "CIMA_ENRICHED_COLUMNS",
    "LEGACY_COORDINATE_SOURCE_MIGRATION",
]

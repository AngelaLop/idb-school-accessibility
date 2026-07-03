"""
Phase B-1: Geocode schools missing coordinates or with coordinate-address mismatches.

Targets:
  - Schools with no coordinates (latitud is NaN)
  - Schools at (0,0) — treated as missing
  - Schools flagged as MISMATCH or OUT_OF_BOUNDS in QC
  - Schools with duplicate coordinates but different addresses

Geocoder cascade: ArcGIS → Photon → Nominatim (all free, no API key).

Must be run from project root:
    uv run python pipeline/04_geocode_missing.py --dry-run
    uv run python pipeline/04_geocode_missing.py --countries MEX
    uv run python pipeline/04_geocode_missing.py
"""

import argparse
import json
import sys
import time
from math import radians, sin, cos, asin, sqrt
from pathlib import Path

import unicodedata

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path("data/schools/AR")
RESULTS = Path("results")
QC_REPORT = RESULTS / "qc_coordinate_report.csv"
QC_DUPES = RESULTS / "qc_duplicate_coordinates.csv"
CACHE_PATH = RESULTS / "geocode_cache.json"

# ADM1 aliases and country bounding boxes — single source of truth in
# pipeline/constants.py. Previously duplicated here; the local copy diverged
# (ECU bbox was too wide, PAN Comarca block was missing).
from constants import ADM1_ALIASES, COUNTRY_BBOX, COUNTRY_SCOPE
import qc_core

# Countries with street-level addresses (Phase B-1).
# Reuses COUNTRY_CONFIG from 02_qc_coordinates.py via import.
# Audit on 2026-05-03: every country here has raw_street populated for
# >=93% of schools. CHL was previously listed but has 0% raw_street; step-03
# classifies it as Type B and it must wait for cascade implementation.
# URY joined the list on 2026-05-04 after preprocess_ury() was rewritten to
# enrich URY_admin.csv with addresses extracted from centros_clean.csv.
PHASE_B1_ISOS = [
    "MEX", "BRA", "COL", "ARG", "HND", "PRY", "GTM",
    "CRI", "PER", "BLZ", "BOL", "GUY", "SUR", "URY",
]

# Countries with admin-level centroids only (Phase B-2).
# No street addresses available; use corregimiento/parroquia/municipio centroids.
# CHL is Type B per step-03 but cascade is not yet implemented for it.
# (URY was previously in this category but moved to Phase B-1 once
# preprocess_ury() was rewritten to enrich addresses from centros_clean.csv.)
PHASE_CENTROID_ISOS = ["PAN", "DOM", "ECU", "SLV"]

CENTROID_LOOKUP_POLICIES = {
    "PAN": {
        "lookup_filename": "PAN_corregimiento_geometric_centroids.csv",
        "school_key_cols": ["raw_adm2", "raw_locality"],
        "lookup_key_cols": ["raw_dist_norm", "raw_corr_norm"],
        "join_label": "corregimiento + distrito",
        "normalize_key": True,
        "apply_aliases": False,
        "source_small": "centroid_cascade",
        "source_large": "centroid_cascade",
    },
    "ECU": {
        "lookup_filename": "ECU_parroquia_centroids.csv",
        "school_key_cols": ["raw_locality_code"],
        "lookup_key_cols": ["lookup_parroquia_code"],
        "join_label": "parroquia",
        "normalize_key": False,
        "apply_aliases": False,
        "source_small": "centroid_cascade",
        "source_large": "centroid_cascade",
    },
    "DOM": {
        "lookup_filename": "DOM_municipio_centroids.csv",
        "school_key_cols": ["raw_adm1", "raw_adm2"],
        "lookup_key_cols": ["provincia_norm", "municipio_norm"],
        "join_label": "municipio + provincia",
        "normalize_key": True,
        "apply_aliases": False,
        "source_small": "centroid_cascade",
        "source_large": "centroid_cascade",
    },
    "SLV": {
        "lookup_filename": "SLV_municipio_centroids.csv",
        "school_key_cols": ["raw_adm1", "raw_adm2"],
        "lookup_key_cols": ["departamento_norm", "municipio_norm"],
        "join_label": "municipio + departamento",
        "normalize_key": True,
        "apply_aliases": False,
        "source_small": "centroid_cascade",
        "source_large": "centroid_cascade",
    },
}


# ---------------------------------------------------------------------------
# Geocoder setup
# ---------------------------------------------------------------------------

def setup_geocoders():
    """Initialize geocoder cascade: ArcGIS → Photon → Nominatim."""
    from geopy.geocoders import ArcGIS, Photon, Nominatim
    from geopy.extra.rate_limiter import RateLimiter

    geocoders = []

    try:
        _arc = ArcGIS(user_agent="idb_school_accessibility")
        geocoders.append(("arcgis", RateLimiter(_arc.geocode, min_delay_seconds=0.3)))
    except Exception as e:
        print(f"  ArcGIS init failed: {e}")

    try:
        _pho = Photon(user_agent="idb_school_accessibility")
        geocoders.append(("photon", RateLimiter(_pho.geocode, min_delay_seconds=0.35)))
    except Exception as e:
        print(f"  Photon init failed: {e}")

    try:
        _nom = Nominatim(user_agent="idb_school_accessibility_phase_b")
        geocoders.append(("nominatim", RateLimiter(_nom.geocode, min_delay_seconds=1.1)))
    except Exception as e:
        print(f"  Nominatim init failed: {e}")

    return geocoders


# ---------------------------------------------------------------------------
# Target identification
# ---------------------------------------------------------------------------

def _detect_centroids_in_coords(cima, addr_df, threshold=5):
    """Detect schools whose coordinates are likely municipal centroids.

    Returns set of id_centro for schools at shared points (>=threshold)
    where addresses are genuinely different (not same building).
    """
    georef = cima[cima["latitud"].notna() & cima["longitud"].notna()].copy()
    if georef.empty:
        return set()

    georef["geo_key"] = (
        georef["latitud"].round(3).astype(str) + "," +
        georef["longitud"].round(3).astype(str)
    )
    point_counts = georef.groupby("geo_key")["id_centro"].count()
    shared_points = set(point_counts[point_counts >= threshold].index)
    if not shared_points:
        return set()

    candidates = georef[georef["geo_key"].isin(shared_points)].copy()

    # Filter: only include if addresses are genuinely different (not same building)
    # Same building = same street + same municipality at that point
    if addr_df is not None and not addr_df.empty:
        candidates = candidates.merge(
            addr_df[["id_centro", "raw_street", "raw_adm2"]],
            on="id_centro", how="left",
        )
        same_building_ids = set()
        for geo_key, group in candidates.groupby("geo_key"):
            streets = group["raw_street"].fillna("").str.lower().str.strip().unique()
            adm2s = group["raw_adm2"].fillna("").str.lower().str.strip().unique()
            streets = [s for s in streets if s and s not in ("nan", "none", "")]
            adm2s = [a for a in adm2s if a and a not in ("nan", "none", "")]
            if len(adm2s) <= 1 and len(streets) <= 1:
                same_building_ids |= set(group["id_centro"])

        return set(candidates["id_centro"]) - same_building_ids
    else:
        return set(candidates["id_centro"])


def identify_targets(iso, addr_df=None):
    """Identify all schools needing geocoding for a country."""
    cima_path = BASE / iso / "processed" / f"{iso}_total_cima.csv"
    if not cima_path.exists():
        return pd.DataFrame(), set(), set(), set(), set(), set()

    cima = pd.read_csv(cima_path, dtype={"id_centro": str})

    # Preferred path: if Step 02 finalize already enriched the CIMA with v2
    # evidence columns, derive targets directly from the current base rather
    # than from legacy side-report CSVs.
    required_v2 = {"id_centro", "qc_in_bounds", "qc_adm1_status", "qc_adm2_status"}
    if required_v2.issubset(cima.columns):
        qc_evidence = cima[list(required_v2)].copy()
        targets = qc_core.compute_geocode_targets(cima, addr_df=addr_df, qc_evidence=qc_evidence)
        missing = targets["missing"]
        zeros = targets["zeros"]
        # Preserve the old function contract: Step 05 compare targets treat
        # out-of-bounds schools the same as mismatches.
        mismatches = targets["mismatches"] | targets.get("out_of_bounds", set())
        dup_addr = targets["dup_addr"]
        coord_centroids = targets["centroids"]
        return cima, missing, zeros, mismatches, dup_addr, coord_centroids

    # 1. Missing coords
    missing = set(cima[cima["latitud"].isna()]["id_centro"])

    # 2. Zero coords — either lat or lon is zero (partial zeros are also invalid)
    zeros = set(cima[(cima["latitud"] == 0) | (cima["longitud"] == 0)]["id_centro"])

    # 3. QC mismatches + OOB
    mismatches = set()
    if QC_REPORT.exists():
        qc = pd.read_csv(QC_REPORT, dtype={"id_centro": str})
        qc_iso = qc[qc["iso"] == iso]
        mismatches = set(qc_iso[qc_iso["qc_status"].isin(["MISMATCH", "OUT_OF_BOUNDS"])]["id_centro"])

    # 4. Duplicate coords with different addresses (from QC report)
    dup_addr = set()
    if QC_DUPES.exists():
        dupes = pd.read_csv(QC_DUPES, dtype={"id_centro": str})
        dupes_iso = dupes[(dupes["iso"] == iso) & (dupes["diff_addr"] == True)]
        dup_addr = set(dupes_iso["id_centro"])

    # 5. Centroid detection on ALL coords (original + geocoded)
    #    Schools at shared points (>=5) with genuinely different addresses
    #    Excludes same-building (same street + same municipality)
    coord_centroids = _detect_centroids_in_coords(cima, addr_df, threshold=5)

    return cima, missing, zeros, mismatches, dup_addr, coord_centroids


# ---------------------------------------------------------------------------
# Address loading (reuse COUNTRY_CONFIG from 02_qc_coordinates)
# ---------------------------------------------------------------------------

def load_country_config():
    """Import COUNTRY_CONFIG and extract_addresses from 02_qc_coordinates."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("qc_mod", "pipeline/02_qc_coordinates.py")
    qc_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qc_module)
    return qc_module.COUNTRY_CONFIG, qc_module.extract_addresses


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    """Distance in km between two points."""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


def _normalize_admin(s):
    if pd.isna(s): return ""
    s = str(s).lower().strip()
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _admin_match(declared, found, iso=None):
    d, f = _normalize_admin(declared), _normalize_admin(found)
    if not d or not f: return "NO_DATA"
    if d == f: return "MATCH"
    # Check aliases (both directions: raw→boundary and boundary→raw)
    if iso and iso in ADM1_ALIASES:
        aliases = ADM1_ALIASES[iso]
        if aliases.get(d) == f or aliases.get(f) == d:
            return "MATCH"
    # Partial match: only if the shorter string is >80% of the longer one.
    # Prevents "santander" matching "norte de santander" or "cauca" matching "valle del cauca".
    if d in f or f in d:
        ratio = min(len(d), len(f)) / max(len(d), len(f))
        if ratio > 0.8:
            return "PARTIAL"
    return "MISMATCH"


# Cache for admin boundaries (loaded once per session)
_adm1_cache = {}
_adm2_cache = {}

def _load_admin_boundaries(iso):
    """Load admin boundaries for a country. Cached per session."""
    if iso in _adm1_cache:
        return _adm1_cache[iso], _adm2_cache.get(iso)

    adm1_path = Path("data/bounderys/LAC/level 1/lac-level-1.shp")
    adm2_path = Path("data/bounderys/LAC/level 2/lac-level-2.shp")

    adm1, adm2 = None, None
    if adm1_path.exists():
        all_adm1 = gpd.read_file(adm1_path)
        adm1 = all_adm1[all_adm1["ADM0_PCODE"] == iso]
        _adm1_cache[iso] = adm1
    if adm2_path.exists():
        all_adm2 = gpd.read_file(adm2_path)
        adm2 = all_adm2[all_adm2["ADM0_PCODE"] == iso]
        _adm2_cache[iso] = adm2
    return adm1, adm2


def validate_geocoded(results_df, addr_df, iso):
    """Spatial validation: check if geocoded coords fall in declared admin unit.

    Adds columns: geocoded_in_adm1, geocoded_in_adm2, adm1_check, adm2_check, acceptance.

    Acceptance levels:
      ACCEPT — correct municipality + street/school_name precision
      ACCEPT_WITH_FLAG — correct department but wrong municipality, or admin precision
      REJECT — wrong department, centroid, or outside country
    """
    valid = results_df[results_df["geocoded_lat"].notna()].copy()
    if valid.empty:
        for col in ["geocoded_in_adm1", "geocoded_in_adm2", "adm1_check", "adm2_check", "acceptance"]:
            results_df[col] = ""
        return results_df

    # Merge declared address
    if addr_df is not None:
        addr_cols = [
            col
            for col in ["id_centro", "raw_adm1", "raw_adm2", "qc_raw_adm1", "qc_raw_adm2", "raw_adm1_code"]
            if col in addr_df.columns
        ]
        valid = valid.merge(addr_df[addr_cols], on="id_centro", how="left")
    else:
        valid["raw_adm1"] = ""
        valid["raw_adm2"] = ""
    if "qc_raw_adm1" not in valid.columns:
        valid["qc_raw_adm1"] = valid["raw_adm1"]
    if "qc_raw_adm2" not in valid.columns:
        valid["qc_raw_adm2"] = valid["raw_adm2"]
    if "raw_adm1_code" not in valid.columns:
        valid["raw_adm1_code"] = ""

    # Spatial join
    adm1, adm2 = _load_admin_boundaries(iso)
    final_level = COUNTRY_SCOPE.get(iso, {}).get("final_match_level", "adm2")

    # --- Validate GEOCODED coordinates ---
    geo_gdf = gpd.GeoDataFrame(
        valid[["id_centro"]],
        geometry=[Point(lon, lat) for lon, lat in zip(valid["geocoded_lon"], valid["geocoded_lat"])],
        crs="EPSG:4326",
    )

    valid["geocoded_in_adm1"] = ""
    valid["geocoded_in_adm2"] = ""
    valid["geocoded_in_adm1_pcode"] = ""

    if adm1 is not None and not adm1.empty:
        j1 = gpd.sjoin(geo_gdf, adm1[["ADM1_EN", "ADM1_PCODE", "geometry"]], how="left", predicate="within")
        j1 = j1[~j1.index.duplicated(keep="first")]  # guard against boundary overlaps
        valid["geocoded_in_adm1"] = j1["ADM1_EN"].values
        valid["geocoded_in_adm1_pcode"] = j1["ADM1_PCODE"].fillna("").values

    if adm2 is not None and not adm2.empty:
        j2 = gpd.sjoin(geo_gdf, adm2[["ADM2_EN", "geometry"]], how="left", predicate="within")
        j2 = j2[~j2.index.duplicated(keep="first")]
        valid["geocoded_in_adm2"] = j2["ADM2_EN"].values

    def _adm1_check(raw_code, raw_name, poly_code, poly_name):
        if raw_code and poly_code:
            return "MATCH" if raw_code == poly_code else "MISMATCH"
        return _admin_match(raw_name, poly_name, iso)

    valid["geo_adm1_check"] = valid.apply(
        lambda r: _adm1_check(r.get("raw_adm1_code", ""), r.get("qc_raw_adm1"), r["geocoded_in_adm1_pcode"], r["geocoded_in_adm1"]),
        axis=1,
    )
    valid["geo_adm2_check"] = valid.apply(lambda r: _admin_match(r.get("qc_raw_adm2"), r["geocoded_in_adm2"], iso), axis=1)

    # --- Validate ORIGINAL coordinates (for compare targets) ---
    valid["original_in_adm1"] = ""
    valid["original_in_adm2"] = ""
    valid["original_in_adm1_pcode"] = ""
    valid["orig_adm1_check"] = "NO_DATA"
    valid["orig_adm2_check"] = "NO_DATA"

    has_orig = valid["original_lat"].notna() & (valid["original_lat"] != 0)
    if has_orig.any():
        orig_gdf = gpd.GeoDataFrame(
            valid.loc[has_orig, ["id_centro"]],
            geometry=[Point(lon, lat) for lon, lat in
                      zip(valid.loc[has_orig, "original_lon"], valid.loc[has_orig, "original_lat"])],
            crs="EPSG:4326",
        )
        if adm1 is not None and not adm1.empty:
            oj1 = gpd.sjoin(orig_gdf, adm1[["ADM1_EN", "ADM1_PCODE", "geometry"]], how="left", predicate="within")
            oj1 = oj1[~oj1.index.duplicated(keep="first")]
            valid.loc[has_orig, "original_in_adm1"] = oj1["ADM1_EN"].values
            valid.loc[has_orig, "original_in_adm1_pcode"] = oj1["ADM1_PCODE"].fillna("").values

        if adm2 is not None and not adm2.empty:
            oj2 = gpd.sjoin(orig_gdf, adm2[["ADM2_EN", "geometry"]], how="left", predicate="within")
            oj2 = oj2[~oj2.index.duplicated(keep="first")]
            valid.loc[has_orig, "original_in_adm2"] = oj2["ADM2_EN"].values

        valid.loc[has_orig, "orig_adm1_check"] = valid.loc[has_orig].apply(
            lambda r: _adm1_check(r.get("raw_adm1_code", ""), r.get("qc_raw_adm1"), r["original_in_adm1_pcode"], r["original_in_adm1"]), axis=1).values
        valid.loc[has_orig, "orig_adm2_check"] = valid.loc[has_orig].apply(
            lambda r: _admin_match(r.get("qc_raw_adm2"), r["original_in_adm2"], iso), axis=1).values

    # --- Decision: accept or reject geocoded coordinates ---
    #
    # Rules (validated by ground truth analysis of 550 schools, 11 countries):
    #   - Precision already reclassified by ArcGIS score in geocode_school():
    #       score >= 95 → "street"    (87% within 5km of real location)
    #       score 90-95 → "centroid"  (51% within 5km — municipal centroid)
    #       score < 90  → "uncertain" (only 40% within 5km — unreliable)
    #
    #   - FILL targets (no GPS): accept street + centroid, reject uncertain
    #   - COMPARE targets (has GPS): NEVER replace GPS. Only diagnose discrepancy.
    #     Ground truth showed geocoder median error 4-8km even for known-good schools.
    #     No IMPROVEMENT category — geocoder cannot reliably improve ministry GPS.
    #
    # See: results/geocoder_ground_truth_all_countries.csv (evidence)
    #      results/geocoder_ground_truth_score_analysis.csv (score thresholds)
    def _decide(r):
        prec = str(r.get("geocode_precision", ""))
        target = r.get("target_type", "")

        # Geocoded admin checks
        ga1, ga2 = r["geo_adm1_check"], r["geo_adm2_check"]
        geo_in_muni = ga2 in ("MATCH", "PARTIAL")
        geo_in_dept = ga1 in ("MATCH", "PARTIAL")
        geo_in_best = geo_in_muni if final_level == "adm2" else geo_in_dept

        # Always reject: geocoded in wrong department
        if final_level in ("adm1", "adm2") and ga1 == "MISMATCH":
            return "REJECT"

        # Uncertain precision (score < 90): reject for all targets
        if prec == "uncertain":
            if target == "fill":
                return "REJECT"
            else:
                return "FLAG"  # keep GPS, flag discrepancy in QC

        # --- FILL targets (no original coord) ---
        if target == "fill":
            if prec == "street" and geo_in_best:
                return "ACCEPT"              # high confidence
            if final_level == "adm2" and prec == "street" and geo_in_dept:
                return "ACCEPT_WITH_FLAG"    # right dept, wrong muni
            if prec == "centroid" and (geo_in_best or geo_in_dept):
                return "ACCEPT_CENTROID"     # centroid in the best validated admin
            return "REJECT"

        # --- COMPARE targets (has GPS) ---
        # Never replace GPS. Only diagnose address-vs-coordinate discrepancy.
        orig_in_dept = r["orig_adm1_check"] in ("MATCH", "PARTIAL")
        orig_in_muni = r["orig_adm2_check"] in ("MATCH", "PARTIAL")

        if (final_level == "adm2" and orig_in_muni) or (final_level != "adm2" and orig_in_dept):
            return "KEEP_ORIGINAL"  # GPS confirmed in correct municipality
        else:
            return "FLAG"           # GPS doesn't match declared address — flag for QC

    valid["acceptance"] = valid.apply(_decide, axis=1)

    # Merge back
    merge_cols = [
        "geocoded_in_adm1", "geocoded_in_adm2", "geo_adm1_check", "geo_adm2_check",
        "original_in_adm1", "original_in_adm2", "orig_adm1_check", "orig_adm2_check",
        "acceptance",
    ]
    results_df = results_df.merge(valid[["id_centro"] + merge_cols], on="id_centro", how="left")
    for col in merge_cols:
        results_df[col] = results_df[col].fillna("")

    return results_df


_JUNK_VALUES = {"", "nan", "none", "ninguno", "s/n", "sin nombre", "sin direccion",
                "ninguno ninguno 0, ninguno", "no disponible", "no aplica", "s/d"}

# Prefixes that indicate the name is a TYPE, not a specific institution.
# "Colegio La Salle" is specific; "Escuela Rural Mixta La Pradera" is not
# because geocoders index named institutions, not generic rural schools.
_GENERIC_PREFIXES = (
    "escuela nueva", "escuela rural mixta", "escuela rural", "escuela urbana",
    "escuela", "centro educativo rural", "centro educativo",
    "sede ", "sede principal", "e m e f", "e m e i f", "e m e i",
    "unidad educativa", "nucleo educativo",
)

# Prefixes that suggest a named, findable institution
_NAMED_PREFIXES = (
    "colegio ", "liceo ", "instituto ", "gimnasio ", "seminario ",
    "fundacion ", "corporacion ", "academia ", "politecnico ",
)


def _is_specific_name(name):
    """Check if a school name is specific enough to geocode by name.

    Only returns True for names likely indexed in geocoder databases:
    named institutions (Colegio X, Liceo Y, Instituto Z), not generic
    types (Escuela Rural, Sede, Centro Educativo).
    """
    if not name or len(name) < 8:
        return False
    name_lower = name.lower().strip()
    # Explicitly named institutions — always usable
    for prefix in _NAMED_PREFIXES:
        if name_lower.startswith(prefix) and len(name_lower) > len(prefix) + 3:
            return True
    # Generic prefixes — never usable
    for prefix in _GENERIC_PREFIXES:
        if name_lower.startswith(prefix):
            return False
    # "Institucion/Institución Educativa [specific name]" — usable if name part is long
    if ("institucion educativa" in name_lower or "institución educativa" in name_lower) and len(name_lower) > 30:
        return True
    # Everything else: skip (single words, indigenous names — too ambiguous for geocoder)
    return False


def build_queries(row, country_name):
    """Build geocoding queries from most to least specific."""
    street = str(row.get("raw_street", "")).strip()
    locality = str(row.get("raw_locality", "")).strip()
    adm2 = str(row.get("raw_adm2", "")).strip()
    adm1 = str(row.get("raw_adm1", "")).strip()
    nombre = str(row.get("nombre_centro", "")).strip()

    # Clean empty/nan/placeholder values
    street = "" if street.lower() in _JUNK_VALUES else street
    locality = "" if locality.lower() in _JUNK_VALUES else locality
    adm2 = "" if adm2.lower() in _JUNK_VALUES else adm2
    adm1 = "" if adm1.lower() in _JUNK_VALUES else adm1
    nombre = "" if nombre.lower() in _JUNK_VALUES else nombre

    queries = []
    # 1. Street address + municipality + department (most precise)
    if street and adm2 and adm1:
        queries.append((f"{street}, {adm2}, {adm1}, {country_name}", "street"))
    # 2. School name + municipality + department (if name is specific)
    if nombre and adm2 and adm1 and _is_specific_name(nombre):
        queries.append((f"{nombre}, {adm2}, {adm1}, {country_name}", "school_name"))
    # 3. Locality + municipality + department
    if locality and adm2 and adm1:
        queries.append((f"{locality}, {adm2}, {adm1}, {country_name}", "locality"))
    # 4. Municipality + department (admin centroid — last resort)
    if adm2 and adm1:
        queries.append((f"{adm2}, {adm1}, {country_name}", "admin"))
    # Skip adm1-only (too coarse) and adm2-only (ambiguous: same municipality name in multiple departments)
    return queries


def _score_to_precision(score, query_precision):
    """Reclassify geocode precision based on ArcGIS score.

    Ground truth analysis (550 schools, 11 countries) showed:
      score >= 95: median 0.2km, 87% < 5km → genuine street match
      score 90-95: median 4.4km, 51% < 5km → likely locality/centroid
      score < 90:  median 8.1km, 40% < 5km → municipal centroid at best

    See results/geocoder_ground_truth_score_analysis.csv for evidence.
    """
    if score is not None and score >= 95:
        return "street"
    if score is not None and score >= 90:
        return "centroid"
    # score < 90: always uncertain, regardless of query level
    if score is not None:
        return "uncertain"
    # No score (non-ArcGIS geocoder): classify by query type
    if query_precision in ("admin",):
        return "centroid"
    return "uncertain"


def geocode_school(queries, geocoders, cache, bbox=None):
    """Try geocoding with query cascade × geocoder cascade.
    Returns (lat, lon, source, precision, query, score) or Nones.
    bbox: (lat_min, lat_max, lon_min, lon_max) — reject results outside this box.

    Precision is reclassified by ArcGIS score:
      score >= 95 → "street"   (accept for fill + compare diagnostic)
      score 90-95 → "centroid" (accept for fill only)
      score < 90  → "uncertain" (reject)
    """
    for query, precision in queries:
        query_clean = query.strip(", ")

        # Check cache
        if query_clean in cache:
            cached = cache[query_clean]
            if cached is not None:
                cached_score = cached.get("score")
                real_precision = _score_to_precision(cached_score, precision)
                return cached["lat"], cached["lon"], cached.get("source", "cache"), real_precision, query_clean, cached_score
            continue  # cached failure

        # Try each geocoder for this query before moving to next query level
        for source_name, geocoder_fn in geocoders:
            try:
                location = geocoder_fn(query_clean)
                if location:
                    lat, lon = location.latitude, location.longitude
                    # Bbox validation: reject results outside the country
                    if bbox:
                        lat_min, lat_max, lon_min, lon_max = bbox
                        margin = 1  # degree margin for islands/borders
                        if lat < lat_min - margin or lat > lat_max + margin or \
                           lon < lon_min - margin or lon > lon_max + margin:
                            continue  # result outside country, try next geocoder
                    # Capture ArcGIS score (other geocoders return None)
                    score = None
                    if hasattr(location, "raw") and isinstance(location.raw, dict):
                        score = location.raw.get("score")
                    real_precision = _score_to_precision(score, precision)
                    cache[query_clean] = {
                        "lat": lat, "lon": lon,
                        "display": location.address,
                        "source": source_name,
                        "score": score,
                    }
                    return lat, lon, source_name, real_precision, query_clean, score
            except Exception:
                continue  # try next geocoder for same query
        # All geocoders failed for this query — cache failure, try next query level
        cache[query_clean] = None

    return None, None, None, None, None, None


# ---------------------------------------------------------------------------
# Centroid fallback (PAN/ECU now; DOM/SLV once municipality lookup exists)
# ---------------------------------------------------------------------------

def _centroid_join_key(values, iso, normalize_key=False, apply_aliases=False):
    """Prepare one key component for centroid lookup."""
    values = values.fillna("").astype(str).str.strip()
    if not normalize_key:
        return values

    values = values.apply(_normalize_admin)
    if apply_aliases and iso in ADM1_ALIASES:
        aliases = ADM1_ALIASES[iso]
        for raw, canonical in aliases.items():
            values = values.str.replace(raw, canonical, regex=False)
    return values


def _build_centroid_join_key(df, cols, iso, normalize_key=False, apply_aliases=False):
    """Prepare a composite join key from one or more columns."""
    prepared = [
        _centroid_join_key(df[col], iso, normalize_key=normalize_key, apply_aliases=apply_aliases)
        for col in cols
    ]
    if len(prepared) == 1:
        return prepared[0]

    key = prepared[0]
    for part in prepared[1:]:
        key = key + "||" + part
    return key


def _invalidate_final_qc(cima, mask):
    """Clear derived final-QC labels for rows touched by Step 05.

    Step 05 is evidence-only. After it mutates coordinates or geocoder evidence,
    Step 02 finalize must rerun to regenerate the canonical labels.
    """
    for col in ["coordinate_quality", "coordinate_quality_reason"]:
        if col in cima.columns:
            cima.loc[mask, col] = ""


VALIDATION_AUDIT_COLUMNS = [
    "geocoded_in_adm1", "geocoded_in_adm2", "geo_adm1_check", "geo_adm2_check",
    "original_in_adm1", "original_in_adm2", "orig_adm1_check", "orig_adm2_check",
]


def _write_validation_audit(cima, idx, result_row):
    """Persist validation audit fields from validate_geocoded() into CIMA."""
    for col in VALIDATION_AUDIT_COLUMNS:
        if col not in cima.columns:
            cima[col] = ""
        cima.loc[idx, col] = result_row.get(col, "")


def refresh_existing_compare_evidence(iso, cfg, extract_addresses_fn, dry_run=False):
    """Re-evaluate existing compare evidence under the current QC semantics.

    Does NOT call geocoders and does NOT change the original GPS.
    It only rebuilds acceptance + admin-audit fields for rows that already have
    a geocoded alternative (`latitud_geocoded` / `longitud_geocoded`).
    """
    cima_path = BASE / iso / "processed" / f"{iso}_total_cima.csv"
    if not cima_path.exists():
        return {"error": "CIMA file not found"}

    cima = pd.read_csv(cima_path, dtype={"id_centro": str})
    compare_mask = cima["latitud_geocoded"].notna() if "latitud_geocoded" in cima.columns else pd.Series(False, index=cima.index)
    # Restrict to TRUE compare rows: original GPS preserved with a geocoded
    # alternative stored for QC reference. Fill rows (coordinate_source in
    # {"geocoded", "centroid_cascade*"}) write latitud_geocoded as a symmetric
    # backup but their acceptance is already correct under fill semantics —
    # treating them as compare here would relabel ACCEPT/ACCEPT_CENTROID as
    # KEEP_ORIGINAL/FLAG and erase the recovery flag.
    if "coordinate_source" in cima.columns:
        compare_mask &= cima["coordinate_source"].fillna("").isin(["", "original"])
    compare = cima[compare_mask].copy()
    if compare.empty:
        print(f"  {iso}: no existing compare evidence to refresh")
        return {"iso": iso, "compare_rows": 0}

    addr_df = extract_addresses_fn(iso, cfg) if cfg else None
    before = compare["acceptance"].fillna("").replace("", "<blank>").value_counts().to_dict()

    results_df = pd.DataFrame({
        "id_centro": compare["id_centro"].astype(str),
        "geocoded_lat": compare["latitud_geocoded"],
        "geocoded_lon": compare["longitud_geocoded"],
        "geocode_source": compare.get("geocode_source", ""),
        "geocode_precision": compare.get("geocode_precision", ""),
        "arcgis_score": compare.get("arcgis_score", np.nan),
        "geocode_distance_km": compare.get("geocode_distance_km", np.nan),
        "target_type": "compare",
        "original_lat": compare["latitud"],
        "original_lon": compare["longitud"],
    })

    print(f"  {iso}: refreshing {len(results_df):,} existing compare rows")
    results_df = validate_geocoded(results_df, addr_df, iso)
    after = results_df["acceptance"].fillna("").replace("", "<blank>").value_counts().to_dict()
    print(f"    Acceptance before: {before}")
    print(f"    Acceptance after:  {after}")

    if dry_run:
        return {
            "iso": iso,
            "compare_rows": len(results_df),
            "before": before,
            "after": after,
        }

    comp_map = results_df.set_index("id_centro")
    n_keep = 0
    n_flag = 0
    for sid in comp_map.index:
        mask = cima["id_centro"] == sid
        if not mask.any():
            continue
        idx = cima.index[mask][0]
        row = comp_map.loc[sid]
        cima.loc[idx, "acceptance"] = row["acceptance"]
        cima.loc[idx, "geocode_source"] = row.get("geocode_source", "")
        cima.loc[idx, "geocode_precision"] = row.get("geocode_precision", "")
        cima.loc[idx, "geocode_distance_km"] = row.get("geocode_distance_km", np.nan)
        cima.loc[idx, "arcgis_score"] = row.get("arcgis_score", np.nan)
        cima.loc[idx, "coordinate_source"] = "original"
        _write_validation_audit(cima, idx, row)
        _invalidate_final_qc(cima, mask)
        if row["acceptance"] == "KEEP_ORIGINAL":
            n_keep += 1
        else:
            n_flag += 1

    cima.to_csv(cima_path, index=False, encoding="utf-8")
    print(f"    Compare refreshed: {n_keep:,} confirmed, {n_flag:,} flagged")
    return {
        "iso": iso,
        "compare_rows": len(results_df),
        "before": before,
        "after": after,
    }


def fill_centroid_missing(iso, cima_path, cfg, extract_addresses_fn, geocoders, cache, dry_run=False):
    """Fill missing coordinates using a country-specific centroid lookup."""
    if not cima_path.exists():
        return None, {"error": "CIMA file not found"}

    policy = CENTROID_LOOKUP_POLICIES.get(iso)
    if policy is None:
        raise ValueError(f"No centroid lookup policy defined for {iso}")

    cima = pd.read_csv(cima_path, dtype={"id_centro": str})
    addr_df = extract_addresses_fn(iso, cfg) if cfg else None

    for col in ["coordinate_source", "geocode_source", "geocode_precision", "acceptance", "qc_centroid_bias"]:
        if col not in cima.columns:
            cima[col] = ""
    for col in ["latitud_geocoded", "longitud_geocoded", "geocode_distance_km", "arcgis_score"]:
        if col not in cima.columns:
            cima[col] = np.nan

    has_coords = cima["latitud"].notna() & (cima["latitud"] != 0) & (cima["longitud"] != 0)
    cima.loc[has_coords & (cima["coordinate_source"] == ""), "coordinate_source"] = "original"

    centroid_path = BASE / iso / "raw" / policy["lookup_filename"]
    if not centroid_path.exists():
        raise FileNotFoundError(
            f"Centroid file not found: {centroid_path}\n"
            f"Expected at: data/schools/AR/{iso}/raw/{policy['lookup_filename']}"
        )

    centroids = pd.read_csv(centroid_path, dtype=str)
    for col in ["centroid_lat", "centroid_lon", "area_km2"]:
        if col in centroids.columns:
            centroids[col] = pd.to_numeric(centroids[col], errors="coerce")
    if "high_bias_centroid" in centroids.columns:
        centroids["high_bias_centroid"] = (
            centroids["high_bias_centroid"].astype(str).str.lower().isin(["true", "1", "yes"])
        )
    else:
        centroids["high_bias_centroid"] = centroids["area_km2"] > 314

    join_label = policy["join_label"]
    lookup_key_cols = policy.get("lookup_key_cols") or [policy["lookup_key_col"]]
    school_key_cols = policy.get("school_key_cols") or [policy["school_key_col"]]
    centroids["centroid_key"] = _build_centroid_join_key(
        centroids,
        lookup_key_cols,
        iso,
        normalize_key=policy["normalize_key"],
        apply_aliases=policy["apply_aliases"],
    )

    missing = cima[cima["latitud"].isna()].copy()
    if missing.empty:
        return cima, {"pasada1_matched": 0, "pasada2_matched": 0, "pasada3_matched": 0, "unmatched": 0}

    print(f"  {iso}: {len(missing):,} schools to fill with centroid fallback")
    print(f"    Pasada 1: ArcGIS name-geocoding (score >= 95)... [skipped for {iso}]")
    print(f"    Pasada 2-3: centroid {join_label} lookup (remaining {len(missing):,})...")

    missing_with_addr = missing.copy()
    if addr_df is not None:
        keep_cols = ["id_centro", "raw_adm1", "raw_adm2", "raw_locality", "raw_locality_code"]
        keep_cols = [col for col in keep_cols if col in addr_df.columns]
        missing_with_addr = missing.merge(addr_df[keep_cols].copy(), on="id_centro", how="left")
    for col in ["raw_adm1", "raw_adm2", "raw_locality", "raw_locality_code"]:
        if col not in missing_with_addr.columns:
            missing_with_addr[col] = ""

    missing_with_addr["centroid_key"] = _build_centroid_join_key(
        missing_with_addr,
        school_key_cols,
        iso,
        normalize_key=policy["normalize_key"],
        apply_aliases=policy["apply_aliases"],
    )

    cent_cols = [
        "centroid_key", "centroid_lat", "centroid_lon", "area_km2", "high_bias_centroid",
    ]
    optional_centroid_cols = [
        "lookup_parroquia_code", "lookup_parroquia_name",
        "canonical_parroquia_code", "canonical_parroquia_name",
        "canonical_canton_name", "canonical_province_name",
    ]
    cent_cols.extend([col for col in optional_centroid_cols if col in centroids.columns])

    filled = missing_with_addr.merge(centroids[cent_cols], on="centroid_key", how="left")
    matched = filled[filled["centroid_lat"].notna()].copy()
    unmatched = filled[filled["centroid_lat"].isna()].copy()

    small_mask = matched["area_km2"].fillna(0) <= 314
    if "high_bias_centroid" in matched.columns:
        small_mask = small_mask & (~matched["high_bias_centroid"].fillna(False))
    pasada2 = matched[small_mask].copy()
    pasada3 = matched[~small_mask].copy()

    for col in ("latitud_geocoded", "longitud_geocoded"):
        if col not in cima.columns:
            cima[col] = np.nan

    for _, row in pasada2.iterrows():
        sid = row["id_centro"]
        mask = cima["id_centro"] == sid
        cima.loc[mask, "latitud"] = row["centroid_lat"]
        cima.loc[mask, "longitud"] = row["centroid_lon"]
        # Respaldo simétrico: si Step 01 se rerunea y borra latitud/longitud
        # desde raw, finalize promueve latitud_geocoded → latitud.
        cima.loc[mask, "latitud_geocoded"] = row["centroid_lat"]
        cima.loc[mask, "longitud_geocoded"] = row["centroid_lon"]
        cima.loc[mask, "geocode_source"] = "centroid_cascade"
        cima.loc[mask, "geocode_precision"] = "centroid"
        cima.loc[mask, "acceptance"] = "ACCEPT_CENTROID"
        cima.loc[mask, "coordinate_source"] = policy["source_small"]
        cima.loc[mask, "qc_centroid_bias"] = "normal"
        _invalidate_final_qc(cima, mask)

    for _, row in pasada3.iterrows():
        sid = row["id_centro"]
        mask = cima["id_centro"] == sid
        cima.loc[mask, "latitud"] = row["centroid_lat"]
        cima.loc[mask, "longitud"] = row["centroid_lon"]
        cima.loc[mask, "latitud_geocoded"] = row["centroid_lat"]
        cima.loc[mask, "longitud_geocoded"] = row["centroid_lon"]
        cima.loc[mask, "geocode_source"] = "centroid_cascade"
        cima.loc[mask, "geocode_precision"] = "centroid"
        cima.loc[mask, "acceptance"] = "ACCEPT_CENTROID"
        cima.loc[mask, "coordinate_source"] = policy["source_large"]
        cima.loc[mask, "qc_centroid_bias"] = "high"
        _invalidate_final_qc(cima, mask)

    unmatched_records = []
    for _, row in unmatched.iterrows():
        unmatched_records.append({
            "id_centro": row["id_centro"],
            "nombre_centro": row.get("nombre_centro", ""),
            "raw_adm2": row.get("raw_adm2", ""),
            "raw_locality": row.get("raw_locality", ""),
            "raw_locality_code": row.get("raw_locality_code", ""),
            "centroid_key": row.get("centroid_key", ""),
            "motivo": "centroid_not_found",
        })

    missing_final = cima[cima["latitud"].isna()]
    qc_dir = RESULTS / "QC"
    qc_dir.mkdir(exist_ok=True)
    if unmatched_records:
        unmatched_path = qc_dir / f"{iso}_centroid_unmatched.csv"
        pd.DataFrame(unmatched_records).to_csv(unmatched_path, index=False, encoding="utf-8")

    summary = {
        "fill": len(missing),
        "compare": 0,
        "centroids_new": 0,
        "total": len(missing),
        "pasada1_matched": 0,
        "pasada2_matched": len(pasada2),
        "pasada3_matched": len(pasada3),
        "unmatched": len(unmatched_records),
        "total_filled": len(pasada2) + len(pasada3),
    }

    print(f"    Pasada 1 (ArcGIS name, score>=95): {summary['pasada1_matched']:,} matched")
    print(f"    Pasada 2 (centroid, area<=314km²): {summary['pasada2_matched']:,} matched")
    print(f"    Pasada 3 (centroid, area>314km²):  {summary['pasada3_matched']:,} matched")
    print(f"    Total filled: {summary['total_filled']:,}")
    print(f"    Still missing: {len(missing_final):,}")
    if unmatched_records:
        print(f"    Unmatched logged to: results/QC/{iso}_centroid_unmatched.csv")

    return cima, summary


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_country(iso, cfg, extract_addresses_fn, geocoders, cache,
                    dry_run=False, skip_dup_coords=False, retry_centroids=False):
    """Geocode all target schools for one country."""

    # Load addresses first (needed for centroid detection fuzzy filter)
    addr_df = extract_addresses_fn(iso, cfg) if cfg else None

    cima, missing, zeros, mismatches, dup_addr, coord_centroids = identify_targets(iso, addr_df)
    if cima.empty:
        return None

    # Combine targets
    fill_targets = missing | zeros  # these get latitud/longitud filled directly
    compare_targets = mismatches  # these get _geocoded columns
    if not skip_dup_coords:
        compare_targets = compare_targets | dup_addr
    # Always include detected centroids (original + geocoded) for re-geocoding
    compare_targets = compare_targets | coord_centroids

    all_targets = fill_targets | compare_targets
    if not all_targets:
        print(f"  {iso}: no schools to geocode")
        return None

    n_centroids = len(coord_centroids - missing - zeros - mismatches - dup_addr)
    print(f"  {iso}: {len(all_targets):,} targets "
          f"(fill={len(fill_targets):,}, compare={len(compare_targets):,}, "
          f"centroids_new={n_centroids:,})")

    # Retry centroids: clear cached queries for centroid schools so geocoders retry
    if retry_centroids and coord_centroids:
        cleared = 0
        for key, val in list(cache.items()):
            if val and val.get("source") == "arcgis":
                # Check if this cached result is a centroid point
                lat_r = round(val["lat"], 3)
                lon_r = round(val["lon"], 3)
                geo_key = f"{lat_r},{lon_r}"
                # Count how many cache entries map to same point
                # Simple heuristic: just clear all arcgis entries for this country's queries
                if cfg and cfg["country_name"].lower() in key.lower():
                    del cache[key]
                    cleared += 1
        if cleared:
            print(f"    Cleared {cleared} ArcGIS cache entries for centroid retry")

    if dry_run:
        return {"iso": iso, "fill": len(fill_targets), "compare": len(compare_targets),
                "centroids_new": n_centroids, "total": len(all_targets)}

    if addr_df is None or addr_df.empty:
        print(f"    No address data available")
        return None

    country_name = cfg["country_name"]

    # Merge addresses with target schools
    target_df = cima[cima["id_centro"].isin(all_targets)].copy()
    target_df = target_df.merge(addr_df, on="id_centro", how="left", suffixes=("", "_addr"))

    results = []
    n_success = 0
    n_fail = 0
    t0 = time.time()

    for i, (_, row) in enumerate(target_df.iterrows()):
        queries = build_queries(row, country_name)
        if not queries:
            n_fail += 1
            results.append({
                "iso": iso,
                "id_centro": row["id_centro"],
                "geocoded_lat": np.nan,
                "geocoded_lon": np.nan,
                "geocode_source": None,
                "geocode_precision": "no_address",
                "geocode_query": "",
                "target_type": "fill" if row["id_centro"] in fill_targets else "compare",
            })
            continue

        bbox = COUNTRY_BBOX.get(iso)
        lat, lon, source, precision, query, score = geocode_school(queries, geocoders, cache, bbox=bbox)

        if lat is not None:
            n_success += 1
        else:
            n_fail += 1

        # Distance from original coords (for compare targets)
        dist_km = np.nan
        orig_lat = row.get("latitud")
        orig_lon = row.get("longitud")
        if lat is not None and pd.notna(orig_lat) and pd.notna(orig_lon) and orig_lat != 0:
            dist_km = haversine_km(orig_lat, orig_lon, lat, lon)

        results.append({
            "iso": iso,
            "id_centro": row["id_centro"],
            "nombre_centro": row.get("nombre_centro", ""),
            "geocoded_lat": lat,
            "geocoded_lon": lon,
            "geocode_source": source,
            "geocode_precision": precision or "failed",
            "arcgis_score": score,
            "geocode_query": query or "",
            "geocode_distance_km": dist_km,
            "target_type": "fill" if row["id_centro"] in fill_targets else "compare",
            "original_lat": orig_lat if pd.notna(orig_lat) else np.nan,
            "original_lon": orig_lon if pd.notna(orig_lon) else np.nan,
        })

        # Progress + periodic cache save
        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            remaining = (len(target_df) - i - 1) / rate if rate > 0 else 0
            print(f"    {i+1:,}/{len(target_df):,} ({n_success} ok, {n_fail} fail) "
                  f"[{rate:.1f}/s, ~{remaining/60:.0f}min left]")
        if (i + 1) % 500 == 0:
            # Save cache periodically to avoid losing progress on crash
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
            print(f"    [cache saved: {len(cache):,} entries]")

    elapsed = time.time() - t0
    print(f"    Done: {n_success:,} geocoded, {n_fail:,} failed ({elapsed:.0f}s)")

    results_df = pd.DataFrame(results)

    # --- Centroid detection on geocoded results ---
    all_geocoded = results_df[results_df["geocoded_lat"].notna()].copy()
    if not all_geocoded.empty:
        all_geocoded["geo_key"] = (
            all_geocoded["geocoded_lat"].round(3).astype(str) + "," +
            all_geocoded["geocoded_lon"].round(3).astype(str)
        )
        point_counts = all_geocoded["geo_key"].value_counts()
        centroid_points = set(point_counts[point_counts >= 5].index)
        if centroid_points:
            centroid_ids = set(all_geocoded[all_geocoded["geo_key"].isin(centroid_points)]["id_centro"])
            # Only downgrade "street" → "centroid". Do NOT promote "uncertain" → "centroid".
            # Score-based classification takes priority over cluster detection.
            downgrade_mask = (
                results_df["id_centro"].isin(centroid_ids) &
                results_df["geocoded_lat"].notna() &
                (results_df["geocode_precision"] == "street")
            )
            n_downgraded = downgrade_mask.sum()
            results_df.loc[downgrade_mask, "geocode_precision"] = "centroid"
            print(f"    Centroids detected: {len(centroid_ids):,} schools at {len(centroid_points)} shared points "
                  f"({n_downgraded} downgraded street→centroid)")

    # --- Spatial validation + acceptance criteria ---
    print("    Validating geocoded coords against admin boundaries...")
    results_df = validate_geocoded(results_df, addr_df, iso)

    accept_counts = results_df[results_df["acceptance"] != ""]["acceptance"].value_counts()
    for level, count in accept_counts.items():
        print(f"    {level}: {count:,}")

    # --- Update CIMA file ---
    # Initialize columns
    for col in ["latitud_geocoded", "longitud_geocoded", "geocode_distance_km", "arcgis_score"]:
        if col not in cima.columns:
            cima[col] = np.nan
    for col in ["geocode_source", "geocode_precision", "acceptance", "coordinate_source"]:
        if col not in cima.columns:
            cima[col] = ""
    for col in VALIDATION_AUDIT_COLUMNS:
        if col not in cima.columns:
            cima[col] = ""

    # Default: all schools with existing valid coords → source=original, quality=gps
    has_coords = cima["latitud"].notna() & (cima["latitud"] != 0) & (cima["longitud"] != 0)
    cima.loc[has_coords & (cima["coordinate_source"] == ""), "coordinate_source"] = "original"

    # --- FILL targets: write geocoded coords where accepted ---
    fill_write = ["ACCEPT", "ACCEPT_WITH_FLAG", "ACCEPT_CENTROID"]
    fill_accepted = results_df[
        (results_df["target_type"] == "fill") &
        results_df["geocoded_lat"].notna() &
        results_df["acceptance"].isin(fill_write)
    ]
    if not fill_accepted.empty:
        fill_map = fill_accepted.set_index("id_centro")
        for sid in fill_map.index:
            mask = cima["id_centro"] == sid
            if mask.any():
                idx = cima.index[mask][0]
                prec = fill_map.loc[sid, "geocode_precision"]
                cima.loc[idx, "latitud"] = fill_map.loc[sid, "geocoded_lat"]
                cima.loc[idx, "longitud"] = fill_map.loc[sid, "geocoded_lon"]
                # Respaldo simétrico en columnas no-base para que save_cima
                # las preserve si Step 01 se rerunea (latitud/longitud sí
                # están en SCHEMA y se reescriben desde raw → NaN).
                cima.loc[idx, "latitud_geocoded"] = fill_map.loc[sid, "geocoded_lat"]
                cima.loc[idx, "longitud_geocoded"] = fill_map.loc[sid, "geocoded_lon"]
                cima.loc[idx, "geocode_source"] = fill_map.loc[sid, "geocode_source"]
                cima.loc[idx, "geocode_precision"] = prec
                cima.loc[idx, "arcgis_score"] = fill_map.loc[sid, "arcgis_score"]
                cima.loc[idx, "acceptance"] = fill_map.loc[sid, "acceptance"]
                cima.loc[idx, "coordinate_source"] = "geocoded"
                _write_validation_audit(cima, idx, fill_map.loc[sid])
                _invalidate_final_qc(cima, mask)

    fill_rejected = results_df[
        (results_df["target_type"] == "fill") &
        (~results_df["acceptance"].isin(fill_write))
    ]
    # If a school was previously accepted as a geocoded fill but the current
    # run rejects it (e.g., under tighter rules or a new uncertain score), the
    # CIMA must be cleared back to a missing-coord state. Without this, the
    # CSV ledger and the CIMA disagree (the "ghost geocode" defect): CIMA
    # carries coordinate_source='geocoded' for rows that have no current
    # accept evidence in geocode_results.csv.
    n_fill_ok = len(fill_accepted)
    n_fill_rej = len(fill_rejected)
    n_cleared = 0
    if not fill_rejected.empty and "coordinate_source" in cima.columns:
        rejected_ids = set(fill_rejected["id_centro"].astype(str))
        rej_mask = (
            cima["id_centro"].astype(str).isin(rejected_ids)
            & (cima["coordinate_source"] == "geocoded")
        )
        if rej_mask.any():
            n_cleared = int(rej_mask.sum())
            cima.loc[rej_mask, ["latitud", "longitud",
                                "latitud_geocoded", "longitud_geocoded",
                                "geocode_distance_km", "arcgis_score"]] = np.nan
            for col in ("coordinate_source", "geocode_source",
                        "geocode_precision", "acceptance"):
                if col in cima.columns:
                    cima.loc[rej_mask, col] = ""
            for col in VALIDATION_AUDIT_COLUMNS:
                if col in cima.columns:
                    cima.loc[rej_mask, col] = ""
            _invalidate_final_qc(cima, rej_mask)
    if n_fill_ok + n_fill_rej > 0:
        suffix = f", cleared {n_cleared:,} prior accepts" if n_cleared else ""
        print(f"    Fill: {n_fill_ok:,} accepted, {n_fill_rej:,} rejected (left as NaN{suffix})")

    # --- COMPARE targets: NEVER replace GPS. Write QC audit columns only. ---
    compare_all = results_df[
        (results_df["target_type"] == "compare") &
        results_df["geocoded_lat"].notna()
    ]
    if not compare_all.empty:
        comp_map = compare_all.set_index("id_centro")
        n_keep = 0
        n_flag = 0
        for sid in comp_map.index:
            mask = cima["id_centro"] == sid
            if not mask.any():
                continue
            idx = cima.index[mask][0]
            acc = comp_map.loc[sid, "acceptance"]

            # Write QC audit columns (geocoded alternative for reference)
            cima.loc[idx, "latitud_geocoded"] = comp_map.loc[sid, "geocoded_lat"]
            cima.loc[idx, "longitud_geocoded"] = comp_map.loc[sid, "geocoded_lon"]
            cima.loc[idx, "geocode_source"] = comp_map.loc[sid, "geocode_source"]
            cima.loc[idx, "geocode_precision"] = comp_map.loc[sid, "geocode_precision"]
            cima.loc[idx, "geocode_distance_km"] = comp_map.loc[sid, "geocode_distance_km"]
            cima.loc[idx, "arcgis_score"] = comp_map.loc[sid, "arcgis_score"]
            cima.loc[idx, "acceptance"] = acc

            # GPS always preserved. Step 05 only writes audit evidence; Step 02
            # finalize will decide whether this becomes geocoder_disagrees etc.
            cima.loc[idx, "coordinate_source"] = "original"
            _write_validation_audit(cima, idx, comp_map.loc[sid])
            _invalidate_final_qc(cima, mask)
            if acc == "KEEP_ORIGINAL":
                n_keep += 1
            else:
                n_flag += 1

        print(f"    Compare: {n_keep:,} confirmed, {n_flag:,} flagged (GPS kept, evidence written for finalize)")

    # Save updated CIMA
    cima_path = BASE / iso / "processed" / f"{iso}_total_cima.csv"
    cima.to_csv(cima_path, index=False, encoding="utf-8")

    return results_df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Phase B-1 & B-2: Geocode schools")
    parser.add_argument("--countries", nargs="+", help="ISO codes to process (default: Phase B-1 + B-2)")
    parser.add_argument("--dry-run", action="store_true", help="Preview targets without geocoding")
    parser.add_argument("--skip-dup-coords", action="store_true", help="Skip duplicate-coord schools")
    parser.add_argument("--no-cache", action="store_true", help="Ignore cache, re-geocode everything")
    parser.add_argument("--retry-centroids", action="store_true",
                        help="Clear ArcGIS cache for centroid points and retry with other geocoders")
    parser.add_argument(
        "--refresh-existing-compare",
        action="store_true",
        help=(
            "Recompute acceptance + admin audit for rows that already have "
            "latitud_geocoded/longitud_geocoded, without calling geocoders."
        ),
    )
    args = parser.parse_args()

    isos = args.countries or (PHASE_B1_ISOS + PHASE_CENTROID_ISOS)

    # Load config
    print("Loading address configurations...")
    country_config, extract_addresses_fn = load_country_config()

    # Load cache
    cache = {}
    if not args.refresh_existing_compare:
        if not args.no_cache and CACHE_PATH.exists():
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
        print(f"  Cache: {len(cache):,} entries")

    # Setup geocoders
    geocoders = []
    if not args.dry_run and not args.refresh_existing_compare:
        print("Initializing geocoders...")
        geocoders = setup_geocoders()
        print(f"  Active: {[g[0] for g in geocoders]}")

    # Process countries
    print()
    print("=" * 60)
    if args.refresh_existing_compare:
        print("  Step 05: Refresh existing compare evidence")
    else:
        print("  Phase B-1 & B-2: Geocoding")
    print("=" * 60)

    all_results = []
    for iso in isos:
        cfg = country_config.get(iso)
        if cfg is None or cfg.get("skip"):
            print(f"  {iso}: not in COUNTRY_CONFIG — skipped")
            continue

        if args.refresh_existing_compare:
            summary = refresh_existing_compare_evidence(iso, cfg, extract_addresses_fn, dry_run=args.dry_run)
            if summary and "error" not in summary:
                all_results.append(summary)
            continue

        # Route to centroid cascade (Phase B-2) or text geocoding (Phase B-1)
        if iso in PHASE_CENTROID_ISOS:
            print(f"  {iso}: Phase B-2 (centroid cascade)")
            cima_path = BASE / iso / "processed" / f"{iso}_total_cima.csv"
            cima, summary = fill_centroid_missing(
                iso, cima_path, cfg, extract_addresses_fn, geocoders, cache, dry_run=args.dry_run
            )
            if cima is not None and not args.dry_run:
                # Save updated CIMA
                cima.to_csv(cima_path, index=False, encoding="utf-8")
                print(f"    Updated CIMA saved: {cima_path}")
            if summary and "error" not in summary:
                all_results.append({"iso": iso, **summary})
        else:
            # Phase B-1: text geocoding
            result = process_country(iso, cfg, extract_addresses_fn, geocoders, cache,
                                     dry_run=args.dry_run, skip_dup_coords=args.skip_dup_coords,
                                     retry_centroids=args.retry_centroids)
            if result is not None:
                if isinstance(result, dict):
                    # dry run
                    all_results.append(result)
                else:
                    all_results.append(result)

    # Save cache
    if not args.dry_run and cache:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        print(f"\nCache saved: {len(cache):,} entries → {CACHE_PATH}")

    # Save results — merge by (iso, id_centro), keeping the latest run per
    # school. Previously this replaced ALL rows for processed ISOs, which
    # destroyed historical accept evidence for fills that the current run
    # didn't re-attempt (because they already had latitud and were no longer
    # in fill_targets). That created the "ghost geocode" defect: CIMA rows
    # with coordinate_source='geocoded' but no audit ledger entry. Now we
    # preserve every prior row and only overwrite when the same id_centro
    # appears in this run.
    if not args.dry_run:
        df_results = [r for r in all_results if isinstance(r, pd.DataFrame)]
        if df_results:
            combined = pd.concat(df_results, ignore_index=True)
            results_path = RESULTS / "geocode_results.csv"
            # Force id_centro as string in both sides of the merge — without
            # this, the round-trip CSV→dataframe coerces numeric-looking ids
            # to int and the dedup by (iso, id_centro) silently fails to
            # supersede a prior int-typed row with the new string-typed row.
            combined["id_centro"] = combined["id_centro"].astype(str)
            if results_path.exists():
                existing = pd.read_csv(
                    results_path, dtype={"id_centro": str}, low_memory=False
                )
                # Reorder columns to match the run output before concatenating
                # so older snapshots with extra/missing columns don't drift.
                missing_cols = [c for c in combined.columns if c not in existing.columns]
                for col in missing_cols:
                    existing[col] = pd.NA
                combined = pd.concat(
                    [existing[combined.columns], combined],
                    ignore_index=True,
                )
                n_before = len(combined)
                combined = combined.drop_duplicates(
                    subset=["iso", "id_centro"], keep="last"
                )
                print(
                    f"Merged with existing results: "
                    f"kept {n_before - len(combined):,} prior rows superseded by current run"
                )
            combined.to_csv(results_path, index=False, encoding="utf-8")
            print(f"Results saved: {len(combined):,} rows → results/geocode_results.csv")

            # Comparison file for mismatch/dup schools
            comparison = combined[combined["target_type"] == "compare"].copy()
            if not comparison.empty:
                comparison.to_csv(RESULTS / "geocode_comparison.csv", index=False, encoding="utf-8")
                print(f"Comparison saved: {len(comparison):,} rows → results/geocode_comparison.csv")

    # Dry run summary
    if args.dry_run and all_results:
        print("\n--- DRY RUN SUMMARY ---")
        dict_results = [r for r in all_results if isinstance(r, dict)]
        if dict_results:
            total_fill = sum(r.get("fill", 0) for r in dict_results)
            total_compare = sum(r.get("compare", 0) for r in dict_results)
            total_centroids = sum(r.get("centroids_new", 0) for r in dict_results)
            print(f"{'ISO':<5} {'Fill':>8} {'Compare':>8} {'Centr':>7} {'Total':>8}")
            print("-" * 42)
            for r in dict_results:
                print(f"{r['iso']:<5} {r.get('fill', 0):>8,} {r.get('compare', 0):>8,} {r.get('centroids_new',0):>7,} {r.get('total', 0):>8,}")
            print("-" * 42)
            total_all = total_fill + total_compare
            print(f"TOTAL {total_fill:>8,} {total_compare:>8,} {total_centroids:>7,} {total_all:>8,}")
            est_sec = total_all / 2  # ~2 schools/s effective rate
            print(f"\nEstimated time at ~2 req/s: {est_sec/3600:.1f} hours")


if __name__ == "__main__":
    main()

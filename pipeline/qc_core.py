"""Shared QC helpers for the accessibility-platform pipeline.

Single source of truth for logic that used to live duplicated across
`02_qc_coordinates.py` and `04_geocode_missing.py`. Lets Step 02 finalize
and Step 04 share the exact same spatial / cluster / migration code instead
of going through CSV side-reports.

Responsibilities (Step 2 of the QC unification work):
    - Pure helpers (no I/O): bbox / swap / admin match / cluster detection
    - Legacy v1 → v2 migration for `coordinate_source` (handles blank/null)
    - Canonical resolver for `coordinate_quality` + `coordinate_quality_reason`
    - I/O helpers: load BID boundaries, run spatial joins
    - `compute_geocode_targets` so Step 05 stops depending on Step 02 reports

The orchestrator `finalize_cima()` is intentionally NOT in this module yet —
it lives in Step 02 (Step 3 of the plan) and calls these helpers. This keeps
qc_core thin and unit-testable.

Consumers:
    from qc_core import resolve_coordinate_quality, migrate_legacy_evidence
    from pipeline.qc_core import detect_clusters_exact   # from tests
"""

from __future__ import annotations

import math
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

try:
    # Test environment imports as `pipeline.qc_core` so relative imports work.
    from pipeline.constants import (
        ADM1_ALIASES,
        ADM1_AGGREGATIONS,
        ADM0_BOUNDARY_PCODE_MAP,
        ADM2_ALIASES,
        ADM2_AGGREGATIONS,
        CIMA_ENRICHED_COLUMNS,
        COORDINATE_QUALITY_PRECEDENCE,
        COORDINATE_QUALITY_VALUES_V2,
        COORDINATE_SOURCES,
        COUNTRY_BBOX,
        FINAL_MATCH_LEVELS,
        LEGACY_COORDINATE_SOURCE_MIGRATION,
        QC_CENTROID_BIASES,
        QC_SCOPE_CLASS_VALUES,
    )
    from pipeline.qc_pre_history import QC_PRE_HISTORY
except ImportError:
    # Pipeline scripts run from repo root with `pipeline/` on sys.path.
    from constants import (  # type: ignore[no-redef]
        ADM1_ALIASES,
        ADM1_AGGREGATIONS,
        ADM0_BOUNDARY_PCODE_MAP,
        ADM2_ALIASES,
        ADM2_AGGREGATIONS,
        CIMA_ENRICHED_COLUMNS,
        COORDINATE_QUALITY_PRECEDENCE,
        COORDINATE_QUALITY_VALUES_V2,
        COORDINATE_SOURCES,
        COUNTRY_BBOX,
        FINAL_MATCH_LEVELS,
        LEGACY_COORDINATE_SOURCE_MIGRATION,
        QC_CENTROID_BIASES,
        QC_SCOPE_CLASS_VALUES,
    )
    from qc_pre_history import QC_PRE_HISTORY  # type: ignore[no-redef]


_ADM_ALIASES_BY_LEVEL: dict[int, dict[str, dict[str, str]]] = {
    1: ADM1_ALIASES,
    2: ADM2_ALIASES,
}

_ADM_AGGREGATIONS_BY_LEVEL: dict[int, dict[str, dict[str, set[str]]]] = {
    1: ADM1_AGGREGATIONS,
    2: ADM2_AGGREGATIONS,
}


BOUNDS_DIR = Path("data/bounderys/LAC")

# Distance threshold (km) for `geocoder_disagrees` based on raw geocoder distance.
GEOCODER_DISAGREE_DISTANCE_KM = 10.0

# Cluster threshold for the canonical `cluster_centroid` rule.
CLUSTER_THRESHOLD = 5

# Radius (degrees) for the optional 50m neighbour cluster — evidence only,
# does not drive the final label in v1.
CLUSTER_RADIUS_50M_DEG = 50.0 / 111_320.0  # ~50 m at the equator

# Distance threshold (km) for classifying a point just outside ADM0 as a
# border review case instead of a definite outside-country point.
ADM0_BORDER_REVIEW_DISTANCE_KM = 5.0


# ===========================================================================
# 1. Pure scalar helpers
# ===========================================================================

def normalize_name(s: Any) -> str:
    """Strip accents, lower-case, collapse whitespace. Empty/NaN -> ''."""
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return ""
    s = str(s).strip()
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = " ".join(s.lower().split())
    return s


_DMS_REGEX = None


def _compile_dms_regex():
    """Lazy-compiled regex matching the real-world DMS variants we've seen.

    Supports:
      - degree separators: '°', '\\xb0', '\\xba', '\\u00ba', or whitespace
      - minute separators: '\\'', '\\u2019' (right single quote), or whitespace
      - second separators: '"', '\\u2033' (double prime), or whitespace
      - optional fractional seconds
      - optional N/S/E/W direction suffix (any case)
      - whitespace-separated 'D M S [N|S|E|W]' form (the simpler variant)

    Mirrors the legacy parser in `02_qc_coordinates.py` originally written
    for PRY / SUR raw strings.
    """
    import re
    return re.compile(
        r"(-?\d+(?:[.,]\d+)?)"           # degrees (possibly decimal)
        r"[°\xb0\xbaº\s]+"          # degree separator
        r"(\d+(?:[.,]\d+)?)"             # minutes
        r"['’\s]+"                  # minute separator
        r"([\d.,]+)?"                    # optional seconds
        r"[\"″\s]*"                 # optional second separator
        r"([NSEWnsew])?"                 # optional direction
    )


def dms_to_dd(dms_str: Any):
    """Convert DMS string like '25°17\\'13.5"S' or '5 51 0 N' to decimal degrees.

    Returns numpy.nan on failure (matches legacy `02_qc_coordinates.dms_to_dd`).
    Tries plain float() first, then the Unicode-aware DMS regex, then a final
    whitespace-only fallback for the 'D M S [hemi]' form.
    """
    if dms_str is None:
        return np.nan
    if isinstance(dms_str, float) and math.isnan(dms_str):
        return np.nan

    s = str(dms_str).strip()
    if not s:
        return np.nan

    # 1. Numeric fast path
    try:
        return float(s.replace(",", "."))
    except ValueError:
        pass

    # 2. Unicode-aware DMS regex (matches '25°17\'13.5"S' etc.)
    global _DMS_REGEX
    if _DMS_REGEX is None:
        _DMS_REGEX = _compile_dms_regex()
    m = _DMS_REGEX.match(s)
    if m:
        try:
            deg = float(m.group(1).replace(",", "."))
            mins = float(m.group(2).replace(",", "."))
            secs = float(m.group(3).replace(",", ".")) if m.group(3) else 0.0
            direction = (m.group(4) or "").upper()
            dd = abs(deg) + mins / 60.0 + secs / 3600.0
            if direction in ("S", "W") or deg < 0:
                dd = -dd
            return dd
        except (ValueError, TypeError):
            pass

    # 3. Final fallback: whitespace-separated 'D M S [hemi]' (legacy contract)
    parts = s.replace(",", ".").split()
    if len(parts) >= 3:
        try:
            deg, mn, sec = float(parts[0]), float(parts[1]), float(parts[2])
            dd = abs(deg) + mn / 60.0 + sec / 3600.0
            if (len(parts) >= 4 and parts[3].upper() in ("S", "W")) or deg < 0:
                dd = -dd
            return dd
        except (ValueError, IndexError):
            pass

    return np.nan


def is_blank(v: Any) -> bool:
    """True for None, NaN, empty string, or whitespace-only string."""
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and not v.strip():
        return True
    return False


def clean_text(v: Any) -> str:
    """Normalize scalar text-like evidence to a safe stripped string."""
    if is_blank(v):
        return ""
    return str(v).strip()


def bbox_check(lat: float | None, lon: float | None, bbox: tuple[float, float, float, float]) -> bool:
    """True if (lat, lon) falls inside (lat_min, lat_max, lon_min, lon_max)."""
    if lat is None or lon is None:
        return False
    if isinstance(lat, float) and math.isnan(lat):
        return False
    if isinstance(lon, float) and math.isnan(lon):
        return False
    lat_min, lat_max, lon_min, lon_max = bbox
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def detect_swapped(lat: float | None, lon: float | None,
                   bbox: tuple[float, float, float, float]) -> bool:
    """True if lat/lon look swapped (each falls in the other's range)."""
    if lat is None or lon is None:
        return False
    if isinstance(lat, float) and math.isnan(lat):
        return False
    if isinstance(lon, float) and math.isnan(lon):
        return False
    lat_min, lat_max, lon_min, lon_max = bbox
    in_bounds = lat_min <= lat <= lat_max and lon_min <= lon <= lon_max
    if in_bounds:
        return False
    lat_in_lon = lon_min <= lat <= lon_max
    lon_in_lat = lat_min <= lon <= lat_max
    return lat_in_lon and lon_in_lat


def admin_match(raw_name: Any, polygon_name: Any, iso: str, level: int = 1) -> str:
    """Return 'MATCH' | 'MISMATCH' | 'NO_RAW_ADM' | 'NO_POLYGON'.

    Uses the level-appropriate alias dict per country (ADM1_ALIASES for
    level=1, ADM2_ALIASES for level=2) plus a partial-match fallback
    (one normalized name contains the other). Pure name comparison —
    code-based matches live in the caller.

    Backwards compatible: callers that omit `level` get ADM1 behavior,
    matching pre-SUR-aliases code paths.
    """
    raw_n = normalize_name(raw_name)
    poly_n = normalize_name(polygon_name)
    if not raw_n:
        return "NO_RAW_ADM"
    if not poly_n:
        return "NO_POLYGON"
    aliases = _ADM_ALIASES_BY_LEVEL.get(level, ADM1_ALIASES).get(iso, {})
    raw_n = aliases.get(raw_n, raw_n)
    if raw_n == poly_n:
        return "MATCH"
    if raw_n in poly_n or poly_n in raw_n:
        return "MATCH"
    # Aggregation map: raw_n is a coarser MoE grouping that legitimately covers
    # several BID polygon children (e.g. BHS "ABACOS" -> {Central Abaco, North
    # Abaco, ...}). Match when poly_n is one of the declared children.
    aggregations = _ADM_AGGREGATIONS_BY_LEVEL.get(level, ADM1_AGGREGATIONS).get(iso, {})
    if poly_n in aggregations.get(raw_n, set()):
        return "MATCH"
    return "MISMATCH"


# ===========================================================================
# 2. Cluster detection
# ===========================================================================

def detect_clusters_exact(df: pd.DataFrame, threshold: int = CLUSTER_THRESHOLD,
                          lat_col: str = "latitud", lon_col: str = "longitud",
                          decimals: int = 5) -> pd.Series:
    """Return a Series aligned to `df.index` with the cluster size for each
    school (count of schools sharing the rounded coordinate)."""
    if df.empty:
        return pd.Series([], dtype=int, index=df.index)

    lat = pd.to_numeric(df[lat_col], errors="coerce")
    lon = pd.to_numeric(df[lon_col], errors="coerce")
    has = lat.notna() & lon.notna() & (lat != 0) & (lon != 0)

    sizes = pd.Series(0, index=df.index, dtype=int, name="cluster_size_exact")
    if not has.any():
        return sizes

    sub = pd.DataFrame({
        "lat_r": lat[has].round(decimals),
        "lon_r": lon[has].round(decimals),
    })
    counts = sub.groupby(["lat_r", "lon_r"]).size().rename("n")
    merged = sub.merge(counts.reset_index(), on=["lat_r", "lon_r"], how="left")
    sizes.loc[has] = merged["n"].values
    return sizes


def detect_clusters_radius(df: pd.DataFrame, radius_deg: float = CLUSTER_RADIUS_50M_DEG,
                           lat_col: str = "latitud", lon_col: str = "longitud") -> pd.Series:
    """Return cluster size within `radius_deg` (using a coarse grid bin).

    NOTE: evidence-only column for v1 — does NOT drive the final label.
    Uses a grid binning by `radius_deg` so this stays O(n) without a KD-tree;
    for v1 the magnitude is what matters, not the exact radius semantics.
    """
    if df.empty:
        return pd.Series([], dtype=int, index=df.index, name="cluster_size_50m")

    lat = pd.to_numeric(df[lat_col], errors="coerce")
    lon = pd.to_numeric(df[lon_col], errors="coerce")
    has = lat.notna() & lon.notna() & (lat != 0) & (lon != 0)

    sizes = pd.Series(0, index=df.index, dtype=int, name="cluster_size_50m")
    if not has.any():
        return sizes

    sub = pd.DataFrame({
        "lat_b": (lat[has] / radius_deg).round().astype(int),
        "lon_b": (lon[has] / radius_deg).round().astype(int),
    })
    counts = sub.groupby(["lat_b", "lon_b"]).size().rename("n")
    merged = sub.merge(counts.reset_index(), on=["lat_b", "lon_b"], how="left")
    sizes.loc[has] = merged["n"].values
    return sizes


def has_diff_admin_or_locality_in_cluster(
    df: pd.DataFrame, addr_df: pd.DataFrame | None,
    lat_col: str = "latitud", lon_col: str = "longitud",
    decimals: int = 5,
) -> pd.Series:
    """Boolean Series: True if school sits in an exact cluster whose members
    have at least 2 distinct values in any of raw_adm1, raw_adm2, raw_locality.

    Stronger placeholder signal than `has_diff_address_in_cluster` because it
    uses categorical raw admin/locality values (normalized to known MoE units)
    rather than free-form street strings (which differ by formatting even at
    a single physical campus). Used by the extended cluster_centroid rule for
    small clusters (n in [2,4]) — see resolve_coordinate_quality.
    """
    out = pd.Series(False, index=df.index, dtype=bool, name="cluster_diff_admin_locality")
    if addr_df is None or addr_df.empty or df.empty:
        return out
    if "id_centro" not in addr_df.columns:
        return out
    addr_cols = [c for c in ("raw_adm1", "raw_adm2", "raw_locality") if c in addr_df.columns]
    if not addr_cols:
        return out

    work = df[["id_centro"]].copy()
    work["lat_r"] = pd.to_numeric(df[lat_col], errors="coerce").round(decimals)
    work["lon_r"] = pd.to_numeric(df[lon_col], errors="coerce").round(decimals)
    work["id_centro"] = work["id_centro"].astype(str)
    work = work.merge(
        addr_df[["id_centro"] + addr_cols].assign(
            id_centro=lambda d: d["id_centro"].astype(str)
        ),
        on="id_centro", how="left",
    )
    # Normalize each addr col: lowercase, strip; blanks/"nan" become empty
    for c in addr_cols:
        work[f"_n_{c}"] = (
            work[c].fillna("").astype(str).str.strip().str.lower()
            .replace({"nan": "", "none": ""})
        )

    has_xy = work["lat_r"].notna() & work["lon_r"].notna()
    if not has_xy.any():
        return out

    sub = work.loc[has_xy].copy()

    def _cluster_has_diff(group: pd.DataFrame) -> bool:
        for c in addr_cols:
            vals = group[f"_n_{c}"]
            vals = vals[vals != ""]
            if vals.nunique() > 1:
                return True
        return False

    diff_per_cluster = sub.groupby(["lat_r", "lon_r"]).apply(_cluster_has_diff)
    diff_keys = set(diff_per_cluster[diff_per_cluster].index)
    if not diff_keys:
        return out

    flags = sub.apply(lambda r: (r["lat_r"], r["lon_r"]) in diff_keys, axis=1)
    out.loc[sub.index] = flags.values
    return out


def detect_n2_frontier_rescue(
    df: pd.DataFrame,
    distance_col: str = "qc_distance_to_raw_polygon_km",
    lat_col: str = "latitud",
    lon_col: str = "longitud",
    decimals: int = 5,
    threshold_km: float = 5.0,
) -> pd.Series:
    """Boolean Series: True if school is in an n=2 exact cluster AND at least
    one cluster member sits within `threshold_km` of its declared raw admin
    polygon edge (i.e. `qc_distance_to_raw_polygon_km` < threshold).

    Frontier rescue for the extended cluster_centroid rule. Two schools sharing
    a coord but declaring different admins MUST have at least one school in
    MISMATCH (the coord can only fall in one polygon), so that school has
    `qc_distance_to_raw_polygon_km` set. If the distance is small, the cluster
    sits on the admin boundary — legitimate cross-boundary mismatch, not
    placeholder.

    Requires `qc_distance_to_raw_polygon_km` already computed (call this AFTER
    the mismatch-distance step in finalize_cima_evidence).
    """
    out = pd.Series(False, index=df.index, dtype=bool, name="n2_frontier_rescue")
    if df.empty or distance_col not in df.columns:
        return out
    lat = pd.to_numeric(df[lat_col], errors="coerce")
    lon = pd.to_numeric(df[lon_col], errors="coerce")
    has = lat.notna() & lon.notna() & (lat != 0) & (lon != 0)
    if not has.any():
        return out

    work = pd.DataFrame({
        "lat_r": lat.round(decimals),
        "lon_r": lon.round(decimals),
        "dist": pd.to_numeric(df[distance_col], errors="coerce"),
    }, index=df.index)
    sub = work[has].copy()
    sizes = sub.groupby(["lat_r", "lon_r"]).size()
    n2_keys = set(sizes[sizes == 2].index)
    if not n2_keys:
        return out

    def _rescue(group: pd.DataFrame) -> bool:
        dists = group["dist"].dropna()
        if dists.empty:
            return False
        return bool(dists.min() < threshold_km)

    n2_sub = sub[sub.apply(lambda r: (r["lat_r"], r["lon_r"]) in n2_keys, axis=1)]
    rescue_per_cluster = n2_sub.groupby(["lat_r", "lon_r"]).apply(_rescue)
    rescue_keys = set(rescue_per_cluster[rescue_per_cluster].index)
    if not rescue_keys:
        return out

    flags = n2_sub.apply(lambda r: (r["lat_r"], r["lon_r"]) in rescue_keys, axis=1)
    out.loc[n2_sub.index] = flags.values
    return out


def has_diff_address_in_cluster(df: pd.DataFrame, addr_df: pd.DataFrame | None,
                                lat_col: str = "latitud", lon_col: str = "longitud",
                                decimals: int = 5) -> pd.Series:
    """Boolean Series: True if school sits in an exact cluster whose members
    have at least 2 distinct addresses. Returns all-False if no address data."""
    out = pd.Series(False, index=df.index, dtype=bool, name="cluster_diff_addr_exact")
    if addr_df is None or addr_df.empty or df.empty:
        return out
    if "id_centro" not in addr_df.columns:
        return out

    addr_cols = [c for c in ("raw_street", "raw_adm2", "raw_locality") if c in addr_df.columns]
    if not addr_cols:
        return out

    work = df[["id_centro"]].copy()
    work["lat_r"] = pd.to_numeric(df[lat_col], errors="coerce").round(decimals)
    work["lon_r"] = pd.to_numeric(df[lon_col], errors="coerce").round(decimals)
    work["id_centro"] = work["id_centro"].astype(str)
    work = work.merge(
        addr_df[["id_centro"] + addr_cols].assign(
            id_centro=lambda d: d["id_centro"].astype(str)
        ),
        on="id_centro", how="left",
    )

    work["_addr_key"] = work[addr_cols].fillna("").astype(str).agg("|".join, axis=1).str.lower()

    has_xy = work["lat_r"].notna() & work["lon_r"].notna()
    if not has_xy.any():
        return out

    diversity = work[has_xy].groupby(["lat_r", "lon_r"])["_addr_key"].nunique()
    diff_keys = set(diversity[diversity > 1].index)
    if not diff_keys:
        return out

    flags = work[has_xy].apply(lambda r: (r["lat_r"], r["lon_r"]) in diff_keys, axis=1)
    out.loc[has_xy[has_xy].index] = flags.values
    return out


# ===========================================================================
# 3. Legacy v1 → v2 migration
# ===========================================================================

def migrate_legacy_evidence(row: dict | pd.Series) -> dict:
    """Normalize a CIMA row's legacy `coordinate_source` to v2 vocabulary.

    Returns a dict with canonical keys:
        - coordinate_source ∈ COORDINATE_SOURCES
        - qc_centroid_bias ∈ QC_CENTROID_BIASES

    Behaviour summary:
        - Known legacy values (adm3_centroid, adm3_centroid_large, original,
          geocoded) → mapped via LEGACY_COORDINATE_SOURCE_MIGRATION.
        - Blank/null/NaN with no usable coordinate → keep blank source so
          the resolver assigns `missing`.
        - Blank/null/NaN with usable coordinate → default to `original`
          (no Step 05 enrichment yet — coordinate must come from raw).
        - Unknown non-blank value → warn-tag and fall back to `original`
          to avoid losing the coordinate; reason captured in the returned
          dict under `migration_warning`.
    """
    raw_source = row.get("coordinate_source") if hasattr(row, "get") else row["coordinate_source"]

    lat = row.get("latitud") if hasattr(row, "get") else row["latitud"]
    lon = row.get("longitud") if hasattr(row, "get") else row["longitud"]
    has_coord = (
        lat is not None and lon is not None
        and not (isinstance(lat, float) and math.isnan(lat))
        and not (isinstance(lon, float) and math.isnan(lon))
        and not (lat == 0 and lon == 0)
    )

    # Read any pre-existing bias from the row (idempotency: don't clobber it
    # when the row already has a meaningful bias from a prior finalize run).
    prior_bias = row.get("qc_centroid_bias") if hasattr(row, "get") else row.get("qc_centroid_bias", None)
    if is_blank(prior_bias) or prior_bias not in QC_CENTROID_BIASES:
        prior_bias = "unknown"

    # 1. Blank legacy value
    if is_blank(raw_source):
        if not has_coord:
            return {"coordinate_source": "", "qc_centroid_bias": prior_bias}
        return {"coordinate_source": "original", "qc_centroid_bias": prior_bias}

    # 2. Known legacy mapping
    key = clean_text(raw_source)
    if key in LEGACY_COORDINATE_SOURCE_MIGRATION:
        return dict(LEGACY_COORDINATE_SOURCE_MIGRATION[key])

    # 3. Already canonical — preserve any existing bias rather than reset it.
    if key in COORDINATE_SOURCES:
        return {"coordinate_source": key, "qc_centroid_bias": prior_bias}

    # 4. Unknown value — preserve coordinate, surface a warning
    return {
        "coordinate_source": "original" if has_coord else "",
        "qc_centroid_bias": prior_bias,
        "migration_warning": f"unknown coordinate_source={key!r}",
    }


# ===========================================================================
# 4. Canonical resolver
# ===========================================================================

def _strong_geocoder_disagreement(ev: dict) -> bool:
    """Per spec §5.3: FLAG only counts as `geocoder_disagrees` when there is
    strong evidence (street/centroid precision, ADM2 contradiction, or large
    distance). FLAG with `uncertain` precision is NOT a strong signal."""
    if ev.get("geocode_acceptance") != "FLAG":
        return False
    precision = clean_text(ev.get("geocode_precision"))
    if precision in ("street", "centroid"):
        return True
    geo_check = clean_text(ev.get("geo_adm2_check"))
    orig_check = clean_text(ev.get("orig_adm2_check"))
    if geo_check == "MATCH" and orig_check == "MISMATCH":
        return True
    dist = ev.get("geocode_distance_km")
    if dist is not None and not (isinstance(dist, float) and math.isnan(dist)):
        if float(dist) >= GEOCODER_DISAGREE_DISTANCE_KM:
            return True
    return False


BOUNDARY_ZONE_MAX_DISTANCE_KM: float = 5.0
BOUNDARY_ZONE_MAX_GEOCODER_DISTANCE_KM: float = 5.0


def _qualifies_as_boundary_zone(evidence: dict) -> bool:
    """Soften adm_mismatch into boundary_zone when the GPS sits near the raw
    polygon edge AND the geocoder corroborates the GPS location.

    Conditions (all required):
      - GPS distance to raw-declared polygon < BOUNDARY_ZONE_MAX_DISTANCE_KM
      - Geocoder ran AND its result agrees with the GPS — proxied by
        geocode_distance_km < BOUNDARY_ZONE_MAX_GEOCODER_DISTANCE_KM (so
        geocoder fell within ~5 km of GPS, very likely in the same polygon).
        Without geocoder evidence the softening doesn't fire — we keep the
        stricter adm_mismatch label.
      - coordinate_source == "original" — boundary_zone is about original
        GPS validity, not about geocoder fills.
    """
    distance_polygon = evidence.get("distance_to_raw_polygon_km")
    if distance_polygon is None or pd.isna(distance_polygon):
        return False
    if distance_polygon >= BOUNDARY_ZONE_MAX_DISTANCE_KM:
        return False

    if clean_text(evidence.get("coordinate_source")) != "original":
        return False

    geocode_distance = evidence.get("geocode_distance_km")
    if geocode_distance is None or pd.isna(geocode_distance):
        return False
    if geocode_distance >= BOUNDARY_ZONE_MAX_GEOCODER_DISTANCE_KM:
        return False

    return True


def resolve_coordinate_quality(evidence: dict, scope: dict) -> tuple[str, str]:
    """Apply COORDINATE_QUALITY_PRECEDENCE to per-school evidence.

    Returns (coordinate_quality, coordinate_quality_reason).

    Required `evidence` keys (None / NaN / "" allowed):
        has_coords        bool
        in_bounds         bool
        swapped           bool
        adm1_status       str   (one of QC_ADM_STATUSES)
        adm2_status       str
        cluster_size_exact int
        coordinate_source  str   (canonical: original|geocoded|centroid_cascade|"")
        geocode_precision  str   (street|centroid|uncertain|"")
        geocode_acceptance str   (ACCEPT|ACCEPT_CENTROID|ACCEPT_WITH_FLAG|FLAG|REJECT|KEEP_ORIGINAL|"")
        geo_adm2_check     str
        orig_adm2_check    str
        geocode_distance_km float

    `scope` keys:
        final_match_level  str   (one of FINAL_MATCH_LEVELS)
    """
    final_level = scope.get("final_match_level", "adm1")
    if final_level not in FINAL_MATCH_LEVELS:
        raise ValueError(f"final_match_level={final_level!r} not in {FINAL_MATCH_LEVELS}")

    has_coords = bool(evidence.get("has_coords"))

    # 1. Missing
    if not has_coords:
        return "missing", "missing"

    # 2. Swapped
    if bool(evidence.get("swapped")):
        return "swapped", "swapped"

    # 3. Out of bounds
    if not bool(evidence.get("in_bounds", True)):
        return "out_of_bounds", "bbox"

    # 4. ADM mismatch — using best level available per country policy.
    # Soften to `boundary_zone` when geocoder concurs with the original GPS
    # AND the school sits within 5 km of the raw-declared polygon edge.
    # `boundary_zone` is ranked between geocoder_disagrees and geocoded_centroid
    # in COORDINATE_QUALITY_PRECEDENCE — softer than adm_mismatch.
    adm2_status = clean_text(evidence.get("adm2_status")) or "NOT_RUN"
    adm1_status = clean_text(evidence.get("adm1_status")) or "NOT_RUN"

    if final_level == "adm2" and adm2_status == "MISMATCH":
        if _qualifies_as_boundary_zone(evidence):
            return "boundary_zone", "boundary_zone_<5km"
        return "adm_mismatch", "adm2_mismatch"
    if final_level in ("adm2", "adm1") and adm1_status == "MISMATCH":
        # O1 (review-cycle 2026-05-10): at adm1-only countries (final_level=adm1),
        # boundary_zone softening would label rows that disagree at the ONLY
        # validated level as "polygon-edge artifact", which is semantically
        # wrong. Keep adm_mismatch to preserve the label invariant across
        # countries. (See docs/coordinate_quality_spec.md §5.2-H1.)
        if final_level == "adm1":
            return "adm_mismatch", "adm1_mismatch"
        if _qualifies_as_boundary_zone(evidence):
            return "boundary_zone", "boundary_zone_<5km"
        return "adm_mismatch", "adm1_mismatch"

    # 5. Cluster centroid — covers two placeholder patterns:
    # (a) classical: n>=5 schools at same coord (current rule, source must be original)
    # (b) sub-5: n in [2,4] at same coord with different MoE-declared admin/locality.
    #     For n=2 specifically, a frontier rescue keeps the row as gps_validated
    #     when the cluster sits within 5 km of an admin boundary (legitimate
    #     cross-boundary geo-mismatch, not placeholder). For n>=3, the diff is
    #     statistically unlikely to be coincidence so we label cluster_centroid
    #     without a frontier rescue.
    cluster = int(evidence.get("cluster_size_exact") or 0)
    source = clean_text(evidence.get("coordinate_source"))
    diff_admin_locality = bool(evidence.get("cluster_diff_admin_locality", False))
    n2_frontier = bool(evidence.get("n2_frontier_rescue", False))
    if source == "original":
        if cluster >= CLUSTER_THRESHOLD:
            return "cluster_centroid", "cluster_ge5"
        if cluster in (3, 4) and diff_admin_locality:
            return "cluster_centroid", "cluster_3_4_diff_admin_locality"
        if cluster == 2 and diff_admin_locality and not n2_frontier:
            return "cluster_centroid", "cluster_2_diff_admin_locality"

    # 6. Geocoder disagreement (strong evidence only)
    if _strong_geocoder_disagreement(evidence):
        return "geocoder_disagrees", "geocoder_compare"

    # 7. Geocoded centroid (from fill or PAN-style cascade)
    if source in ("geocoded", "centroid_cascade"):
        precision = clean_text(evidence.get("geocode_precision"))
        if source == "centroid_cascade" or precision == "centroid":
            return "geocoded_centroid", "fill_centroid"
        if precision == "street":
            return "geocoded_street", "fill_street"
        # geocoded but precision unknown — treat as centroid (conservative).
        return "geocoded_centroid", "fill_centroid"

    # 8. GPS validated — original GPS, validated at best level, no cluster.
    # Per §5.5 (2026-05-11): "validation" means the GPS was cross-checked
    # against a *declared* admin from the raw source. Country configurations
    # that lack raw admin codes (final_level ∈ {spatial_only, bbox_only})
    # cannot produce a real MATCH — the spatial join is an *assignment*, not
    # a validation. Without an independent raw declaration to contradict the
    # GPS, we have no evidence that the coordinate is accurate; gps_unverified
    # is the honest label. JAM is the canonical example (no admin codes from
    # the EMIS source; 914 schools previously labeled gps_validated trivially).
    validated = False
    if final_level == "adm2" and adm2_status == "MATCH":
        validated = True
    elif final_level == "adm1" and adm1_status == "MATCH":
        validated = True
    # final_level ∈ {spatial_only, bbox_only} → validated stays False.

    if validated and source == "original" and cluster < CLUSTER_THRESHOLD:
        return "gps_validated", "validated"

    # 9. Special reason: FLAG with uncertain precision wasn't strong enough for #6
    if clean_text(evidence.get("geocode_acceptance")) == "FLAG":
        return "gps_unverified", "geocoder_low_confidence"

    # 10. Default
    return "gps_unverified", "default_unverified"


# ===========================================================================
# 5. Geocode target discovery (for Step 05 in-memory consumption)
# ===========================================================================

def compute_geocode_targets(cima: pd.DataFrame, addr_df: pd.DataFrame | None,
                            qc_evidence: pd.DataFrame | None = None) -> dict[str, set[str]]:
    """Build the target lists Step 05 used to read from CSV side-reports.

    Returns a dict:
        {
          "missing":     ids of schools without coordinates,
          "zeros":       ids of schools with (0, 0) placeholders,
          "out_of_bounds": ids whose `qc_in_bounds == False` in evidence,
          "mismatches":  ids whose ADM1/ADM2 status is MISMATCH (per v2 enums),
          "dup_addr":    ids in exact clusters with diff addresses,
          "centroids":   ids in exact clusters >= CLUSTER_THRESHOLD AND with
                         distinct addresses (excludes shared-campus / same-building
                         co-locations to preserve legacy Step 05 semantics),
        }

    `qc_evidence` is a per-school DataFrame with at least
    `id_centro`, `qc_in_bounds`, `qc_adm1_status`, `qc_adm2_status` columns.
    If None, the OOB and mismatches sets are empty (caller can populate later).

    Centroid bucket semantics: legacy `_detect_centroids_in_coords` in
    `04_geocode_missing.py` excluded shared-campus colocations using the
    address signal. This implementation reproduces that by intersecting
    the cluster mask with `has_diff_address_in_cluster()`. If no addresses
    are available the centroid bucket falls back to pure cluster detection
    (logs a note via `centroids_address_filtered` flag in the return dict).
    """
    out: dict[str, set[str] | bool] = {
        "missing": set(),
        "zeros": set(),
        "out_of_bounds": set(),
        "mismatches": set(),
        "dup_addr": set(),
        "centroids": set(),
        "centroids_address_filtered": False,
    }
    if cima.empty:
        return out  # type: ignore[return-value]

    ids = cima["id_centro"].astype(str)
    lat = pd.to_numeric(cima["latitud"], errors="coerce")
    lon = pd.to_numeric(cima["longitud"], errors="coerce")

    out["missing"] = set(ids[lat.isna() | lon.isna()])
    out["zeros"] = set(ids[(lat == 0) | (lon == 0)])

    if qc_evidence is not None and not qc_evidence.empty:
        ev = qc_evidence.copy()
        ev["id_centro"] = ev["id_centro"].astype(str)

        # OOB now flows from the dedicated qc_in_bounds flag (v2 contract).
        # Rows without a usable coordinate also have qc_in_bounds=False (no
        # bbox to test); they belong in `missing`, not `out_of_bounds`. Restrict
        # OOB to rows that actually have a coordinate so the buckets are
        # disjoint and the dashboard funnel does not double-count.
        if "qc_in_bounds" in ev.columns:
            has_coord_mask = pd.Series(False, index=ev.index)
            ev_lat = None
            ev_lon = None
            if "latitud" in ev.columns and "longitud" in ev.columns:
                ev_lat = pd.to_numeric(ev["latitud"], errors="coerce")
                ev_lon = pd.to_numeric(ev["longitud"], errors="coerce")
            else:
                coord_lookup = cima[["id_centro", "latitud", "longitud"]].copy()
                coord_lookup["id_centro"] = coord_lookup["id_centro"].astype(str)
                ev = ev.merge(coord_lookup, on="id_centro", how="left")
                ev_lat = pd.to_numeric(ev["latitud"], errors="coerce")
                ev_lon = pd.to_numeric(ev["longitud"], errors="coerce")
            if ev_lat is not None and ev_lon is not None:
                # Match `_evidence_for_row`: either coord == 0 is a placeholder.
                has_coord_mask = (
                    ev_lat.notna() & ev_lon.notna()
                    & (ev_lat != 0) & (ev_lon != 0)
                )
            oob_mask = (ev["qc_in_bounds"] == False) & has_coord_mask  # noqa: E712 — explicit bool comparison
            out["out_of_bounds"] = set(ev.loc[oob_mask, "id_centro"])

        # Admin mismatches — per v2, OOB is no longer a "MISMATCH" of any level.
        adm1_mis = ev.get("qc_adm1_status", pd.Series([], dtype=str)) == "MISMATCH"
        adm2_mis = ev.get("qc_adm2_status", pd.Series([], dtype=str)) == "MISMATCH"
        out["mismatches"] = set(ev.loc[adm1_mis | adm2_mis, "id_centro"])

    sizes = detect_clusters_exact(cima)
    cluster_mask = sizes >= CLUSTER_THRESHOLD

    diff = has_diff_address_in_cluster(cima, addr_df)
    if cluster_mask.any():
        if addr_df is not None and not addr_df.empty and diff.any():
            # Restrict to schools with actually-different addresses in the cluster
            # (shared-campus exclusion — matches legacy Step 05 semantics).
            out["centroids"] = set(ids[cluster_mask & diff])
            out["centroids_address_filtered"] = True
        else:
            out["centroids"] = set(ids[cluster_mask])
            out["centroids_address_filtered"] = False

    if diff.any():
        out["dup_addr"] = set(ids[diff])

    return out  # type: ignore[return-value]


# ===========================================================================
# 5b. Dashboard QC baseline (per-country test rollup × sector × pre/post)
# ===========================================================================

def compute_qc_baseline(cima: pd.DataFrame, iso: str, scope: dict) -> list[dict]:
    """Roll up the 7 QC tests per country across (mode, sector) buckets.

    Returns 6 rows: (mode ∈ {pre, post}) × (sector ∈ {total, public, private}).
    Each row carries the values the dashboard QCMatrix reads directly:
        n_total, n_georef,
        test1_wgs84_n, test2_zero_zero_n, test3_oob_n, test4_swapped_n,
        test5_georef_pct,
        test6_adm1_match_pct, test6_adm1_n_evaluated,
        test6_adm2_match_pct, test6_adm2_n_evaluated,
        test7_dup_pct, test7_dup_n

    Tests 1/2/4 capture historical events Step 01 already corrected, so the
    post-CIMA carries no signal. PRE values for these come from
    `qc_pre_history.QC_PRE_HISTORY` (Total only — sector breakdown of
    historical events is part of the deferred auto-generated path); POST
    is 0 by construction.

    Tests 3/5/6/7 are derived from the enriched CIMA. PRE filters to the
    raw subset (`coordinate_source ∈ {"", "original"}`); POST uses full CIMA.
    """
    pre_history = QC_PRE_HISTORY.get(iso, {})
    final_level = scope.get("final_match_level", "")

    src = cima.get("coordinate_source", pd.Series([""] * len(cima), index=cima.index))
    src = src.fillna("").astype(str)
    # Always restrict to the raw (pre-geocoding) subset. Step-03 runs BEFORE
    # Step-04/05 geocoder fills, so the matrix should never include geocoded
    # rows in any test denominator. The pre/post toggle only changes how we
    # render tests 1/2/4 (historical-event tests Step 01 corrected); tests
    # 3/5/6/7 are computed identically for both modes on the raw subset.
    raw_mask = src.isin(["", "original"])

    sector_col = cima.get("sector", pd.Series([""] * len(cima), index=cima.index))
    sector_col = sector_col.fillna("").astype(str)

    rows: list[dict] = []
    for mode in ("pre", "post"):
        for sector in ("total", "public", "private"):
            # Sector mask defines the universe (denominator for georef rate).
            # Full universe = country × sector, regardless of coordinate_source.
            sector_mask = pd.Series(True, index=cima.index)
            if sector == "public":
                sector_mask = sector_col == "Public"
            elif sector == "private":
                sector_mask = sector_col == "Private"

            full_sub = cima[sector_mask]
            n_total = int(len(full_sub))

            # Raw subset = sector ∩ raw_mask. Tests 3/5/6/7 evaluate on this
            # subset (they only have meaning for raw coords; geocoded fills
            # are out of step-03's scope).
            raw_sub = cima[sector_mask & raw_mask]

            # --- Tests 1, 2, 4 (historical-event tests) ---
            # PRE+Total: from manifest. POST: always 0. PRE+Public/Private:
            # null (sector breakdown of historical events not in v1).
            if mode == "post":
                t1, t2, t4 = 0, 0, 0
            elif sector == "total":
                t1 = int(pre_history.get("test1_utm_pre_n", 0))
                t2 = int(pre_history.get("test2_zero_zero_pre_n", 0))
                t4 = int(pre_history.get("test4_swapped_pre_n", 0))
            else:
                t1 = t2 = t4 = None

            # --- Test 3: out_of_bounds count over raw subset ---
            cq = raw_sub.get("coordinate_quality", pd.Series([], dtype=str)).fillna("").astype(str)
            t3 = int((cq == "out_of_bounds").sum())

            # --- Test 5: georef rate = (raw schools with coords) / full universe ---
            # Numerator = rows in raw subset whose coord survived as valid.
            # Denominator = full country×sector universe (3,615 for PAN total),
            # NOT the raw subset (which would inflate the rate by hiding the
            # schools that needed geocoding fill).
            n_georef = int((cq != "missing").sum())
            t5_pct = round(n_georef / n_total * 100, 1) if n_total > 0 else None

            # --- Test 6: ADM match by level (over raw subset where evaluated) ---
            adm1 = raw_sub.get("qc_adm1_status", pd.Series([], dtype=str)).fillna("").astype(str)
            adm2 = raw_sub.get("qc_adm2_status", pd.Series([], dtype=str)).fillna("").astype(str)

            adm1_eval = adm1.isin(["MATCH", "MISMATCH"])
            adm1_n_eval = int(adm1_eval.sum())
            adm1_n_mis = int((adm1 == "MISMATCH").sum())
            adm1_pct = (
                round((1 - adm1_n_mis / adm1_n_eval) * 100, 1)
                if adm1_n_eval > 0 else None
            )

            adm2_eval = adm2.isin(["MATCH", "MISMATCH"])
            adm2_n_eval = int(adm2_eval.sum())
            adm2_n_mis = int((adm2 == "MISMATCH").sum())
            adm2_pct = (
                round((1 - adm2_n_mis / adm2_n_eval) * 100, 1)
                if adm2_n_eval > 0 else None
            )

            # spatial_only / bbox_only countries don't run a name-based ADM
            # match — JAM uses parish containment (not name compare) and BRB
            # has no admin column. Force both ADM cells to None so the matrix
            # renders "—" instead of a misleading 100%.
            if final_level in ("spatial_only", "bbox_only"):
                adm1_pct = None
                adm1_n_eval = 0
                adm2_pct = None
                adm2_n_eval = 0

            # --- Test 7: % schools in clusters of ≥2 with diff addresses ---
            # `qc_cluster_diff_addr_exact` is the boolean flag from
            # `has_diff_address_in_cluster` (True if the school sits in an exact
            # cluster whose members carry ≥2 distinct addresses). Computed over
            # the raw subset; denominator is raw schools with coords.
            dup = raw_sub.get("qc_cluster_diff_addr_exact", pd.Series([], dtype=bool))
            dup_bool = dup.replace({"True": True, "False": False, "": False}).fillna(False).astype(bool)
            t7_n = int(dup_bool.sum())
            t7_pct = round(t7_n / n_georef * 100, 1) if n_georef > 0 else None

            rows.append({
                "iso": iso,
                "mode": mode,
                "sector": sector,
                "n_total": n_total,
                "n_georef": n_georef,
                "test1_wgs84_n": t1,
                "test2_zero_zero_n": t2,
                "test3_oob_n": t3,
                "test4_swapped_n": t4,
                "test5_georef_pct": t5_pct,
                "test6_adm1_match_pct": adm1_pct,
                "test6_adm1_n_evaluated": adm1_n_eval,
                "test6_adm2_match_pct": adm2_pct,
                "test6_adm2_n_evaluated": adm2_n_eval,
                "test7_dup_pct": t7_pct,
                "test7_dup_n": t7_n,
            })
    return rows


# ===========================================================================
# 6. I/O helpers (BID boundaries + spatial join)
# ===========================================================================

_boundaries_cache: dict[int, "Any"] = {}


def load_boundaries(level: int = 1):
    """Load BID LAC admin polygons via pyshp (handles latin-1 encoding).

    Mirrors the legacy `02_qc_coordinates.load_boundaries`. Cached per process.
    Uses pyshp + shapely directly (NOT geopandas.read_file) because the BID
    shapefiles are encoded in latin-1 and geopandas misreads accented names
    on Windows defaults.
    """
    if level in _boundaries_cache:
        return _boundaries_cache[level]
    import geopandas as gpd
    import shapefile as shp_lib
    from shapely.geometry import shape as shp_shape

    if level == 0:
        path = BOUNDS_DIR / "level 0" / "lac-level-0.shp"
    elif level == 1:
        path = BOUNDS_DIR / "level 1" / "lac-level-1.shp"
    elif level == 2:
        path = BOUNDS_DIR / "level 2" / "lac-level-2.shp"
    else:
        raise ValueError(f"Unsupported admin level: {level}")
    if not path.exists():
        raise FileNotFoundError(path)

    sf = shp_lib.Reader(str(path), encoding="latin-1")
    fields = [f[0] for f in sf.fields[1:]]
    records, geoms = [], []
    for i, rec in enumerate(sf.iterRecords()):
        records.append(dict(zip(fields, rec)))
        geoms.append(shp_shape(sf.shape(i).__geo_interface__))

    gdf = gpd.GeoDataFrame(records, geometry=geoms, crs="EPSG:4326")
    name_col = f"ADM{level}_EN"
    norm_col = f"adm{level}_norm"
    if name_col in gdf.columns:
        gdf[norm_col] = gdf[name_col].apply(normalize_name)
    _boundaries_cache[level] = gdf
    return gdf


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two points (vectorizable for scalars)."""
    R = 6371.0
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = (math.sin(dlat / 2) ** 2
         + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _distance_to_polygon_boundary_km(lat: float, lon: float, geom) -> float:
    """Great-circle distance in km from a point to the nearest polygon edge."""
    from shapely.geometry import Point
    from shapely.ops import nearest_points

    pt = Point(lon, lat)
    nearest = nearest_points(geom.boundary, pt)[0]
    return _haversine_km(lat, lon, nearest.y, nearest.x)


def _distance_to_raw_polygon_km(
    rows: pd.DataFrame,
    polygons,
    raw_name_col: str,
    level: int,
    iso: str,
) -> "pd.Series":
    """Per-row great-circle distance (km) from (latitud, longitud) to the
    exterior of the polygon whose name matches `raw_name_col`.

    Used by the boundary_zone label as a softening signal for adm_mismatch:
    schools < 5 km from the raw-declared polygon's edge are likely boundary
    overlaps rather than full misplacements.

    Returns NaN for rows whose `raw_name_col` doesn't resolve to a polygon
    (e.g. raw blank, alias mismatch, opt-out countries).
    """
    from shapely.geometry import Point
    from shapely.ops import nearest_points

    norm_col = f"adm{level}_norm"
    if norm_col not in polygons.columns:
        return pd.Series(np.nan, index=rows.index)

    aliases = (ADM2_ALIASES if level == 2 else ADM1_ALIASES).get(iso, {})

    polygons_local = polygons[[norm_col, "geometry"]].copy()
    polygons_local["_norm_name"] = polygons_local[norm_col].astype(str)

    # Lookup: norm name -> geometry. If multiple polygons share a norm name
    # (rare, but possible after alias collisions), pick the first.
    name_to_geom: dict[str, "Any"] = {}
    for nm, geom in zip(polygons_local["_norm_name"], polygons_local["geometry"]):
        if nm and nm not in name_to_geom:
            name_to_geom[nm] = geom

    distances: list[float] = []
    for _, r in rows.iterrows():
        raw_name = clean_text(r.get(raw_name_col))
        if not raw_name:
            distances.append(float("nan"))
            continue
        norm = normalize_name(raw_name)
        norm = aliases.get(norm, norm)
        geom = name_to_geom.get(norm)
        if geom is None:
            distances.append(float("nan"))
            continue
        lat = r.get("latitud")
        lon = r.get("longitud")
        if lat is None or lon is None or pd.isna(lat) or pd.isna(lon):
            distances.append(float("nan"))
            continue
        pt = Point(lon, lat)
        # nearest_points returns (point_on_polygon_boundary, query_point);
        # we use the boundary geometry to ensure distance reflects the edge,
        # not "0 km" when the point is interior. For boundary_zone we only
        # compute on MISMATCH rows so the point is OUTSIDE the polygon —
        # nearest_points on the polygon itself gives the same answer.
        try:
            nearest = nearest_points(geom.boundary, pt)[0]
        except Exception:
            distances.append(float("nan"))
            continue
        distances.append(_haversine_km(lat, lon, nearest.y, nearest.x))

    return pd.Series(distances, index=rows.index)


def rescue_near_border_polygons(out: pd.DataFrame, country_polys, level: int,
                                polygon_name_col: str, polygon_pcode_col: str,
                                polygon_norm_col: str, pcode_col: str,
                                k: int = 2) -> int:
    """Rescue rows where the spatial join failed (NO_POLYGON) but the ministry
    declared a valid admin code AND the corresponding polygon is among the K
    nearest polygons to the school's coords.

    Conservative: cross-checks the raw declaration (`raw_adm{level}_code`)
    against geographic proximity. Without the proximity cross-check we'd be
    blindly trusting raw codes; without raw codes we'd be blindly trusting
    proximity. Together they're robust.

    Returns the number of rescued rows.
    """
    raw_code_col = f"raw_adm{level}_code"
    if (raw_code_col not in out.columns
            or country_polys is None
            or country_polys.empty):
        return 0

    name_field = f"ADM{level}_EN"
    pcode_field = f"ADM{level}_PCODE"
    norm_field = f"adm{level}_norm"
    if pcode_field not in country_polys.columns or name_field not in country_polys.columns:
        return 0

    no_poly = out[polygon_pcode_col].astype(str).str.strip() == ""
    has_raw = out[raw_code_col].astype(str).str.strip() != ""
    has_coord = out["latitud"].notna() & out["longitud"].notna()
    in_bbox = out.get("qc_in_bounds", pd.Series(False, index=out.index)) == True  # noqa: E712
    candidates = no_poly & has_raw & has_coord & in_bbox
    if not candidates.any():
        return 0

    from shapely.geometry import Point

    pcode_to_polygons = country_polys.groupby(pcode_field).first()

    rescued = 0
    for idx in out.index[candidates]:
        raw_code = str(out.at[idx, raw_code_col]).strip()
        if raw_code not in pcode_to_polygons.index:
            continue
        match_poly = pcode_to_polygons.loc[raw_code]

        school_pt = Point(float(out.at[idx, "longitud"]), float(out.at[idx, "latitud"]))
        all_dists = country_polys.geometry.distance(school_pt)
        nearest_pcodes = country_polys.loc[all_dists.nsmallest(k).index, pcode_field].tolist()
        if raw_code not in nearest_pcodes:
            continue

        out.at[idx, polygon_pcode_col] = raw_code
        out.at[idx, polygon_name_col] = match_poly[name_field]
        out.at[idx, polygon_norm_col] = (
            match_poly[norm_field] if norm_field in match_poly.index
            else normalize_name(match_poly[name_field])
        )
        out.at[idx, pcode_col] = raw_code
        rescued += 1
    return rescued


def rescue_near_border_polygons_by_name(out: pd.DataFrame, country_polys, level: int,
                                        iso: str,
                                        polygon_name_col: str, polygon_pcode_col: str,
                                        polygon_norm_col: str, pcode_col: str,
                                        tolerance_km: float = 1.0) -> int:
    """Name-based parallel to `rescue_near_border_polygons` for countries that
    lack a numeric raw admin code (BHS) but have a raw admin NAME column.

    Rescues NO_POLYGON rows where:
      - the school is inside ADM0 bbox (`qc_in_bounds=True`),
      - the nearest BID polygon boundary lies within `tolerance_km` great-circle
        kilometres of the school coordinate,
      - the nearest polygon's name MATCHes the raw admin name via the canonical
        `admin_match` rule (which consults ADM{level}_ALIASES and
        ADM{level}_AGGREGATIONS for this ISO).

    Without the name-match cross-check we'd be snapping schools blindly to any
    nearest polygon; with it we only rescue when the geographic and ministerial
    signals concur. Typical use: archipelagic coasts where BID polygon edges are
    cartographically simplified and real-GPS schools fall sub-km outside.

    Returns the number of rescued rows.
    """
    raw_name_col = f"qc_raw_adm{level}"
    if (raw_name_col not in out.columns
            or country_polys is None
            or country_polys.empty):
        return 0

    name_field = f"ADM{level}_EN"
    pcode_field = f"ADM{level}_PCODE"
    norm_field = f"adm{level}_norm"
    if pcode_field not in country_polys.columns or name_field not in country_polys.columns:
        return 0

    no_poly = out[polygon_pcode_col].astype(str).str.strip() == ""
    has_raw = out[raw_name_col].astype(str).str.strip() != ""
    has_coord = out["latitud"].notna() & out["longitud"].notna()
    in_bbox = out.get("qc_in_bounds", pd.Series(False, index=out.index)) == True  # noqa: E712
    candidates = no_poly & has_raw & has_coord & in_bbox
    if not candidates.any():
        return 0

    from shapely.geometry import Point

    rescued = 0
    for idx in out.index[candidates]:
        raw_name = str(out.at[idx, raw_name_col]).strip()
        lat = float(out.at[idx, "latitud"])
        lon = float(out.at[idx, "longitud"])
        school_pt = Point(lon, lat)
        all_dists_deg = country_polys.geometry.distance(school_pt)
        nearest_idx = all_dists_deg.idxmin()
        nearest_poly = country_polys.loc[nearest_idx]
        nearest_km = _distance_to_polygon_boundary_km(lat, lon, nearest_poly.geometry)
        if nearest_km > tolerance_km:
            continue
        if admin_match(raw_name, nearest_poly[name_field], iso, level=level) != "MATCH":
            continue
        out.at[idx, polygon_pcode_col] = nearest_poly[pcode_field]
        out.at[idx, polygon_name_col] = nearest_poly[name_field]
        out.at[idx, polygon_norm_col] = (
            nearest_poly[norm_field] if norm_field in nearest_poly.index
            else normalize_name(nearest_poly[name_field])
        )
        out.at[idx, pcode_col] = nearest_poly[pcode_field]
        rescued += 1
    return rescued


def spatial_join_adm(coords: pd.DataFrame, polygons, level: int,
                     lat_col: str = "latitud", lon_col: str = "longitud") -> pd.DataFrame:
    """Spatial-join `coords` (must include `id_centro`) against `polygons`.

    Returns a DataFrame with `id_centro`, `polygon_adm{level}` (name),
    `polygon_adm{level}_pcode`, `polygon_adm{level}_norm`.
    """
    import geopandas as gpd
    from shapely.geometry import Point

    name_col = f"ADM{level}_EN"
    pcode_col = f"ADM{level}_PCODE"
    norm_col = f"adm{level}_norm"

    valid = coords.dropna(subset=[lat_col, lon_col]).copy()
    if valid.empty:
        return pd.DataFrame(columns=["id_centro", f"polygon_adm{level}",
                                     f"polygon_adm{level}_pcode",
                                     f"polygon_adm{level}_norm"])

    geom = [Point(lon, lat) for lon, lat in zip(valid[lon_col], valid[lat_col])]
    gdf = gpd.GeoDataFrame(valid[["id_centro"]], geometry=geom, crs="EPSG:4326")
    cols_we_need = [c for c in (name_col, pcode_col, norm_col, "geometry") if c in polygons.columns]
    j = gpd.sjoin(gdf, polygons[cols_we_need], how="left", predicate="within")
    j = j.drop_duplicates(subset="id_centro", keep="first")
    out = j[["id_centro", name_col, pcode_col, norm_col]].rename(columns={
        name_col: f"polygon_adm{level}",
        pcode_col: f"polygon_adm{level}_pcode",
        norm_col: f"polygon_adm{level}_norm",
    })
    return out.reset_index(drop=True)


# ===========================================================================
# 7. Canonical finalize orchestrator (pure on inputs, returns enriched CIMA)
# ===========================================================================

# Output columns produced by `finalize_cima_evidence`. These augment whatever
# evidence already lives in the input CIMA (e.g. Step 05 geocoder columns).
FINALIZE_OUTPUT_COLUMNS: tuple[str, ...] = (
    "adm1_pcode", "adm2_pcode",
    "qc_in_bounds", "qc_scope_class", "include_in_spatial_indicators", "qc_swapped",
    "qc_adm1_status", "qc_adm2_status", "qc_match_level",
    "qc_distance_to_raw_polygon_km",
    "qc_cluster_size_exact", "qc_cluster_diff_addr_exact",
    "qc_cluster_size_50m", "qc_cluster_diff_addr_50m",
    "qc_cluster_diff_admin_locality", "qc_n2_frontier_rescue",
    "coordinate_source", "coordinate_quality", "coordinate_quality_reason",
    "qc_centroid_bias", "qc_evidence_version",
)


def _classify_scope_row(
    row: pd.Series,
    *,
    iso: str,
    country_geom,
) -> str:
    """Classify a row by territory/scope, independent of coordinate_quality."""
    lat = row.get("latitud")
    lon = row.get("longitud")
    if lat is None or lon is None or pd.isna(lat) or pd.isna(lon):
        return "missing"
    if abs(float(lat)) > 90 or abs(float(lon)) > 180:
        return "invalid_numeric"

    in_bounds = bool(row.get("qc_in_bounds", False))
    if country_geom is None:
        return "inside_mainland_bbox" if in_bounds else "outside_country"

    from shapely.geometry import Point

    pt = Point(float(lon), float(lat))
    try:
        if country_geom.covers(pt):
            return "inside_mainland_bbox" if in_bounds else "remote_territory_or_island"
        distance_km = _distance_to_polygon_boundary_km(float(lat), float(lon), country_geom)
    except Exception:
        return "inside_mainland_bbox" if in_bounds else "outside_country"

    if distance_km <= ADM0_BORDER_REVIEW_DISTANCE_KM:
        return "near_border_review"
    return "outside_country"


_HIGH_SEVERITY_QUALITIES = frozenset({
    "adm_mismatch",
    "geocoder_disagrees",
    "cluster_centroid",
    "swapped",
    "missing",
    "out_of_bounds",
})


def _spatial_indicator_policy(row: pd.Series, *, final_match_level: str = "adm2"):
    """Nullable policy flag derived from scope classification + QC severity.

    Returns:
      - True  => safe to include in automated spatial indicators
      - False => exclude
      - <NA>  => keep in master table but requires manual review / explicit policy

    Reference: docs/coordinate_quality_spec.md §4-5.
    """
    scope_class = row.get("qc_scope_class")
    quality = row.get("coordinate_quality")
    scope_class = "" if scope_class is None or pd.isna(scope_class) else str(scope_class)
    quality = "" if quality is None or pd.isna(quality) else str(quality)

    if scope_class in {"missing", "invalid_numeric", "outside_country"}:
        return False
    if quality in {"missing", "swapped"}:
        return False

    # Street-level geocoded coords (score ≥95) anchor to a precise address.
    # Promote to True even at near_border_review. (spec §5.3)
    if quality == "geocoded_street":
        return True

    # boundary_zone is by construction: (a) original GPS, (b) <5 km from raw
    # polygon edge, (c) geocoder corroborates within 5 km of GPS. The
    # conjunction is stronger than gps_unverified. (spec §5.2)
    #
    # Invariant from the resolver: at adm1-only countries the boundary_zone
    # label is never produced when adm1_status != MATCH (see
    # resolve_coordinate_quality, branch B). So a row that reaches this point
    # already meets the adm1-only safety condition; no extra gate needed.
    if quality == "boundary_zone":
        return True

    # Centroid-precision labels (geocoded_centroid + cluster_centroid) have
    # ~1-5 km positional error. That floor exceeds the 15-min walking isochrone
    # (~1.25 km @ 5 km/h), so auto-including them in walking-accessibility
    # indicators conflates municipal geometry with school location — the
    # classic centroid-as-aggregate ecological fallacy (Apparicio et al. 2008;
    # Hewko et al. 2002). Provenance (deliberate cascade vs covert cluster)
    # is methodologically irrelevant: same precision floor, same NaN treatment.
    # Downstream consumers may opt-in per indicator (e.g. ADM2-aggregate or
    # 60-min indicators may include them after explicit policy decision).
    # (spec §5.4 — 2026-05-11 update)
    if quality in {"geocoded_centroid", "cluster_centroid"}:
        return pd.NA

    if scope_class == "near_border_review":
        # Severity gate (spec §5.1): the rescue must not lift high-severity
        # labels even when adm1_status=MATCH. Only gps_validated (full rescue)
        # or gps_unverified+adm1=MATCH (partial rescue) qualify.
        if quality in _HIGH_SEVERITY_QUALITIES:
            return pd.NA
        if quality == "gps_validated":
            return True
        adm1_status = row.get("qc_adm1_status", "")
        adm1_status = "" if adm1_status is None or pd.isna(adm1_status) else str(adm1_status)
        if adm1_status == "MATCH":
            return True
        return pd.NA

    if quality in {"adm_mismatch", "geocoder_disagrees"}:
        return pd.NA

    if scope_class in {"inside_mainland_bbox", "remote_territory_or_island"}:
        return True
    return False


def _evidence_for_row(row: pd.Series, scope: dict) -> dict:
    """Assemble the evidence dict required by `resolve_coordinate_quality`."""
    def _txt(key: str, default: str = "") -> str:
        value = row.get(key)
        if value is None or pd.isna(value):
            return default
        text = str(value)
        return text if text else default

    def _int(key: str, default: int = 0) -> int:
        value = row.get(key)
        if value is None or pd.isna(value):
            return default
        return int(value)

    lat = row.get("latitud")
    lon = row.get("longitud")
    # Either coordinate exactly equal to 0.0 is a placeholder, not a valid
    # GPS reading — no LAC country has lon=0 (range is roughly [-120, -30])
    # and no school in the dataset reports an exact lat=0.0 in a way that
    # is geographically plausible (verified across 21 countries: lat=0
    # always pairs with a populated lon, indicating only-half-of-coords
    # was filled). Treat any single zero the same as both zeros: missing.
    has_coords = (
        lat is not None and lon is not None
        and not (isinstance(lat, float) and math.isnan(lat))
        and not (isinstance(lon, float) and math.isnan(lon))
        and lat != 0 and lon != 0
    )
    return {
        "has_coords": has_coords,
        "in_bounds": bool(row.get("qc_in_bounds", False)) if has_coords else False,
        "swapped": bool(row.get("qc_swapped", False)),
        "adm1_status": _txt("qc_adm1_status", "NOT_RUN"),
        "adm2_status": _txt("qc_adm2_status", "NOT_RUN"),
        "cluster_size_exact": _int("qc_cluster_size_exact", 0),
        "cluster_diff_admin_locality": bool(row.get("qc_cluster_diff_admin_locality", False)),
        "n2_frontier_rescue": bool(row.get("qc_n2_frontier_rescue", False)),
        "coordinate_source": _txt("coordinate_source", ""),
        "geocode_precision": _txt("geocode_precision", ""),
        "geocode_acceptance": _txt("acceptance", ""),
        "geo_adm1_check": _txt("geo_adm1_check", ""),
        "geo_adm2_check": _txt("geo_adm2_check", ""),
        "orig_adm1_check": _txt("orig_adm1_check", ""),
        "orig_adm2_check": _txt("orig_adm2_check", ""),
        "geocode_distance_km": row.get("geocode_distance_km"),
        "distance_to_raw_polygon_km": row.get("qc_distance_to_raw_polygon_km"),
    }


def finalize_cima_evidence(cima: pd.DataFrame,
                           addr_df: pd.DataFrame | None,
                           boundaries_by_level: dict[int, "Any"],
                           iso: str,
                           scope: dict) -> pd.DataFrame:
    """Compute the canonical v2 evidence + label for one country's CIMA.

    Pure function in the sense that, given the same inputs, returns the same
    DataFrame. No file I/O. The orchestration in `02_qc_coordinates.py`
    handles loading boundaries / addresses / scope and writing the result.

    Args:
        cima: country CIMA (must have id_centro, latitud, longitud, etc.).
              May already carry Step 05 enrichment (acceptance, latitud_geocoded
              etc.) — those are preserved untouched.
        addr_df: per-school raw addresses, with at least `id_centro` and any of
              `raw_adm1`, `raw_adm2`, `raw_locality`, `raw_street`. Optional.
        boundaries_by_level: dict {1: gdf_adm1, 2: gdf_adm2}. Either may be
              None or empty (e.g. countries without ADM2 polygons).
        iso: ISO code (drives ADM1_ALIASES / ADM2_ALIASES + bbox lookup).
        scope: dict with at least `final_match_level`.

    Returns:
        A new DataFrame with all original CIMA columns PLUS the columns in
        FINALIZE_OUTPUT_COLUMNS. Idempotent — re-running on its own output
        produces the same DataFrame.
    """
    if "id_centro" not in cima.columns:
        raise ValueError("CIMA must have an id_centro column")

    out = cima.copy()

    # Defensive cleanup: drop _x/_y leftovers from prior runs that hit the
    # addr_df merge before its overlap-drop guard was added (observed for SUR).
    # Restricted to known merge candidates so unrelated columns are untouched.
    _addr_merge_candidates = ("raw_adm1", "raw_adm2", "qc_raw_adm1", "qc_raw_adm2", "raw_adm1_code")
    _legacy_suffixed = [
        f"{c}{suf}" for c in _addr_merge_candidates
        for suf in ("_x", "_y")
        if f"{c}{suf}" in out.columns
    ]
    if _legacy_suffixed:
        out = out.drop(columns=_legacy_suffixed)

    n = len(out)
    final_level = scope.get("final_match_level", "adm1")
    if final_level not in FINAL_MATCH_LEVELS:
        raise ValueError(f"final_match_level={final_level!r} not in {FINAL_MATCH_LEVELS}")

    # --- 0. Defensive promotion: latitud_geocoded → latitud ---
    # Si Step 01 se rerunea sobre un CIMA enriquecido, latitud/longitud (en
    # SCHEMA) se reescriben desde raw y las coords geocodificadas se pierden.
    # latitud_geocoded sobrevive (no es base). Si una fila tiene
    # coordinate_source ∈ {geocoded, centroid_cascade} y la base lat es
    # NaN/0 pero la geocoded está poblada, recuperamos la geocoded como base.
    if "latitud_geocoded" in out.columns and "longitud_geocoded" in out.columns:
        lat_base = pd.to_numeric(out["latitud"], errors="coerce")
        lon_base = pd.to_numeric(out["longitud"], errors="coerce")
        lat_geo = pd.to_numeric(out["latitud_geocoded"], errors="coerce")
        lon_geo = pd.to_numeric(out["longitud_geocoded"], errors="coerce")
        src = out.get("coordinate_source", pd.Series([""] * len(out))).fillna("")
        eligible_src = src.isin(("geocoded", "centroid_cascade"))
        base_missing = lat_base.isna() | lon_base.isna() | ((lat_base == 0) & (lon_base == 0))
        geo_present = lat_geo.notna() & lon_geo.notna() & ~((lat_geo == 0) & (lon_geo == 0))
        promote = eligible_src & base_missing & geo_present
        if promote.any():
            out.loc[promote, "latitud"] = lat_geo[promote]
            out.loc[promote, "longitud"] = lon_geo[promote]

    # --- 1. Coordinate normalization (DMS→DD if needed) ---
    lat_numeric = pd.to_numeric(out["latitud"], errors="coerce")
    if lat_numeric.notna().sum() == 0 and out["latitud"].notna().sum() > 0:
        out["latitud"] = out["latitud"].apply(dms_to_dd)
        out["longitud"] = out["longitud"].apply(dms_to_dd)
    else:
        out["latitud"] = lat_numeric
        out["longitud"] = pd.to_numeric(out["longitud"], errors="coerce")

    # Treat (0, 0) as missing
    zero_mask = (out["latitud"] == 0) & (out["longitud"] == 0)
    out.loc[zero_mask, ["latitud", "longitud"]] = np.nan

    # --- 2. Bbox + swap evidence ---
    bbox = COUNTRY_BBOX.get(iso)
    if bbox is None:
        out["qc_in_bounds"] = False
        out["qc_swapped"] = False
    else:
        out["qc_in_bounds"] = [bbox_check(la, lo, bbox) for la, lo in zip(out["latitud"], out["longitud"])]
        out["qc_swapped"] = [detect_swapped(la, lo, bbox) for la, lo in zip(out["latitud"], out["longitud"])]

    # --- 2b. Territory scope classification (ADM0 polygon containment) ---
    adm0_polys = boundaries_by_level.get(0)
    country_geom = None
    if adm0_polys is not None and not adm0_polys.empty:
        adm0_code = ADM0_BOUNDARY_PCODE_MAP.get(iso, iso)
        country_polys = adm0_polys[adm0_polys["ADM0_PCODE"] == adm0_code] if "ADM0_PCODE" in adm0_polys.columns else adm0_polys
        if not country_polys.empty:
            try:
                country_geom = country_polys.geometry.union_all()
            except AttributeError:
                country_geom = country_polys.unary_union
    out["qc_scope_class"] = [_classify_scope_row(row, iso=iso, country_geom=country_geom) for _, row in out.iterrows()]
    invalid_scope = set(out["qc_scope_class"].dropna().astype(str)) - set(QC_SCOPE_CLASS_VALUES)
    if invalid_scope:
        raise ValueError(f"{iso}: invalid qc_scope_class values {sorted(invalid_scope)}")

    # --- 3. Spatial joins (ADM1 + ADM2) — only for in-bounds, geo-able rows ---
    has_coords = out["latitud"].notna() & out["longitud"].notna()
    in_bounds_geo = has_coords & out["qc_in_bounds"]

    for level in (1, 2):
        polys = boundaries_by_level.get(level)
        pcode_col = f"adm{level}_pcode"
        polygon_name_col = f"polygon_adm{level}"
        polygon_pcode_col = f"polygon_adm{level}_pcode"
        polygon_norm_col = f"polygon_adm{level}_norm"

        # Default columns
        out[pcode_col] = ""
        out[polygon_name_col] = ""
        out[polygon_pcode_col] = ""
        out[polygon_norm_col] = ""

        if polys is None or polys.empty:
            continue

        country_polys = polys[polys["ADM0_PCODE"] == iso] if "ADM0_PCODE" in polys.columns else polys
        if country_polys.empty:
            continue

        in_bounds_subset = out.loc[in_bounds_geo, ["id_centro", "latitud", "longitud"]]
        if in_bounds_subset.empty:
            continue
        joined = spatial_join_adm(in_bounds_subset, country_polys, level=level)
        if joined.empty:
            continue
        joined = joined.rename(columns={
            f"polygon_adm{level}": polygon_name_col,
            f"polygon_adm{level}_pcode": polygon_pcode_col,
            f"polygon_adm{level}_norm": polygon_norm_col,
        })
        # Merge — only update for matched ids
        out = out.drop(columns=[polygon_name_col, polygon_pcode_col, polygon_norm_col])
        out = out.merge(joined[["id_centro", polygon_name_col,
                                polygon_pcode_col, polygon_norm_col]],
                        on="id_centro", how="left")
        out[polygon_name_col] = out[polygon_name_col].fillna("")
        out[polygon_pcode_col] = out[polygon_pcode_col].fillna("")
        out[polygon_norm_col] = out[polygon_norm_col].fillna("")
        out[pcode_col] = out[polygon_pcode_col]

    # --- 4. ADM status per level (using addr_df for raw names) ---
    if addr_df is not None and not addr_df.empty:
        addr_cols = [
            c
            for c in (
                "id_centro", "raw_adm1", "raw_adm2",
                "qc_raw_adm1", "qc_raw_adm2",
                "raw_adm1_code", "raw_adm2_code",
            )
            if c in addr_df.columns
        ]
        addr_local = addr_df[addr_cols].copy()
        addr_local["id_centro"] = addr_local["id_centro"].astype(str)
        # Drop overlapping columns from `out` before merging so re-runs on a
        # previously enriched CIMA (e.g. SUR carrying raw_adm1_code) don't
        # leave _x/_y suffixes behind.
        overlap = [c for c in addr_cols if c != "id_centro" and c in out.columns]
        if overlap:
            out = out.drop(columns=overlap)
        out = out.merge(addr_local, on="id_centro", how="left")
    if "raw_adm1" not in out.columns:
        out["raw_adm1"] = ""
    if "raw_adm2" not in out.columns:
        out["raw_adm2"] = ""
    out["raw_adm1"] = out["raw_adm1"].fillna("")
    out["raw_adm2"] = out["raw_adm2"].fillna("")
    if "qc_raw_adm1" not in out.columns:
        out["qc_raw_adm1"] = out["raw_adm1"]
    if "qc_raw_adm2" not in out.columns:
        out["qc_raw_adm2"] = out["raw_adm2"]
    out["qc_raw_adm1"] = out["qc_raw_adm1"].fillna("")
    out["qc_raw_adm2"] = out["qc_raw_adm2"].fillna("")
    if "raw_adm1_code" not in out.columns:
        out["raw_adm1_code"] = ""
    out["raw_adm1_code"] = out["raw_adm1_code"].fillna("")
    if "raw_adm2_code" not in out.columns:
        out["raw_adm2_code"] = ""
    out["raw_adm2_code"] = out["raw_adm2_code"].fillna("")

    # --- 3.5. Near-border polygon rescue ---
    # Schools whose coords are inside the country bbox but the spatial join
    # found NO_POLYGON (BID polygon edge is slightly off from the school's
    # GPS) get one chance to recover their admin assignment. If the ministry
    # declared a valid admin code AND that polygon is among the 2 nearest to
    # the school, trust it. Conservative: requires both raw declaration AND
    # geographic proximity to agree.
    for level in (1, 2):
        polys = boundaries_by_level.get(level)
        if polys is None or polys.empty:
            continue
        country_polys = polys[polys["ADM0_PCODE"] == iso] if "ADM0_PCODE" in polys.columns else polys
        if country_polys.empty:
            continue
        rescue_near_border_polygons(
            out, country_polys, level=level,
            polygon_name_col=f"polygon_adm{level}",
            polygon_pcode_col=f"polygon_adm{level}_pcode",
            polygon_norm_col=f"polygon_adm{level}_norm",
            pcode_col=f"adm{level}_pcode", k=2,
        )
        # Second-pass rescue: countries without numeric raw admin codes (BHS)
        # still benefit from snap-to-nearest when (a) the school is within
        # tolerance_km of any polygon and (b) the raw admin NAME matches the
        # nearest polygon via ADM_ALIASES/ADM_AGGREGATIONS. Idempotent vs. the
        # code-based rescue above (operates only on rows still NO_POLYGON).
        rescue_near_border_polygons_by_name(
            out, country_polys, level=level, iso=iso,
            polygon_name_col=f"polygon_adm{level}",
            polygon_pcode_col=f"polygon_adm{level}_pcode",
            polygon_norm_col=f"polygon_adm{level}_norm",
            pcode_col=f"adm{level}_pcode", tolerance_km=1.0,
        )

    def _status_for_level(row, level: int) -> str:
        if not bool(row["qc_in_bounds"]):
            return "NO_DATA" if not has_coords.loc[row.name] else "NOT_RUN"
        if not has_coords.loc[row.name]:
            return "NO_DATA"
        polygon_name = row[f"polygon_adm{level}"]
        raw_name = row[f"qc_raw_adm{level}"]
        if not polygon_name:
            return "NO_POLYGON"
        # Code-based path (preferred — deterministic, encoding-safe). Available
        # at level 1 for ARG/BRA/ECU/MEX/CHL and at level 2 for ECU. Falls
        # through to name-based admin_match if codes aren't populated for this row.
        raw_code = row.get(f"raw_adm{level}_code", "")
        poly_code = row.get(f"polygon_adm{level}_pcode", "")
        if raw_code and poly_code:
            return "MATCH" if raw_code == poly_code else "MISMATCH"
        return admin_match(raw_name, polygon_name, iso, level=level)

    # Mark levels that aren't part of this country's policy as NOT_RUN
    if final_level == "bbox_only":
        out["qc_adm1_status"] = "NOT_RUN"
        out["qc_adm2_status"] = "NOT_RUN"
    elif final_level == "spatial_only":
        # Only spatial containment matters — use NO_RAW_ADM if raw missing,
        # but treat polygon containment as MATCH (no name compare).
        out["qc_adm1_status"] = [
            ("MATCH" if r["polygon_adm1"] else
             ("NO_DATA" if not has_coords.loc[r.name] else "NO_POLYGON"))
            for _, r in out.iterrows()
        ]
        out["qc_adm2_status"] = "NOT_RUN"
    else:
        # Always evaluate BOTH ADM levels when raw data is available. This is
        # purely additive evidence for the dashboard — `coordinate_quality`
        # resolution still respects `final_match_level` (the resolver in
        # resolve_coordinate_quality consults adm1_status only for adm1
        # countries and adm2_status only for adm2 countries; the extra
        # qc_adm2_status for adm1 countries is for the matrix display, not
        # for canonical labeling).
        # For countries that don't validate ADM2 against BID polygons
        # (BLZ/GUY: raw has no adm2 col; BOL/URY: qc_adm2_col=None opt-out
        # because the raw adm2 doesn't align to BID's polygons by name),
        # `qc_raw_adm2` is empty and admin_match returns NO_RAW_ADM — the
        # matrix renders "—" correctly.
        out["qc_adm1_status"] = [_status_for_level(r, 1) for _, r in out.iterrows()]
        out["qc_adm2_status"] = [_status_for_level(r, 2) for _, r in out.iterrows()]

    # qc_match_level: which level the resolver actually used
    if final_level == "adm2":
        out["qc_match_level"] = "ADM2"
    elif final_level == "adm1":
        out["qc_match_level"] = "ADM1"
    elif final_level == "spatial_only":
        out["qc_match_level"] = "SPATIAL_ONLY"
    else:
        out["qc_match_level"] = "NONE"

    # --- 4b. Distance to raw-declared polygon edge (boundary_zone evidence) ---
    # Only computed for rows with a MISMATCH at the level the resolver uses.
    # Used by `resolve_coordinate_quality` to soften adm_mismatch into
    # boundary_zone for schools sitting < 5 km from the raw polygon's exterior.
    out["qc_distance_to_raw_polygon_km"] = pd.NA
    if final_level in ("adm1", "adm2"):
        level_for_distance = 2 if final_level == "adm2" else 1
        polys = boundaries_by_level.get(level_for_distance)
        status_col = f"qc_adm{level_for_distance}_status"
        raw_name_col = f"qc_raw_adm{level_for_distance}"
        if (
            polys is not None
            and not polys.empty
            and status_col in out.columns
            and raw_name_col in out.columns
        ):
            country_polys = (
                polys[polys["ADM0_PCODE"] == iso] if "ADM0_PCODE" in polys.columns
                else polys
            )
            if not country_polys.empty:
                mismatch_mask = (
                    (out[status_col] == "MISMATCH")
                    & out["latitud"].notna()
                    & out["longitud"].notna()
                )
                if mismatch_mask.any():
                    distances = _distance_to_raw_polygon_km(
                        out.loc[mismatch_mask],
                        country_polys,
                        raw_name_col=raw_name_col,
                        level=level_for_distance,
                        iso=iso,
                    )
                    out.loc[mismatch_mask, "qc_distance_to_raw_polygon_km"] = distances

    # --- 5. Cluster evidence ---
    out["qc_cluster_size_exact"] = detect_clusters_exact(out).values
    out["qc_cluster_diff_addr_exact"] = has_diff_address_in_cluster(out, addr_df).values
    out["qc_cluster_size_50m"] = detect_clusters_radius(out).values
    # 50m diff-address is an evidence-only column; v1 leaves it as NA placeholder.
    out["qc_cluster_diff_addr_50m"] = pd.NA
    # Extended cluster_centroid signals (consumed by resolve_coordinate_quality
    # for the n in [2,4] placeholder rule). Computed even when addr_df is None
    # — both helpers return all-False in that case, so the resolver simply
    # falls through to gps_validated for sub-5 clusters as before.
    out["qc_cluster_diff_admin_locality"] = (
        has_diff_admin_or_locality_in_cluster(out, addr_df).values
    )
    out["qc_n2_frontier_rescue"] = detect_n2_frontier_rescue(out).values

    # --- 6. Legacy migration of coordinate_source ---
    if "coordinate_source" not in out.columns:
        out["coordinate_source"] = ""
    if "qc_centroid_bias" not in out.columns:
        out["qc_centroid_bias"] = "unknown"

    migrated = out.apply(migrate_legacy_evidence, axis=1)
    out["coordinate_source"] = migrated.apply(lambda d: d["coordinate_source"])
    out["qc_centroid_bias"] = migrated.apply(lambda d: d["qc_centroid_bias"])

    # --- 7. Resolve final coordinate_quality + reason ---
    qualities, reasons = [], []
    for _, row in out.iterrows():
        ev = _evidence_for_row(row, scope)
        q, r = resolve_coordinate_quality(ev, scope)
        qualities.append(q)
        reasons.append(r)
    out["coordinate_quality"] = qualities
    out["coordinate_quality_reason"] = reasons
    final_level = scope.get("final_match_level", "adm2")
    out["include_in_spatial_indicators"] = pd.array(
        [_spatial_indicator_policy(row, final_match_level=final_level) for _, row in out.iterrows()],
        dtype="boolean",
    )
    out["qc_evidence_version"] = 2

    # --- 8. Drop transient join columns; keep only the contracted outputs ---
    drop = [c for c in ("polygon_adm1", "polygon_adm1_pcode", "polygon_adm1_norm",
                        "polygon_adm2", "polygon_adm2_pcode", "polygon_adm2_norm",
                        "raw_adm1", "raw_adm2", "qc_raw_adm1", "qc_raw_adm2") if c in out.columns]
    out = out.drop(columns=drop)

    # Materialize the full canonical finalize schema even for countries that
    # never ran Step 05 compare/fill. Missing audit columns stay blank rather
    # than disappearing, so all `{ISO}_total_cima.csv` files share one shape.
    for col in CIMA_ENRICHED_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    out = out.loc[:, list(CIMA_ENRICHED_COLUMNS)]

    return out


__all__ = [
    # constants re-exported for convenience
    "GEOCODER_DISAGREE_DISTANCE_KM",
    "CLUSTER_THRESHOLD",
    "CLUSTER_RADIUS_50M_DEG",
    "FINALIZE_OUTPUT_COLUMNS",
    # pure helpers
    "normalize_name",
    "dms_to_dd",
    "is_blank",
    "bbox_check",
    "detect_swapped",
    "admin_match",
    # cluster detection
    "detect_clusters_exact",
    "detect_clusters_radius",
    "has_diff_address_in_cluster",
    # migration + resolver
    "migrate_legacy_evidence",
    "resolve_coordinate_quality",
    # target discovery
    "compute_geocode_targets",
    # finalize orchestrator
    "finalize_cima_evidence",
    # I/O
    "load_boundaries",
    "spatial_join_adm",
    "rescue_near_border_polygons",
    "rescue_near_border_polygons_by_name",
    "has_diff_admin_or_locality_in_cluster",
    "detect_n2_frontier_rescue",
]

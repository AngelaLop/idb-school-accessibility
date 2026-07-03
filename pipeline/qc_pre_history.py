"""Manifest of historical pre-cleaning events for the dashboard QC matrix.

Each entry documents a correction applied during Step 01 (`01_build_cima.py`)
that wiped a quality signal from the post-CIMA. Without this manifest, the
dashboard's "pre-process" toggle would show 0 for tests 1/2/4 in every
country — losing the story of what the pipeline actually caught and fixed.

This is the **Camino B (manifest)** approach: hardcoded, narrative-driven,
low-maintenance. The auto-generated alternative — hooks inside each
`process_{ISO}` that emit raw signals to a sidecar CSV — is documented as
deferred work in `docs/step03_audit_2026-05.md` and will land when the
broader reproducibility plan kicks in.

Schema:
    QC_PRE_HISTORY[iso] = {
        "test1_utm_pre_n":      int,  # rows in UTM/projected coords (Step 01 reprojected)
        "test2_zero_zero_pre_n": int,  # rows with lat==0 AND lon==0 (Step 01 wiped to NaN)
        "test4_swapped_pre_n":   int,  # rows with lat/lon columns swapped (Step 01 swapped back)
        "_source": str,                # short narrative — where this number came from
    }

Only countries with a documented historical event have an entry. Countries
not in the dict implicitly have all three values = 0 (i.e., the test passed
in pre too — no correction was needed).

Sector breakdown: not tracked here. Per-row tracking of which sector each
affected school belongs to is part of the deferred auto-generated path.
For pre mode, sector cells (Public/Private) show null for these three tests
when sector ≠ Total.
"""

from __future__ import annotations

from typing import Any

QC_PRE_HISTORY: dict[str, dict[str, Any]] = {
    "COL": {
        "test2_zero_zero_pre_n": 13,
        "_source": (
            "Step 01 process_COL drops rows with lat==0 AND lon==0 from the "
            "DANE Características file before merge. Count of 13 verified in "
            "docs/step03_audit_2026-05.md §2 Test 2."
        ),
    },
    "URY": {
        "test1_utm_pre_n": 2395,
        "_source": (
            "ANEP CEIP/CES/CETP shapefiles ship in EPSG:3857 (Web Mercator, "
            "meters). Reprojected to EPSG:4326 in pipeline/01_build_cima.py "
            "(process_URY at lines 1879-1889 and process_URY_fallback at "
            "line 1992). Count = current URY CIMA total (every URY school's "
            "raw coord came in non-WGS84 meters and was reprojected). The "
            "manifest key is named 'utm' for legacy reasons; the test name on "
            "the dashboard is 'Proyección WGS84' which captures both UTM and "
            "Web Mercator cases."
        ),
    },
    "ECU": {
        "test1_utm_pre_n": 14938,
        "_source": (
            "MINEDUC GeoJSON sources mixed projection — some files in EPSG:32717 "
            "(UTM zone 17S). Step 01 process_ECU reprojects to WGS84 before "
            "writing CIMA. Count = current ECU CIMA total (conservative: all "
            "rows could have been UTM pre-reproject)."
        ),
    },
    "GUY": {
        "test4_swapped_pre_n": 503,
        "_source": (
            "School Data-Mapping file ships with `Latitude` and `Longitude` "
            "columns SWAPPED relative to what the headers claim. Step 01 "
            "process_GUY swaps them. Count = current GUY CIMA total."
        ),
    },
    "PRY": {
        "test4_swapped_pre_n": 7628,
        "_source": (
            "MEC Establecimientos file ships with lat/lon columns swapped. "
            "Step 01 process_PRY swaps them. Count = current PRY CIMA total."
        ),
    },
}


def get(iso: str, key: str) -> int:
    """Return the historical count for a (iso, test) pair; 0 when absent.

    `key` ∈ {test1_utm_pre_n, test2_zero_zero_pre_n, test4_swapped_pre_n}.
    """
    return int(QC_PRE_HISTORY.get(iso, {}).get(key, 0))

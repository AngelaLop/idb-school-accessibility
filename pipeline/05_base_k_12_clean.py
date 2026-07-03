"""Resolve id_edificio against BID reference data and finalize the 14-col school base.

Step 05 is the sole producer of `{ISO}_schools_clean.csv`. It reads each
country's CIMA enriched file directly (47 cols), projects to the canonical
13 logical columns plus `qc_scope_class`, joins each school to its
`id_edificio` through a country-aware cascade, validates the result against
the cluster-proxy columns from CIMA (used in-memory only), and writes the
canonical 14-col schema. The proxy columns
(`qc_cluster_size_exact`, `qc_cluster_diff_addr_exact`) never reach disk —
they survive only inside the audit trail.

Match cascade per school:
    A. {ISO}_total.csv on normalized id_centro
    B. LAC_merged.csv (same country) on normalized id_centro
    C. LAC_merged.csv (same country) on (lat, lon) rounded to 5 decimals
    D. Synthetic id_edificio = "{ISO}_SYN_{N:05d}" for unmatched schools

Outputs:
    - data/schools/AR/{ISO}/processed/{ISO}_schools_clean.csv  (14 cols, in place)
    - results/QC/id_edificio_audit.csv                         (per-school audit trail)
    - results/QC/id_edificio_validation.csv                    (per-country validation summary)

Run from project root:
    uv run python pipeline/05_base_k_12_clean.py --countries all
    uv run python pipeline/05_base_k_12_clean.py --countries MEX,COL --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd

from _paths import REPO_ROOT as ROOT, DATA_ROOT, RESULTS_ROOT  # noqa: E402
sys.path.insert(0, str(ROOT))

from pipeline.constants import ANALYSIS_ISOS  # noqa: E402

CIMA_DIR = DATA_ROOT / "schools" / "AR"
LAC_MERGED_PATH = CIMA_DIR / "LAC_merged.csv"
QC_DIR = RESULTS_ROOT / "QC"

# 13 logical columns projected from CIMA, plus scope_class. proxies are
# loaded for in-memory validation only and never persisted.
CIMA_PROJECTION: tuple[str, ...] = (
    "adm0_pcode", "adm1_pcode", "adm2_pcode",
    "id_centro", "sector",
    "nivel_primaria", "nivel_secbaja", "nivel_secalta",
    "latitud", "longitud",
    "coordinate_source", "coordinate_quality", "qc_scope_class",
)
PROXY_COLUMNS: tuple[str, ...] = (
    "qc_cluster_size_exact", "qc_cluster_diff_addr_exact",
)

# 14-col canonical schema (id_edificio added after id_centro, proxies dropped).
FINAL_COLUMNS: tuple[str, ...] = (
    "adm0_pcode", "adm1_pcode", "adm2_pcode",
    "id_centro", "id_edificio", "sector",
    "nivel_primaria", "nivel_secbaja", "nivel_secalta",
    "latitud", "longitud",
    "coordinate_source", "coordinate_quality", "qc_scope_class",
)

# Validation summary threshold: surfaced in results/QC/id_edificio_validation.csv.
# Output schema is unconditionally 14 cols regardless — the proxies are
# dropped from disk because the user has accepted that diff_addr=True drift
# in MEX/ARG/BRA/etc reflects sloppy address strings on legitimate multi-shift
# buildings, not covert centroids. COL/PRY size-mismatch buildings get
# bid_match_suspect=True in the audit instead.
VALIDATION_THRESHOLD = 0.85

# BRA's _total.csv has 134K rows but only 2 unique id_edificio values — broken.
# Skip the file-A merge for BRA and let the LAC_merged cascade carry it.
SKIP_FILE_A: frozenset[str] = frozenset({"BRA"})


def normalize_id(s: pd.Series) -> pd.Series:
    """Strip whitespace and trailing `.0` (float-cast artifacts) from id_centro."""
    return s.fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def round_coords(df: pd.DataFrame, lat_col: str, lon_col: str, decimals: int = 5) -> pd.DataFrame:
    """Add `_lat_r5` / `_lon_r5` numeric columns rounded to `decimals` places."""
    df = df.copy()
    df["_lat_r5"] = pd.to_numeric(df[lat_col], errors="coerce").round(decimals)
    df["_lon_r5"] = pd.to_numeric(df[lon_col], errors="coerce").round(decimals)
    return df


def build_lookup_from_id(
    ref: pd.DataFrame, id_col: str, edif_col: str = "id_edificio"
) -> dict[str, str]:
    """Collapse a reference frame to {normalized id_centro: id_edificio}."""
    sub = ref[[id_col, edif_col]].dropna(subset=[edif_col]).copy()
    sub["_id_norm"] = normalize_id(sub[id_col])
    sub = sub[sub["_id_norm"] != ""]
    sub = sub.drop_duplicates(subset="_id_norm", keep="first")
    return dict(zip(sub["_id_norm"], sub[edif_col].astype(str)))


def build_lookup_from_coords(
    ref: pd.DataFrame, lat_col: str, lon_col: str, edif_col: str = "id_edificio"
) -> dict[tuple[float, float], str]:
    """Collapse a reference frame to {(lat_r5, lon_r5): id_edificio}.

    Schools where the rounded (lat, lon) collides across different id_edificio
    values are dropped from the lookup — we cannot disambiguate.
    """
    ref = round_coords(ref, lat_col, lon_col)
    sub = ref.dropna(subset=["_lat_r5", "_lon_r5", edif_col])
    grouped = sub.groupby(["_lat_r5", "_lon_r5"])[edif_col].nunique()
    unique_keys = grouped[grouped == 1].index
    sub = sub.set_index(["_lat_r5", "_lon_r5"]).loc[unique_keys]
    sub = sub[~sub.index.duplicated(keep="first")]
    return {(lat, lon): str(eid) for (lat, lon), eid in sub[edif_col].items()}


def resolve_id_edificio_for_country(
    iso: str, clean: pd.DataFrame, lac: pd.DataFrame
) -> pd.DataFrame:
    """Apply the A → B → C → synthetic cascade and return clean+id_edificio+match_path."""
    out = clean.copy()
    out["_id_norm"] = normalize_id(out["id_centro"])
    out = round_coords(out, "latitud", "longitud")

    id_edificio = pd.Series([pd.NA] * len(out), index=out.index, dtype=object)
    match_path = pd.Series(["unmatched"] * len(out), index=out.index, dtype=object)

    # --- Cascade A: {ISO}_total.csv direct merge -----------------------------
    if iso not in SKIP_FILE_A:
        tot_path = CIMA_DIR / iso / "processed" / f"{iso}_total.csv"
        if tot_path.exists():
            tot = pd.read_csv(tot_path, dtype=str, low_memory=False)
            if "id_edificio" in tot.columns and tot["id_edificio"].notna().sum() > 0:
                lookup_a = build_lookup_from_id(tot, "id_centro")
                hit_mask = id_edificio.isna() & out["_id_norm"].isin(lookup_a)
                id_edificio.loc[hit_mask] = out.loc[hit_mask, "_id_norm"].map(lookup_a)
                match_path.loc[hit_mask] = "A_total"

    # --- Cascade B: LAC_merged.csv (same ISO) on id_centro -------------------
    lac_iso = lac[lac["adm0_pcode"] == iso]
    if not lac_iso.empty:
        lookup_b = build_lookup_from_id(lac_iso, "id_centro")
        hit_mask = id_edificio.isna() & out["_id_norm"].isin(lookup_b)
        id_edificio.loc[hit_mask] = out.loc[hit_mask, "_id_norm"].map(lookup_b)
        match_path.loc[hit_mask] = "B_lac_id"

        # --- Cascade C: LAC_merged.csv (same ISO) on rounded (lat, lon) ------
        lookup_c = build_lookup_from_coords(lac_iso, "lat", "lon")
        if lookup_c:
            unmatched = id_edificio.isna() & out["_lat_r5"].notna() & out["_lon_r5"].notna()
            keys = list(zip(out.loc[unmatched, "_lat_r5"], out.loc[unmatched, "_lon_r5"]))
            mapped = pd.Series([lookup_c.get(k) for k in keys], index=out.loc[unmatched].index)
            hit_idx = mapped.dropna().index
            id_edificio.loc[hit_idx] = mapped.loc[hit_idx]
            match_path.loc[hit_idx] = "C_lac_coords"

    # --- Synthetic for the rest ---------------------------------------------
    syn_mask = id_edificio.isna()
    syn_count = int(syn_mask.sum())
    syn_ids = [f"{iso}_SYN_{i:05d}" for i in range(1, syn_count + 1)]
    id_edificio.loc[syn_mask] = syn_ids
    match_path.loc[syn_mask] = "D_synthetic"

    out["id_edificio"] = id_edificio.astype(str)
    out["match_path"] = match_path.astype(str)
    return out.drop(columns=["_id_norm", "_lat_r5", "_lon_r5"])


def validate_proxy_coherence(df: pd.DataFrame) -> dict[str, float | int]:
    """Check whether real (non-synthetic) id_edificio values agree with cluster proxies.

    For every id_edificio that appears N>1 times AND is a real BID id (not
    synthetic), confirm that all N rows have cluster_size>1 AND diff_addr==False.
    Returns counts and a coherence ratio in [0, 1].
    """
    real_mask = ~df["match_path"].eq("D_synthetic")
    real = df[real_mask].copy()
    real["_size"] = pd.to_numeric(real["qc_cluster_size_exact"], errors="coerce").fillna(0)
    real["_diff"] = real["qc_cluster_diff_addr_exact"].astype(str).str.lower().isin(
        {"true", "1", "1.0"}
    )
    multi = real.groupby("id_edificio").filter(lambda g: len(g) > 1)
    if multi.empty:
        return {
            "real_buildings_multi": 0, "coherent_buildings": 0,
            "incoherent_size": 0, "incoherent_diff_addr": 0, "coherence": 1.0,
        }
    grp = multi.groupby("id_edificio")
    sizes_ok = grp["_size"].apply(lambda s: (s > 1).all())
    diff_ok = grp["_diff"].apply(lambda s: (~s).all())
    coherent = (sizes_ok & diff_ok).sum()
    incoherent_size = (~sizes_ok).sum()
    incoherent_diff = (sizes_ok & ~diff_ok).sum()
    total = len(grp)
    return {
        "real_buildings_multi": int(total),
        "coherent_buildings": int(coherent),
        "incoherent_size": int(incoherent_size),
        "incoherent_diff_addr": int(incoherent_diff),
        "coherence": float(coherent / total) if total else 1.0,
    }


def process_country(iso: str, lac: pd.DataFrame, dry_run: bool) -> dict:
    cima_path = CIMA_DIR / iso / "processed" / f"{iso}_total_cima.csv"
    if not cima_path.exists():
        return {"iso": iso, "status": "missing", "rows": 0}
    cima = pd.read_csv(
        cima_path,
        dtype={"id_centro": str, "qc_cluster_diff_addr_exact": str},
        usecols=list(CIMA_PROJECTION) + list(PROXY_COLUMNS),
        low_memory=False,
    )
    enriched = resolve_id_edificio_for_country(iso, cima, lac)
    clean_path = CIMA_DIR / iso / "processed" / f"{iso}_schools_clean.csv"
    diag = validate_proxy_coherence(enriched)

    path_counts = enriched["match_path"].value_counts().to_dict()
    summary = {
        "iso": iso,
        "rows": len(enriched),
        "A_total": int(path_counts.get("A_total", 0)),
        "B_lac_id": int(path_counts.get("B_lac_id", 0)),
        "C_lac_coords": int(path_counts.get("C_lac_coords", 0)),
        "D_synthetic": int(path_counts.get("D_synthetic", 0)),
        **diag,
    }

    summary["meets_threshold"] = bool(
        diag["coherence"] >= VALIDATION_THRESHOLD or diag["real_buildings_multi"] == 0
    )

    final = enriched[list(FINAL_COLUMNS)].copy()

    enriched["_size"] = pd.to_numeric(
        enriched["qc_cluster_size_exact"], errors="coerce"
    ).fillna(0)
    real_mask = ~enriched["match_path"].eq("D_synthetic")
    suspect_buildings = (
        enriched[real_mask]
        .groupby("id_edificio")
        .filter(lambda g: len(g) > 1 and (g["_size"] <= 1).any())["id_edificio"]
        .unique()
    )

    audit = enriched[[
        "adm0_pcode", "id_centro", "id_edificio", "match_path",
        "qc_cluster_size_exact", "qc_cluster_diff_addr_exact",
    ]].copy()
    audit["bid_match"] = audit["match_path"].ne("D_synthetic")
    audit["bid_match_suspect"] = audit["id_edificio"].isin(suspect_buildings)
    summary["bid_match_suspect_rows"] = int(audit["bid_match_suspect"].sum())

    if not dry_run:
        final.to_csv(clean_path, index=False)
        audit_path = QC_DIR / "id_edificio_audit.csv"
        QC_DIR.mkdir(parents=True, exist_ok=True)
        if audit_path.exists() and iso != ANALYSIS_ISOS[0]:
            existing = pd.read_csv(audit_path, dtype=str, low_memory=False)
            existing = existing[existing["adm0_pcode"] != iso]
            audit = pd.concat([existing, audit], ignore_index=True)
        audit.to_csv(audit_path, index=False)

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--countries", default="all",
                        help='Comma-separated ISO list, or "all" (default).')
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute and report without writing CSVs.")
    args = parser.parse_args(argv)

    isos: Iterable[str] = (
        ANALYSIS_ISOS if args.countries == "all"
        else [c.strip().upper() for c in args.countries.split(",") if c.strip()]
    )

    print(f"[step-05] Loading LAC_merged.csv ...", flush=True)
    lac = pd.read_csv(LAC_MERGED_PATH, dtype=str, low_memory=False)
    print(f"[step-05] LAC_merged: {len(lac):,} rows, {lac['adm0_pcode'].nunique()} ISOs",
          flush=True)

    summaries: list[dict] = []
    for iso in isos:
        try:
            s = process_country(iso, lac, dry_run=args.dry_run)
            summaries.append(s)
            print(
                f"  [{iso}] rows={s['rows']:>6}  "
                f"A={s.get('A_total',0):>6}  B={s.get('B_lac_id',0):>5}  "
                f"C={s.get('C_lac_coords',0):>4}  SYN={s.get('D_synthetic',0):>5}  "
                f"coherence={s.get('coherence',1.0):.3f}  "
                f"suspect={s.get('bid_match_suspect_rows', 0)}",
                flush=True,
            )
        except Exception as exc:
            summaries.append({"iso": iso, "status": "error", "error": str(exc)})
            print(f"  [{iso}] ERROR: {exc}", flush=True)

    summary_df = pd.DataFrame(summaries)
    if not args.dry_run:
        QC_DIR.mkdir(parents=True, exist_ok=True)
        summary_df.to_csv(QC_DIR / "id_edificio_validation.csv", index=False)

    total_rows = int(summary_df.get("rows", pd.Series(dtype=int)).fillna(0).sum())
    syn_total = int(summary_df.get("D_synthetic", pd.Series(dtype=int)).fillna(0).sum())
    print(
        f"\n[step-05] Done. {total_rows:,} schools across {len(summary_df)} countries. "
        f"Synthetic id_edificio: {syn_total:,}.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

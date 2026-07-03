"""
OSRM walking-time matrix for one country × one education level.

For each WorldPop 1km cell in the country, finds the K nearest eligible
schools and queries the local OSRM server for the network walking times.
Records the minimum (= time to nearest reachable school).

Usage
-----
    docker start osrm-pan       # ensure OSRM server is up on localhost:5000
    uv run python pipeline/09b_travel_time_osrm.py --country PAN --level primaria --mode walking

Output
------
    results/osrm/{ISO}_{mode}_{level}[_{sector}]_osrm.parquet  with columns:
        cell_id, lat, lon, pop_total, pop_5_9, pop_10_14, pop_15_19,
        area_class, ADM1_PCODE, ADM2_PCODE, rwi,
        n_schools_in_range, nearest_school_id, time_to_nearest_min

    sector="total" (all schools) keeps the unsuffixed name; public / private
    get a _{sector} suffix. The published SCL Total is derived in Step 10b as
    the cell-wise min of the public and private matrices (mirrors Step 09/10
    FMM), so production runs query public + private, not total.

Networking
----------
The OSRM server speaks HTTP/1.1 with keep-alive. We hold ONE persistent
connection per worker thread (thread-local `http.client`) and reuse it for
every request — so the whole run uses only `max_workers` TCP connections,
not one-per-request. The previous urllib-per-call version exhausted the
Windows ephemeral-port pool after ~17.8k requests, which silently turned
every subsequent cell into NaN (the "38.5%-reachable" bug, 2026-05-14).

A NaN in `time_to_nearest_min` therefore means *genuinely unreachable*
(OSRM routed and found no path). Transport failures are retried, counted,
and — if any survive a final sequential retry — abort the run loudly rather
than be written as misleading NaNs.

Caveats
-------
- OSRM "foot" profile uses default OSM walking speed (≈5 km/h).
- Schools are filtered to include_in_spatial_indicators == True (same gate
  as Step 09 FMM, so the two outputs are apples-to-apples).
- K (default 50) is the number of nearest schools (Euclidean) we ask OSRM
  about per cell. We take the MIN — if the truly-fastest school happens to
  rank 51st by Euclidean we miss it, but in practice K=50 covers >99.9%
  of cases for our resolution.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

log = logging.getLogger("osrm_matrix")

# ────────────────────────── config ────────────────────────────────────────────
from _paths import DATA_ROOT, RESULTS_ROOT  # noqa: E402

SCHOOLS_PATH = DATA_ROOT / "schools" / "AR" / "LAC_schools_k12_with_context.csv"
WORLDPOP_DIR = DATA_ROOT / "population" / "WorldPop" / "processed"
OUT_DIR = RESULTS_ROOT / "osrm"

OSRM_HOST = "localhost"
OSRM_PORT = 5000
OSRM_TIMEOUT = 30.0
OSRM_RETRIES = 4          # attempts per request before declaring a transport failure
OSRM_PROFILE_BY_MODE = {"walking": "foot", "motorized": "car"}

LEVEL_TO_COLUMN = {
    "primaria": "nivel_primaria",
    "secbaja": "nivel_secbaja",
    "secalta": "nivel_secalta",
}

# Sector → value in the `sector` column of LAC_schools_k12_with_context.csv.
# Mirrors Step 09 FMM exactly. "total" anchors on all K-12 schools (no sector
# filter); public / private restrict to that sector. Note: the SCL "Total"
# sector is derived in Step 10b as the cell-wise min of public and private —
# this "total" option exists only for ad-hoc all-schools runs and parity with
# FMM, and is NOT what the published Total column uses.
SECTOR_TO_VALUE = {
    "total": None,
    "public": "Public",
    "private": "Private",
}


# ────────────────────────── OSRM client ───────────────────────────────────────
# One persistent keep-alive connection per worker thread. Reused across all of
# that thread's requests, so the run uses `max_workers` TCP connections total.

class OSRMTransportError(RuntimeError):
    """Raised when an HTTP request to OSRM fails after all retries.

    Distinct from a routing 'no path' result: this is an infrastructure
    failure (connection refused/reset, timeout, non-200) and must NOT be
    silently recorded as an unreachable cell.
    """


_thread_local = threading.local()


def _get_conn() -> http.client.HTTPConnection:
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        conn = http.client.HTTPConnection(OSRM_HOST, OSRM_PORT, timeout=OSRM_TIMEOUT)
        _thread_local.conn = conn
    return conn


def _drop_conn() -> None:
    """Discard this thread's connection (e.g. after an error) so the next
    request opens a fresh one."""
    conn = getattr(_thread_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _thread_local.conn = None


def osrm_get(path: str) -> dict:
    """GET an OSRM endpoint over the thread's persistent connection.

    Retries transient transport failures with backoff. Raises
    OSRMTransportError if every attempt fails — the caller must treat that
    as a hard error, not as an unreachable cell.
    """
    last_exc: Exception | None = None
    for attempt in range(OSRM_RETRIES):
        try:
            conn = _get_conn()
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read()          # must fully read to keep the connection alive
            if resp.status != 200:
                raise OSRMTransportError(f"HTTP {resp.status} for {path[:80]}")
            return json.loads(body)
        except Exception as exc:        # ConnectionReset, timeout, BadStatusLine, etc.
            last_exc = exc
            _drop_conn()                # broken socket — force a fresh one next try
            time.sleep(0.25 * (attempt + 1))
    raise OSRMTransportError(
        f"OSRM request failed after {OSRM_RETRIES} attempts: {last_exc!r}"
    ) from last_exc


def osrm_table(profile: str, sources_xy: list[tuple[float, float]],
               dests_xy: list[tuple[float, float]]) -> np.ndarray:
    """
    Query the OSRM /table endpoint. Returns (N_src × N_dest) array of seconds.
    Pairs OSRM cannot route come back as NaN. Raises OSRMTransportError on an
    HTTP/transport failure or a non-Ok OSRM response code.
    """
    all_coords = sources_xy + dests_xy
    coord_str = ";".join(f"{lon},{lat}" for lon, lat in all_coords)
    src_idx = ";".join(str(i) for i in range(len(sources_xy)))
    dst_idx = ";".join(str(len(sources_xy) + i) for i in range(len(dests_xy)))
    path = (f"/table/v1/{profile}/{coord_str}"
            f"?sources={src_idx}&destinations={dst_idx}&annotations=duration")

    payload = osrm_get(path)
    if payload.get("code") != "Ok":
        # NoSegment / NoTable etc. — a structural problem with the coordinates,
        # not a transient transport blip. Surface it rather than mask it.
        raise OSRMTransportError(
            f"OSRM /table responded {payload.get('code')}: {payload.get('message')}"
        )
    # OSRM returns null where unreachable — numpy converts those to NaN.
    return np.asarray(payload["durations"], dtype=np.float64)


# ────────────────────────── main pipeline ─────────────────────────────────────

def _out_path(iso: str, mode: str, level: str, sector: str) -> Path:
    # sector="total" keeps the original unsuffixed name (backward compatible
    # with the pre-sector runs); public / private get an explicit suffix,
    # mirroring Step 09 FMM's raster naming.
    stem = (f"{iso}_{mode}_{level}" if sector == "total"
            else f"{iso}_{mode}_{level}_{sector}")
    return OUT_DIR / f"{stem}_osrm.parquet"


def run(iso: str, mode: str, level: str, sector: str, k: int, max_workers: int,
        overwrite: bool) -> Path:
    profile = OSRM_PROFILE_BY_MODE[mode]
    level_col = LEVEL_TO_COLUMN[level]

    out_path = _out_path(iso, mode, level, sector)
    if out_path.exists() and not overwrite:
        log.info("Output already exists, skipping: %s", out_path)
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Load WorldPop cells for this country
    pop_path = WORLDPOP_DIR / f"population_grid_{iso}.csv"
    pop = pd.read_csv(pop_path, usecols=[
        "cell_id", "lat", "lon", "pop_total",
        "pop_5_9", "pop_10_14", "pop_15_19",
        "area_class", "ADM1_PCODE", "ADM2_PCODE", "rwi",
    ])
    log.info("[%s] WorldPop cells: %d", iso, len(pop))

    # 2. Load eligible schools (same gate as Step 09 FMM)
    schools = pd.read_csv(SCHOOLS_PATH, low_memory=False)
    mask = (
        (schools["adm0_pcode"] == iso)
        & (schools[level_col] == 1)
        & schools["latitud"].notna()
        & schools["longitud"].notna()
        & (schools["include_in_spatial_indicators"] == True)  # noqa: E712
    )
    sector_value = SECTOR_TO_VALUE[sector]
    if sector_value is not None:
        mask &= (schools["sector"] == sector_value)
    sch = schools[mask].reset_index(drop=True)
    log.info("[%s/%s/%s] Eligible schools: %d", iso, level, sector, len(sch))
    if len(sch) == 0:
        # A sector with no georeferenced school is expected (e.g. HND is
        # public-only by design; CRI's private schools all lack coordinates).
        # Skip without writing — Step 10b's total = min(public, private) falls
        # back to the present sector. Do NOT raise: that would abort the shell
        # driver's set -e loop mid-country.
        log.warning("[%s/%s/%s] no eligible schools — skipping (no parquet written)",
                    iso, level, sector)
        return out_path

    # 3. K-nearest schools per cell using Euclidean (deg) — proxy for "close"
    k_use = min(k, len(sch))
    tree = cKDTree(sch[["latitud", "longitud"]].values)
    log.info("[%s] Querying %d cells × %d nearest schools via OSRM /%s ...",
             iso, len(pop), k_use, profile)

    pop_arr = pop[["lat", "lon"]].values
    _, idx_matrix = tree.query(pop_arr, k=k_use)  # (N_cells, k)
    if k_use == 1:
        # cKDTree.query(k=1) returns 1-D arrays, so idx_matrix[i] would be a bare
        # scalar (not iterable). Normalize to (N_cells, 1). This happens when a
        # sector has a single eligible school in the country (e.g. GTM private).
        idx_matrix = idx_matrix.reshape(-1, 1)

    school_lons = sch["longitud"].to_numpy()
    school_lats = sch["latitud"].to_numpy()
    school_ids = sch["id_centro"].astype(str).to_numpy()

    # 4. Query OSRM, one origin at a time, parallel via threads
    n_cells = len(pop)
    times_min = np.full(n_cells, np.nan, dtype=np.float32)
    nearest_id = np.full(n_cells, "", dtype=object)
    n_in_range = np.zeros(n_cells, dtype=np.int32)

    def query_cell(i: int) -> tuple[float, str, int]:
        """One origin × K schools. Returns (min_minutes, school_id, n_finite).
        Propagates OSRMTransportError — callers decide how to handle it."""
        origin = (float(pop_arr[i, 1]), float(pop_arr[i, 0]))
        dest_idx = idx_matrix[i]
        dests = [(school_lons[j], school_lats[j]) for j in dest_idx]
        row = osrm_table(profile, [origin], dests)[0]
        finite_count = int(np.isfinite(row).sum())
        if finite_count == 0:
            return float("nan"), "", 0           # genuinely unreachable
        j_best = int(np.nanargmin(row))
        school_id = school_ids[int(dest_idx[j_best])]
        return float(row[j_best] / 60.0), str(school_id), finite_count

    def worker(i: int):
        try:
            t_min, sid, nr = query_cell(i)
            return i, t_min, sid, nr, None
        except OSRMTransportError as exc:
            return i, np.nan, "", 0, exc

    t0 = time.time()
    progress_every = max(1, n_cells // 20)
    transport_fails: list[int] = []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(worker, i) for i in range(n_cells)]
        for done_count, fut in enumerate(as_completed(futures), 1):
            i, t_min, sid, nr, exc = fut.result()
            if exc is not None:
                transport_fails.append(i)
            else:
                times_min[i] = t_min
                nearest_id[i] = sid
                n_in_range[i] = nr
            if done_count % progress_every == 0 or done_count == n_cells:
                elapsed = time.time() - t0
                rate = done_count / elapsed if elapsed > 0 else 0
                eta = (n_cells - done_count) / rate if rate > 0 else 0
                log.info("  %d/%d cells (%.1f%%)  rate=%.0f/s  eta=%.0fs  transport_fails=%d",
                         done_count, n_cells, done_count / n_cells * 100,
                         rate, eta, len(transport_fails))

    # 5. Final sequential retry for any transport failures — no concurrency
    #    pressure, so a survivor here is a real, persistent problem.
    if transport_fails:
        log.warning("[%s/%s] %d cells hit transport failures — retrying sequentially",
                    iso, level, len(transport_fails))
        still_failing: list[int] = []
        for i in transport_fails:
            try:
                t_min, sid, nr = query_cell(i)
                times_min[i] = t_min
                nearest_id[i] = sid
                n_in_range[i] = nr
            except OSRMTransportError:
                still_failing.append(i)
        if still_failing:
            raise RuntimeError(
                f"[{iso}/{level}] {len(still_failing)} cells still fail after a "
                f"sequential retry — OSRM server problem. NOT writing a partial "
                f"parquet. First few cell rows: {still_failing[:10]}"
            )
        log.info("[%s/%s] sequential retry recovered all %d cells",
                 iso, level, len(transport_fails))

    pop["n_schools_in_range"] = n_in_range
    pop["nearest_school_id"] = nearest_id
    pop["time_to_nearest_min"] = times_min
    pop.to_parquet(out_path, index=False)

    n_ok = int(np.isfinite(times_min).sum())
    log.info("[%s/%s/%s] Done. Reachable cells: %d / %d (%.1f%%)  → %s",
             iso, mode, level, n_ok, n_cells, n_ok / n_cells * 100, out_path)
    return out_path


def run_mode_all_combos(iso: str, mode: str, k: int, max_workers: int,
                        overwrite: bool) -> None:
    """Route every cell ONCE per mode and derive all six sector×level matrices.

    The per-combo `run()` routes each cell six times per mode (public/private ×
    3 levels), repeating OSRM's expensive origin search each time. Here we query
    each cell a single time against the UNION of its K-nearest schools across the
    active combos, then extract each combo's answer from that one response. The
    per-combo min is computed over exactly that combo's own K nearest (in the same
    Euclidean order), so the output is bit-identical to `run()` while doing ~6×
    fewer OSRM searches.

    Combos whose parquet already exists (without --overwrite) are skipped, so a
    resumed run only routes what is missing. Sectors with no georeferenced school
    are skipped silently (no parquet), mirroring `run()`.
    """
    profile = OSRM_PROFILE_BY_MODE[mode]

    pop_path = WORLDPOP_DIR / f"population_grid_{iso}.csv"
    pop = pd.read_csv(pop_path, usecols=[
        "cell_id", "lat", "lon", "pop_total",
        "pop_5_9", "pop_10_14", "pop_15_19",
        "area_class", "ADM1_PCODE", "ADM2_PCODE", "rwi",
    ])
    n_cells = len(pop)
    pop_arr = pop[["lat", "lon"]].values
    log.info("[%s/%s] WorldPop cells: %d", iso, mode, n_cells)

    schools = pd.read_csv(SCHOOLS_PATH, low_memory=False)

    # For each active combo, per-cell K-nearest schools mapped to one shared
    # ("global") school pool, so a single OSRM call per cell serves all combos.
    combos = [(s, l) for s in ("public", "private")
              for l in ("primaria", "secbaja", "secalta")]
    g_lon: list[float] = []
    g_lat: list[float] = []
    g_id: list[str] = []
    key2g: dict[str, int] = {}
    combo_gidx: dict[tuple[str, str], np.ndarray] = {}
    out_paths: dict[tuple[str, str], Path] = {}
    active: list[tuple[str, str]] = []

    for sector, level in combos:
        out_path = _out_path(iso, mode, level, sector)
        out_paths[(sector, level)] = out_path
        if out_path.exists() and not overwrite:
            log.info("[%s/%s/%s/%s] parquet exists — skip", iso, mode, level, sector)
            continue
        level_col = LEVEL_TO_COLUMN[level]
        mask = (
            (schools["adm0_pcode"] == iso)
            & (schools[level_col] == 1)
            & schools["latitud"].notna()
            & schools["longitud"].notna()
            & (schools["include_in_spatial_indicators"] == True)  # noqa: E712
        )
        sv = SECTOR_TO_VALUE[sector]
        if sv is not None:
            mask &= (schools["sector"] == sv)
        sch = schools[mask].reset_index(drop=True)
        if len(sch) == 0:
            log.warning("[%s/%s/%s/%s] no eligible schools — skip (no parquet)",
                        iso, mode, level, sector)
            continue
        k_use = min(k, len(sch))
        tree = cKDTree(sch[["latitud", "longitud"]].values)
        _, idx = tree.query(pop_arr, k=k_use)
        if k_use == 1:
            idx = idx.reshape(-1, 1)
        ids = sch["id_centro"].astype(str).to_numpy()
        lons = sch["longitud"].to_numpy()
        lats = sch["latitud"].to_numpy()
        local2g = np.empty(len(sch), dtype=np.int64)
        for li in range(len(sch)):
            sid = ids[li]
            g = key2g.get(sid)
            if g is None:
                g = len(g_id)
                key2g[sid] = g
                g_id.append(sid)
                g_lon.append(float(lons[li]))
                g_lat.append(float(lats[li]))
            local2g[li] = g
        combo_gidx[(sector, level)] = local2g[idx].astype(np.int32)  # N × k_use
        active.append((sector, level))

    if not active:
        log.info("[%s/%s] nothing to route (all combos present or empty)", iso, mode)
        return

    g_lon_a = np.asarray(g_lon)
    g_lat_a = np.asarray(g_lat)
    g_id_a = np.asarray(g_id, dtype=object)
    log.info("[%s/%s] %d active combos | %d unique schools | routing each cell once",
             iso, mode, len(active), len(g_id_a))

    res_t = {c: np.full(n_cells, np.nan, dtype=np.float32) for c in active}
    res_id = {c: np.full(n_cells, "", dtype=object) for c in active}
    res_n = {c: np.zeros(n_cells, dtype=np.int32) for c in active}

    def query_cell(i: int) -> dict:
        # union of candidate global-indices across active combos → one OSRM call
        gset = np.unique(np.concatenate([combo_gidx[c][i] for c in active]))
        origin = (float(pop_arr[i, 1]), float(pop_arr[i, 0]))
        dests = [(float(g_lon_a[g]), float(g_lat_a[g])) for g in gset]
        row = osrm_table(profile, [origin], dests)[0]
        col = {int(g): j for j, g in enumerate(gset)}
        out = {}
        for c in active:
            cand = combo_gidx[c][i]
            durs = row[[col[int(g)] for g in cand]]
            fin = np.isfinite(durs)
            if fin.any():
                jb = int(np.nanargmin(durs))
                out[c] = (float(durs[jb] / 60.0), str(g_id_a[int(cand[jb])]), int(fin.sum()))
            else:
                out[c] = (float("nan"), "", 0)
        return out

    def worker(i: int):
        try:
            return i, query_cell(i), None
        except OSRMTransportError as exc:
            return i, None, exc

    def _store(i: int, out: dict) -> None:
        for c in active:
            t, sid, nr = out[c]
            res_t[c][i] = t
            res_id[c][i] = sid
            res_n[c][i] = nr

    t0 = time.time()
    progress_every = max(1, n_cells // 20)
    transport_fails: list[int] = []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(worker, i) for i in range(n_cells)]
        for done_count, fut in enumerate(as_completed(futures), 1):
            i, out, exc = fut.result()
            if exc is not None:
                transport_fails.append(i)
            else:
                _store(i, out)
            if done_count % progress_every == 0 or done_count == n_cells:
                elapsed = time.time() - t0
                rate = done_count / elapsed if elapsed > 0 else 0
                eta = (n_cells - done_count) / rate if rate > 0 else 0
                log.info("  %d/%d cells (%.1f%%)  rate=%.0f/s  eta=%.0fs  transport_fails=%d",
                         done_count, n_cells, done_count / n_cells * 100,
                         rate, eta, len(transport_fails))

    if transport_fails:
        log.warning("[%s/%s] %d cells hit transport failures — retrying sequentially",
                    iso, mode, len(transport_fails))
        still: list[int] = []
        for i in transport_fails:
            try:
                _store(i, query_cell(i))
            except OSRMTransportError:
                still.append(i)
        if still:
            raise RuntimeError(
                f"[{iso}/{mode}] {len(still)} cells still fail after a sequential "
                f"retry — OSRM server problem. NOT writing parquets. First: {still[:10]}")
        log.info("[%s/%s] sequential retry recovered all %d cells",
                 iso, mode, len(transport_fails))

    for c in active:
        sector, level = c
        out_df = pop.copy()
        out_df["n_schools_in_range"] = res_n[c]
        out_df["nearest_school_id"] = res_id[c]
        out_df["time_to_nearest_min"] = res_t[c]
        out_df.to_parquet(out_paths[c], index=False)
        n_ok = int(np.isfinite(res_t[c]).sum())
        log.info("[%s/%s/%s/%s] Done. Reachable: %d/%d (%.1f%%) → %s",
                 iso, mode, level, sector, n_ok, n_cells, n_ok / n_cells * 100,
                 out_paths[c])


def main() -> None:
    global OSRM_PORT
    ap = argparse.ArgumentParser(description="OSRM travel-time matrix for one country × level.")
    ap.add_argument("--country", required=True, help="ISO3 (e.g. PAN)")
    ap.add_argument("--mode", default="walking", choices=list(OSRM_PROFILE_BY_MODE))
    ap.add_argument("--level", default="primaria", choices=list(LEVEL_TO_COLUMN))
    ap.add_argument("--sector", default="total", choices=list(SECTOR_TO_VALUE),
                    help="School sector to query (default total = all schools). "
                         "The published Total is derived in Step 10b as "
                         "min(public, private); use public/private here.")
    ap.add_argument("--k", type=int, default=50,
                    help="K nearest schools to query per cell (default 50)")
    ap.add_argument("--max-workers", type=int, default=12,
                    help="Concurrent OSRM requests (default 12)")
    ap.add_argument("--all-combos", action="store_true",
                    help="Route each cell ONCE for this --mode and write all six "
                         "sector×level matrices (public/private × 3 levels). ~6× "
                         "fewer OSRM searches; output bit-identical to per-combo. "
                         "Ignores --sector/--level.")
    ap.add_argument("--port", type=int, default=OSRM_PORT,
                    help=f"OSRM server port (default {OSRM_PORT})")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    OSRM_PORT = args.port

    if args.all_combos:
        run_mode_all_combos(args.country, args.mode, args.k,
                            args.max_workers, args.overwrite)
    else:
        run(args.country, args.mode, args.level, args.sector, args.k,
            args.max_workers, args.overwrite)


if __name__ == "__main__":
    main()

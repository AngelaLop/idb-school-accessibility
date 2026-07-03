#!/usr/bin/env bash
#
# Reproducible OSRM (Tier-2) driver — graph build + travel-time matrices.
#
# Companion of `09b_travel_time_osrm.py` (the Python client). For each ISO it:
#   1. downloads the Geofabrik OSM extract (cached, once),
#   2. builds the foot and car OSRM graphs if missing (extract/partition/customize),
#   3. for each profile, starts the OSRM server and runs 09b for public +
#      private × 3 levels (the SCL Total is derived in 10b as min of the two),
#   4. stops the server.
# Idempotent: an already-built graph is reused. One server at a time on :5000.
#
# Usage (from project root):
#     bash pipeline/09b_osrm_build_and_run.sh CRI ECU PER
#
# Then aggregate to SCL:
#     uv run python pipeline/10b_accessibility_aggregate_osrm.py --countries CRI ECU PER
#
# Requires Docker Desktop running. On Git Bash, MSYS_NO_PATHCONV=1 stops the
# shell from mangling the container-side /data path.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OSRM_DIR="$ROOT/data/transportation/osrm"
IMG="osrm/osrm-backend"
PORT=5000

# ISO3 → Geofabrik path (region/country, no -latest.osm.pbf suffix).
# Verify the path on first use for a new country — a wrong URL fails loudly at curl.
declare -A GEOFABRIK_PATH=(
  [ARG]=south-america/argentina   [BOL]=south-america/bolivia
  [BRA]=south-america/brazil      [CHL]=south-america/chile
  [COL]=south-america/colombia    [ECU]=south-america/ecuador
  [GUY]=south-america/guyana      [PER]=south-america/peru
  [PRY]=south-america/paraguay    [SUR]=south-america/suriname
  [URY]=south-america/uruguay
  [BLZ]=central-america/belize    [CRI]=central-america/costa-rica
  [GTM]=central-america/guatemala [HND]=central-america/honduras
  [PAN]=central-america/panama    [SLV]=central-america/el-salvador
  [JAM]=central-america/jamaica
  [DOM]=central-america/haiti-and-domrep
  [HTI]=central-america/haiti-and-domrep
  [MEX]=north-america/mexico
)

# ISO3 → direct PBF URL, for small Caribbean states Geofabrik does not split out.
# Source: OSM-France (download.openstreetmap.fr) — same OpenStreetMap planet, a
# different mirror. Takes precedence over GEOFABRIK_PATH. Caveat: Bahamas is an
# archipelago and OSRM routes land only (no inter-island ferries).
declare -A PBF_URL=(
  [BHS]=https://download.openstreetmap.fr/extracts/central-america/bahamas.osm.pbf
  [BRB]=https://download.openstreetmap.fr/extracts/central-america/barbados.osm.pbf
)

log() { echo "[$(date +%T)] $*"; }

build_graph() {           # $1 ISO  $2 profile(foot|car)
  local iso="$1" profile="$2"
  local dir="$OSRM_DIR/${iso}_${profile}"
  if [ -f "$dir/network.osrm.mldgr" ]; then
    log "[$iso/$profile] graph present — skip build"
    return
  fi
  # Resolve the OSM source: a direct PBF_URL (OSM-FR) wins; else Geofabrik.
  local url="${PBF_URL[$iso]:-}"
  if [ -z "$url" ]; then
    local geo="${GEOFABRIK_PATH[$iso]:-}"
    [ -n "$geo" ] || { echo "ERROR: no OSM source for $iso — add to GEOFABRIK_PATH or PBF_URL"; exit 1; }
    url="https://download.geofabrik.de/${geo}-latest.osm.pbf"
  fi

  mkdir -p "$OSRM_DIR/_pbf" "$dir"
  local pbf="$OSRM_DIR/_pbf/${iso}.osm.pbf"
  if [ ! -f "$pbf" ]; then
    log "[$iso] downloading $url ..."
    curl -fL --retry 3 -o "$pbf" "$url"
  fi
  cp -f "$pbf" "$dir/network.osm.pbf"
  log "[$iso/$profile] extract → partition → customize ..."
  MSYS_NO_PATHCONV=1 docker run --rm -v "$dir:/data" "$IMG" \
    osrm-extract -p "/opt/${profile}.lua" /data/network.osm.pbf
  MSYS_NO_PATHCONV=1 docker run --rm -v "$dir:/data" "$IMG" \
    osrm-partition /data/network.osrm
  MSYS_NO_PATHCONV=1 docker run --rm -v "$dir:/data" "$IMG" \
    osrm-customize /data/network.osrm
  log "[$iso/$profile] graph built"
}

run_profile() {           # $1 ISO  $2 profile  $3 mode
  local iso="$1" profile="$2" mode="$3"
  local dir="$OSRM_DIR/${iso}_${profile}" name="osrm-run"
  docker rm -f "$name" >/dev/null 2>&1 || true
  MSYS_NO_PATHCONV=1 docker run -d --name "$name" -p "$PORT:5000" \
    -v "$dir:/data" "$IMG" osrm-routed --algorithm mld /data/network.osrm >/dev/null
  local up=""
  for _ in $(seq 1 40); do
    if curl -s "http://localhost:$PORT/nearest/v1/$profile/0,0?number=1" 2>/dev/null | grep -q '"code"'; then
      up=1; break; fi
    sleep 1
  done
  [ -n "$up" ] || { echo "ERROR: OSRM server for $iso/$profile failed to start"; docker rm -f "$name" >/dev/null 2>&1; exit 1; }
  log "[$iso/$profile] server up — running 09b (public + private × 3 levels)"
  # Query public and private separately; the published Total is derived in
  # Step 10b as min(public, private) — same contract as Step 09/10 FMM. A
  # sector with no georeferenced school (e.g. HND private) is skipped inside
  # the client without writing, so the loop never aborts.
  for sector in public private; do
    for level in primaria secbaja secalta; do
      uv run python "$ROOT/pipeline/09b_travel_time_osrm.py" \
        --country "$iso" --mode "$mode" --level "$level" \
        --sector "$sector" --port "$PORT" --overwrite
    done
  done
  docker rm -f "$name" >/dev/null 2>&1 || true
}

[ "$#" -ge 1 ] || { echo "Usage: bash pipeline/09b_osrm_build_and_run.sh ISO [ISO ...]"; exit 1; }
log "OSRM driver — countries: $*"
for iso in "$@"; do
  build_graph "$iso" foot
  build_graph "$iso" car
  run_profile "$iso" foot walking
  run_profile "$iso" car  motorized
  log "[$iso] DONE — up to 12 matrices written to results/osrm/ (2 modes × 2 sectors × 3 levels)"
done
log "ALL DONE. Next: uv run python pipeline/10b_accessibility_aggregate_osrm.py --countries $*"

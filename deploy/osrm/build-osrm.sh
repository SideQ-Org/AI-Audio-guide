#!/usr/bin/env bash
# One-time OSRM pre-processing for the proactive "guided" mode (foot profile).
#
# The osrm-foot service in docker-compose.yml serves routes from pre-processed graph
# files (region.osrm*). This script builds them from an OSM extract, using the SAME
# osrm/osrm-backend image so the tool versions match the server. Run it once per region
# (and re-run when you refresh the OSM data). It's heavy (CPU + RAM + disk), which is why
# it's a manual step, not part of `docker compose up`.
#
# Usage:
#   1) Put a regional OSM extract here as region.osm.pbf, e.g. from https://download.geofabrik.de
#      (pick the smallest extract that covers where your users walk — a city/oblast, NOT a
#       whole continent; foot routing over a huge extract needs a lot of RAM).
#        curl -L -o region.osm.pbf https://download.geofabrik.de/<...>-latest.osm.pbf
#   2) ./build-osrm.sh
#   3) docker compose up -d osrm-foot     (and set ROUTING_SOURCE=osrm in ../backend/.env)
#
# Result: region.osrm* files land next to region.osm.pbf in this folder (the ./osrm bind mount).

set -euo pipefail
cd "$(dirname "$0")"

PBF="${1:-region.osm.pbf}"
IMAGE="osrm/osrm-backend"
BASENAME="$(basename "${PBF%.osm.pbf}")"   # region.osm.pbf -> region

if [[ ! -f "$PBF" ]]; then
  echo "error: $PBF not found. Download a regional extract first (see the header of this script)." >&2
  exit 1
fi

echo "==> extract (foot profile)"
docker run --rm -t -v "$PWD:/data" "$IMAGE" \
  osrm-extract -p /opt/foot.lua "/data/$PBF"

echo "==> partition"
docker run --rm -t -v "$PWD:/data" "$IMAGE" \
  osrm-partition "/data/${BASENAME}.osrm"

echo "==> customize"
docker run --rm -t -v "$PWD:/data" "$IMAGE" \
  osrm-customize "/data/${BASENAME}.osrm"

echo "==> done. region.osrm* built. Start with: docker compose up -d osrm-foot"

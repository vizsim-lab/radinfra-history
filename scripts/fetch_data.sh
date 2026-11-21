#!/usr/bin/env bash
# Put the published build of a region into site/data/<region>/: to work on the
# page locally without building first, and for the quarterly update, which
# appends to the build's versions. Public URLs, no key needed.
#
#   scripts/fetch_data.sh [region]                from the bucket (default berlin)
#   scripts/fetch_data.sh --from-branch [region]  from the old `data` branch, as a
#                                                 build in the new layout (until it is gone)
#
# DATA_URL: the bucket's public address (default https://tiles.vizsim.de/file/radinfra-history).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

from_branch=
if [ "${1:-}" = "--from-branch" ]; then
    from_branch=1
    shift
fi
REGION="${1:-berlin}"
DIR="site/data/$REGION"

if [ -z "$from_branch" ]; then
    uv run --quiet python -m pipeline.builds fetch "$DIR" \
        "${DATA_URL:-https://tiles.vizsim.de/file/radinfra-history}/$REGION/"
    exit 0
fi

# The branch holds <slug>_timeline.json, <slug>.pmtiles and so on, flat.
BRANCH="${DATA_BRANCH:-data}"
git fetch --quiet --depth 1 origin "$BRANCH"
rm -rf "$DIR/work"
mkdir -p "$DIR/work"
while read -r source target; do
    git show "FETCH_HEAD:${REGION}${source}" > "$DIR/work/$target"
done <<EOF
_timeline.json timeline.json
_versions.parquet versions.parquet
_spans.bin spans.bin
_areas.json areas.json
_areas.geojson areas.geojson
.pmtiles tiles.pmtiles
_lo.pmtiles tiles_lo.pmtiles
EOF
uv run --quiet python -m pipeline.builds finish "$DIR"

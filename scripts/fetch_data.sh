#!/usr/bin/env bash
# Put the published data (branch `data`) into site/ and data/: to work on the
# page locally without building it first, and for the quarterly update, which
# appends to the versions file.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRANCH="${DATA_BRANCH:-data}"
cd "$ROOT"
git fetch --quiet --depth 1 origin "$BRANCH"
mkdir -p site/tiles site/data data
for name in $(git ls-tree --name-only FETCH_HEAD); do
    case "$name" in
        *.pmtiles) git show "FETCH_HEAD:$name" > "site/tiles/$name" ;;
        *_timeline.json|*_areas.json|*_areas.geojson|*_spans.bin) git show "FETCH_HEAD:$name" > "site/data/$name" ;;
        *_versions.parquet) git show "FETCH_HEAD:$name" > "data/$name" ;;
    esac
done
ls -l site/tiles site/data data/*_versions.parquet 2>/dev/null || true

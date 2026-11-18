#!/usr/bin/env bash
# Put the published tiles and timeline (branch `data`) into site/, to work on
# the page locally without building them first.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRANCH="${DATA_BRANCH:-data}"
cd "$ROOT"
git fetch --quiet --depth 1 origin "$BRANCH"
mkdir -p site/tiles site/data
for name in $(git ls-tree --name-only FETCH_HEAD); do
    case "$name" in
        *.pmtiles) git show "FETCH_HEAD:$name" > "site/tiles/$name" ;;
        *_timeline.json) git show "FETCH_HEAD:$name" > "site/data/$name" ;;
    esac
done
ls -l site/tiles site/data

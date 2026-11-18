#!/usr/bin/env bash
# Download the published tiles and timeline into site/, to run the page locally
# without building them. Needs the GitHub CLI (gh).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${REPO:-vizsim-lab/radinfra-history}"
gh release download data -R "$REPO" --pattern '*.pmtiles' --dir "$ROOT/site/tiles" --clobber
gh release download data -R "$REPO" --pattern '*_timeline.json' --dir "$ROOT/site/data" --clobber
ls -l "$ROOT/site/tiles" "$ROOT/site/data"

#!/usr/bin/env bash
# Install the pinned OSMnexus release and its tilda config into .tools/osmnexus.
#
# Only the bikelanes topic is copied: OSMnexus runs every topic in the config
# directory, and the roads topic would double the work for nothing the map shows.
# The shared files at the config root (macros, sanitizers, lists, units) come along.
set -euo pipefail

TAG="${OSMNEXUS_TAG:-v0.1.0-build1}"
REPO="rush42/OSMnexus"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/.tools/osmnexus"
ASSET="osmnexus-${TAG}-x86_64-linux.tar.gz"

mkdir -p "$DEST"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "OSMnexus $TAG → $DEST"
curl -fsSL -o "$tmp/$ASSET" "https://github.com/$REPO/releases/download/$TAG/$ASSET"
tar -xzf "$tmp/$ASSET" -C "$tmp"
install -m 0755 "$tmp/osmnexus" "$DEST/osmnexus"

git clone -q --depth 1 --branch "$TAG" "https://github.com/$REPO.git" "$tmp/src"
rm -rf "$DEST/config"
mkdir -p "$DEST/config"
cp "$tmp/src/configs/tilda/"*.json "$DEST/config/"
cp -r "$tmp/src/configs/tilda/bikelanes" "$DEST/config/bikelanes"
echo "$TAG" > "$DEST/VERSION"

"$DEST/osmnexus" --help > /dev/null
echo "ok: $("$DEST/osmnexus" --version 2>/dev/null || echo "$TAG")"

#!/usr/bin/env bash
# Publish the current build of a region to the B2 bucket (pipeline/builds.py).
#
#   scripts/publish_b2.sh [region]     default berlin
#
# Order matters, so the page never sees half a build: first the build's files
# (cached for good: their path names their content), then the timeline that
# points at it (revalidated on every load). Then every build but the current one
# and the newest other (KEEP_BUILDS, default 2) is deleted: a page loaded just
# before the switch still finds its files.
#
# Key: B2_APPLICATION_KEY_ID and B2_APPLICATION_KEY from the environment (the
# workflow's secrets), else from .env in the repository or in pipeline/. The b2 CLI
# caches its login in a temporary file, so a login of yours for other buckets
# stays as it is. Needs the b2 CLI (uv tool install b2).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REGION="${1:-berlin}"
BUCKET="${B2_BUCKET:-radinfra-history}"
KEEP="${KEEP_BUILDS:-2}"
DIR="site/data/$REGION"

if [ -z "${B2_APPLICATION_KEY_ID:-}" ]; then
    for env in .env pipeline/.env; do
        if [ -f "$env" ]; then
            set -a
            # shellcheck disable=SC1090
            . "$env"
            set +a
            break
        fi
    done
fi
if [ -z "${B2_APPLICATION_KEY_ID:-}" ] || [ -z "${B2_APPLICATION_KEY:-}" ]; then
    echo "no B2 key: set B2_APPLICATION_KEY_ID and B2_APPLICATION_KEY, or put them in .env" >&2
    exit 1
fi
command -v b2 >/dev/null || { echo "b2 CLI not found: uv tool install b2" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export B2_ACCOUNT_INFO="$tmp/account_info"
b2 account authorize >/dev/null

build="$(uv run --quiet python -m pipeline.builds check "$DIR")"
prefix="$REGION/builds/$build"
mapfile -t files < <(cd "$DIR/builds/$build" && find . -type f -printf '%P\n' | sort)

# B2 answers "503 too_busy" when an upload pod is full, and the b2 CLI gives up
# after five quick tries: wait longer between them, as B2's docs ask.
upload() {  # local file, bucket name, content type, cache control
    local wait
    for wait in 0 10 30 60 120; do
        sleep "$wait"
        if b2 file upload --no-progress --content-type "$3" --cache-control "$4" \
            "$BUCKET" "$1" "$2" >/dev/null 2>"$tmp/error"; then
            return 0
        fi
        echo "  $(tail -1 "$tmp/error"); again in a while" >&2
    done
    cat "$tmp/error" >&2
    return 1
}

# What of the build is on the bucket already has the same bytes (its path names
# them), so a run that stopped carries on with the rest.
declare -A there=()
while read -r name; do
    there["${name#"$prefix"/}"]=1
done < <(b2 ls --recursive "b2://$BUCKET/$prefix/")
todo=0
for name in "${files[@]}"; do
    [ -n "${there[$name]:-}" ] && continue
    todo=$((todo + 1))
    case "$name" in
        *.json|*.geojson) type=application/json ;;
        *) type=application/octet-stream ;;  # not compressed by Cloudflare: Range must see the bytes
    esac
    echo "upload $prefix/$name"
    upload "$DIR/builds/$build/$name" "$prefix/$name" "$type" "public, max-age=31536000, immutable"
done
if [ "$todo" -eq 0 ]; then
    echo "build $build: on $BUCKET already"
fi

echo "upload $REGION/timeline.json → $build"
upload "$DIR/timeline.json" "$REGION/timeline.json" application/json "no-cache"

b2 ls --json --recursive "b2://$BUCKET/$REGION/builds/" > "$tmp/listing.json"
uv run --quiet python -m pipeline.builds stale "$REGION/builds/" "$build" "$KEEP" < "$tmp/listing.json" |
    while read -r old; do
        echo "delete $REGION/builds/$old/"
        b2 rm --recursive --versions --no-progress "b2://$BUCKET/$REGION/builds/$old/" >/dev/null </dev/null
    done
echo "published $REGION: build $build"

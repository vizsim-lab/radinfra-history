#!/usr/bin/env bash
# Publish the built tiles and timeline on the `data` branch and redeploy Pages.
#
# The branch holds a single orphan commit that is replaced on every publish, so
# old tilesets do not pile up in the history the way they would on main.
# Needs the GitHub CLI (gh) for the redeploy; without it, run the Pages workflow
# by hand from the Actions tab.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRANCH="${DATA_BRANCH:-data}"
shopt -s nullglob
files=("$ROOT"/site/tiles/*.pmtiles "$ROOT"/site/data/*_timeline.json "$ROOT"/site/data/*_areas.json "$ROOT"/site/data/*_areas.geojson)
if [ ${#files[@]} -eq 0 ]; then
    echo "nothing to publish: build first (python -m pipeline all ...)" >&2
    exit 1
fi

tmp="$(mktemp -d)"
cleanup() {
    git -C "$ROOT" worktree remove --force "$tmp" 2>/dev/null || true
    git -C "$ROOT" branch -D "$BRANCH-publish" >/dev/null 2>&1 || true
}
trap cleanup EXIT

git -C "$ROOT" worktree add --quiet --detach "$tmp"
cd "$tmp"
git checkout --quiet --orphan "$BRANCH-publish"
git rm -r --quiet --cached . >/dev/null
git clean -fdxq
cp "${files[@]}" .
cat > README.md <<EOF
# Published data

Built tiles and timeline for the page on \`main\`, replaced on every publish
(\`scripts/publish_data.sh\`). The Pages workflow copies them next to the page.

Map data © OpenStreetMap contributors, ODbL.
EOF
git add -A
# COMMIT_TRAILER: optional lines appended to the commit message (e.g. Co-Authored-By).
git commit --quiet -F - <<EOF
Data as of $(date -u +%Y-%m-%d)

$(cd "$tmp" && ls -l --time-style=+ *.pmtiles *.json *.geojson 2>/dev/null | awk '{print $5, $NF}')
${COMMIT_TRAILER:+
$COMMIT_TRAILER}
EOF
git push --force --quiet origin "HEAD:refs/heads/$BRANCH"
echo "published to $BRANCH:"
git ls-tree -l HEAD | awk '{print "  " $5 "  " $4 " bytes"}'

if command -v gh >/dev/null 2>&1; then
    gh workflow run pages.yml --ref main -R "$(git -C "$ROOT" remote get-url origin | sed -E 's#.*github.com[:/]##; s#\.git$##')" \
        && echo "Pages redeploy started"
fi

#!/usr/bin/env bash
# Commit and push derived changes, surviving races with the phone app.
#
# Usage: scripts/publish.sh "<commit message>" <prepare command...>
#
# The prepare command must be a pure function of the working tree (finalize, archive-stale, build).
# On a rejected push we reset to the new origin/main, copy back anything in $RESTORE_DIR
# (files Claude created, mirrored at their repo paths), re-run prepare and try again.
# index.json is never merged: it is always rebuilt from scratch.
set -euo pipefail

msg="$1"; shift
branch="${PUBLISH_BRANCH:-main}"
cd "$(git rev-parse --show-toplevel)"
git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

for attempt in 1 2 3 4 5; do
  if [ "$attempt" -gt 1 ]; then
    sleep $((attempt * 2))
    git fetch --quiet --depth=1 origin "$branch"
    git reset --quiet --hard "origin/$branch"
    git clean --quiet -fd -- tasks archive inbox briefs
    if [ -n "${RESTORE_DIR:-}" ] && [ -d "$RESTORE_DIR" ]; then
      cp -R "$RESTORE_DIR"/. .
    fi
  fi
  "$@"
  git add -A -- tasks archive inbox index.json briefs $(ls people.json 2>/dev/null)
  if git diff --cached --quiet; then
    echo "nothing to publish"
    exit 0
  fi
  git commit --quiet -m "$msg"
  if git push --quiet origin "HEAD:$branch"; then
    echo "published on attempt $attempt"
    exit 0
  fi
  echo "push rejected (attempt $attempt), rebuilding on the new head"
done
echo "giving up after 5 attempts" >&2
exit 1

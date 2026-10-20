#!/usr/bin/env bash
# Host -> Git one-way backup sync for this repo (same pattern as
# RailReplay/scripts/auto-sync.sh).
#
# Commits and pushes whatever is on disk to `origin`. Never fetches, pulls,
# or resets from any remote — the server's working tree (data/ excluded via
# .gitignore — videos/media don't belong in Git) is always the source of
# truth. secrets/ und data/ sind per .gitignore ausgeschlossen.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

git add -A

if git diff --cached --quiet; then
  echo "[$(date -Is)] no changes, nothing to sync"
  exit 0
fi

git commit -m "auto-sync $(date -Is)" --quiet
git push origin main
echo "[$(date -Is)] synced to origin/main"

#!/usr/bin/env bash
# Publishes the state directory as a single snapshot commit on the `data` branch (force push).
# History is intentionally not kept: runs append to files, and a 5-minute commit history would bloat the repo.
set -euo pipefail
cd "${SPXAI_STATE_DIR:?SPXAI_STATE_DIR not set}"
git add -A
if git diff --cached --quiet && git rev-parse --verify -q HEAD >/dev/null; then
  echo "no state changes"; exit 0
fi
git checkout --orphan snapshot >/dev/null 2>&1
git add -A
git commit -q -m "state snapshot $(date -u +%FT%TZ)"
git push --force origin snapshot:data
echo "published"

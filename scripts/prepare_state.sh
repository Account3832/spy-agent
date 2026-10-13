#!/usr/bin/env bash
# Checks out the `data` branch (runtime state) into ./data_wt and points SPXAI_STATE_DIR at it.
# The `data` branch only ever holds ONE snapshot commit (see publish_state.sh) so repo size stays small.
set -euo pipefail
WT="${GITHUB_WORKSPACE:-$PWD}/data_wt"
git config user.name "spxai-bot"
git config user.email "spxai-bot@users.noreply.github.com"
if git ls-remote --exit-code --heads origin data >/dev/null 2>&1; then
  git fetch --depth 1 origin data
  git worktree add "$WT" -B data FETCH_HEAD
else
  git worktree add --detach "$WT"
  (cd "$WT" && git checkout --orphan data >/dev/null 2>&1 && git rm -rf . >/dev/null 2>&1 || true)
  mkdir -p "$WT/bars"
fi
echo "SPXAI_STATE_DIR=$WT" >> "${GITHUB_ENV:-/dev/null}"
echo "state directory: $WT"

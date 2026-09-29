#!/usr/bin/env bash
# GitHub Actions helper: pull data/jobs.db from the `state` branch (if it exists).
set -euo pipefail
mkdir -p data
if git fetch --depth=1 origin state 2>/dev/null; then
  git show origin/state:jobs.db > data/jobs.db
  echo "Restored jobs.db ($(du -h data/jobs.db | cut -f1)) from state branch"
else
  echo "No state branch yet; starting with an empty DB (first run will seed silently)"
fi

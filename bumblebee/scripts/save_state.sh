#!/usr/bin/env bash
# GitHub Actions helper: force-push data/jobs.db to the `state` branch as a
# single orphan commit, so the repo doesn't grow by one DB copy every 15 min.
set -euo pipefail
tmp=$(mktemp -d)
cp data/jobs.db "$tmp/jobs.db"
cd "$tmp"
git init -q
git checkout -q -b state
git config user.name "bumblebee"
git config user.email "bumblebee@users.noreply.github.com"
git add jobs.db
git commit -q -m "state $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q --force "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" state
echo "Saved jobs.db to state branch"

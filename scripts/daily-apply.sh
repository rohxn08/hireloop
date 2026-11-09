#!/usr/bin/env bash
# Start Claude Code on today's job batch, at most once per day.
# Run from VS Code's "Daily Wellfound batch" task (opens with the folder), or by hand.
#   scripts/daily-apply.sh          run if it hasn't run today
#   scripts/daily-apply.sh --force  run anyway
set -euo pipefail
cd "$(dirname "$0")/.."

marker="data/last-daily-run"
today="$(date +%F)"
mkdir -p data
if [[ "${1:-}" != "--force" && -f "$marker" && "$(cat "$marker")" == "$today" ]]; then
    echo "Today's batch already ran ($today). Use --force to run again."
    exit 0
fi
echo "$today" > "$marker"

# Give the network and the desktop a moment after login.
sleep "${HIRELOOP_DAILY_DELAY:-${WELLFOUND_DAILY_DELAY:-20}}"
exec claude "$(cat scripts/daily-prompt.md)"

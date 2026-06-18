#!/bin/zsh
# Daily scraper for the Texas Lottery dashboard. Invoked by launchd (once/day)
# or manually: `zsh run_scrape.sh`.
#
# IMPORTANT: project lives at ~/texas-lottery-dashboard (NOT in ~/Documents), so the
# launchd background job isn't blocked by macOS privacy protection (TCC).

set -euo pipefail

PROJECT_DIR="/Users/aidan/texas-lottery-dashboard"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

STAMP="$(date +%Y-%m-%d_%H%M%S)"
LOG_FILE="$LOG_DIR/scrape-$STAMP.log"

echo "[$STAMP] starting daily scrape" >> "$LOG_FILE"
"$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scraper.py" >> "$LOG_FILE" 2>&1
echo "[$(date +%Y-%m-%d_%H%M%S)] done (exit $?)" >> "$LOG_FILE"

#!/bin/zsh
# Daily scraper for the Texas Lottery dashboard. Invoked by launchd (3x/day)
# or manually: `zsh run_scrape.sh`.
#
# IMPORTANT: project lives at ~/texas-lottery-dashboard (NOT in ~/Documents), so the
# launchd background job isn't blocked by macOS privacy protection (TCC).
#
# After scraping, it commits + pushes data/lottery.db to GitHub so the Streamlit
# Community Cloud deploy redeploys with fresh data. The cloud GitHub Action pushes the
# same file on its own schedule, so this script first UNIONS the remote DB's rows into
# the local one (SQLite INSERT OR IGNORE) before pushing — that way neither pipeline
# ever overwrites snapshot history the other accumulated while this Mac was offline.

set -euo pipefail

PROJECT_DIR="/Users/aidan/texas-lottery-dashboard"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON="$PROJECT_DIR/.venv/bin/python"
DB_PATH="$PROJECT_DIR/data/lottery.db"
mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

STAMP="$(date +%Y-%m-%d_%H%M%S)"
LOG_FILE="$LOG_DIR/scrape-$STAMP.log"

echo "[$STAMP] starting daily scrape" >> "$LOG_FILE"
"$PYTHON" "$PROJECT_DIR/scraper.py" >> "$LOG_FILE" 2>&1
echo "[$(date +%Y-%m-%d_%H%M%S)] done (exit $?)" >> "$LOG_FILE"

# --- publish to GitHub (best-effort; a failure here never loses local data) -------
# Runs in an `if` so `set -e` is suspended inside the function: each git step handles
# its own failure and we simply try again on the next scheduled run.
publish_to_github() {
    # Don't bother (or push noise) when there's no GitHub remote configured.
    git remote get-url origin >/dev/null 2>&1 || { echo "no 'origin' remote; skipping push"; return 0; }

    # Pull the cloud Action's latest committed DB and fold any rows we're missing into
    # our freshly scraped local DB. Local rows win on primary-key collisions (we just
    # scraped them); remote-only snapshot dates get added. If this union can't complete,
    # bail BEFORE committing so we never publish a DB that drops the remote's history.
    git fetch --quiet origin main || { echo "fetch failed; skipping push"; return 1; }
    local remote_db="$LOG_DIR/.remote.db"   # under logs/ (gitignored), so never committed
    if git show origin/main:data/lottery.db > "$remote_db" 2>/dev/null; then
        if ! "$PYTHON" - "$DB_PATH" "$remote_db" <<'PY'
import sqlite3, sys
local, remote = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(local)
conn.execute("ATTACH ? AS remote", (remote,))
for tbl in ("prizes", "game_meta"):
    cols = ",".join(r[1] for r in conn.execute(f"PRAGMA table_info({tbl})"))
    conn.execute(f"INSERT OR IGNORE INTO {tbl} ({cols}) SELECT {cols} FROM remote.{tbl}")
conn.commit()
conn.close()
PY
        then
            echo "DB union failed; skipping push to protect history"
            rm -f "$remote_db"
            return 1
        fi
        rm -f "$remote_db"
    fi

    git add data/lottery.db
    if git diff --quiet --cached; then
        echo "no data changes to publish"
        return 0
    fi
    git commit --quiet -m "Scrape: update prize data (local $(date -u '+%Y-%m-%d %H:%MZ'))" \
        || { echo "commit failed"; return 1; }

    # Our tree already contains every remote row (unioned above), so the 'ours' merge
    # strategy reconciles the cloud Action's parallel commit without dropping anything.
    # (No-op when origin/main hasn't moved — a normal fast-forward push then suffices.)
    git merge -s ours --quiet -m "Merge cloud scrape history" origin/main \
        || { echo "merge with origin/main failed"; return 1; }
    GIT_TERMINAL_PROMPT=0 git push --quiet origin HEAD:main \
        || { echo "push failed (will retry next run)"; return 1; }
    echo "published to origin/main"
}

if publish_to_github >> "$LOG_FILE" 2>&1; then
    echo "[$(date +%Y-%m-%d_%H%M%S)] publish ok" >> "$LOG_FILE"
else
    echo "[$(date +%Y-%m-%d_%H%M%S)] publish skipped/failed (see above)" >> "$LOG_FILE"
fi

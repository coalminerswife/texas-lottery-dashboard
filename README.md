# Texas Scratch-Off Tracker

A dashboard that tracks Texas Lottery scratch-off games: how many grand prizes are
still out there, which games had a grand prize claimed recently, and a sortable view
of every active game and its prize tiers.

## How it works
The official Texas Lottery site publishes one current snapshot of prizes claimed vs.
printed per game (no history). This project scrapes that snapshot **once a day** and
stores it in SQLite (`data/lottery.db`). With multiple days on file it computes
day-over-day diffs, which is what powers the "recently claimed grand prizes" view.

Data granularity is **game-level**, not per-roll (the lottery does not publish roll
data). "Recently" means "since the previous daily snapshot."

## Setup
```bash
cd ~/texas-lottery-dashboard
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Use
```bash
# Pull today's snapshot (run daily to build history)
.venv/bin/python scraper.py

# Launch the dashboard
.venv/bin/streamlit run app.py
```

## Files
- `lottery.py` — scrape, parse, SQLite storage, and the diff/query engine.
- `scraper.py` — CLI: fetch + store one daily snapshot.
- `app.py` — Streamlit dashboard.
- `run_scrape.sh` — wrapper for the daily cron (launchd).
- `data/lottery.db` — accumulated snapshots (the value is in the history).

## Notes
- Source data updates once daily, so this is not live/intraday.
- Be polite: one fetch per day. There is no robots.txt disallow, and the data is public,
  but don't hammer the site.
- "Tickets remaining" is not published (only *prizes* remaining), so any odds estimate
  would be approximate.

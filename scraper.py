#!/usr/bin/env python
"""CLI entry point: fetch the current Texas Lottery snapshot and store it.

Run daily (cron/launchd) to build the history the dashboard relies on:
    .venv/bin/python scraper.py
"""

import sys

import lottery


def main() -> int:
    try:
        date, n = lottery.scrape_and_store()
    except Exception as exc:  # noqa: BLE001 - surface any failure to the cron log
        print(f"[scrape] FAILED: {exc}", file=sys.stderr)
        return 1

    conn = lottery.get_conn()
    try:
        dates = lottery.snapshot_dates(conn)
        winners = lottery.recent_winners(conn)
    finally:
        conn.close()

    print(f"[scrape] stored snapshot {date}: {n} prize rows ({len(dates)} snapshot(s) on file)")
    if len(dates) < 2:
        print("[scrape] only one snapshot so far; 'recent winners' starts once tomorrow's run lands.")
    else:
        print(f"[scrape] {len(winners)} grand-prize claim(s) since the previous snapshot ({dates[1]}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Core logic for the Texas Lottery scratch-off dashboard.

Scrapes the official Texas Lottery "all games" prize-claim page once per run,
stores a dated snapshot in SQLite, and computes day-over-day deltas so the app
can answer "which games had a grand prize claimed recently" and "how many are
still out there." The official site only publishes a single current snapshot
with no history, so accumulating these snapshots locally is the whole point.

Data source (one page contains every prize tier for every active game):
  https://www.texaslottery.com/export/sites/lottery/Games/Scratch_Offs/all.html
The page is a single table whose columns are:
  Game Number | Start Date | Ticket Price | (icon) | Game Name | Prize Amount | Prizes Printed | Prizes Claimed
A new game starts on any row where Game Number is non-empty; following rows with
a blank Game Number are additional (lower) prize tiers of that same game.
"""

from __future__ import annotations

import datetime as _dt
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SITE_ROOT = "https://www.texaslottery.com"
DATA_URL = (
    "https://www.texaslottery.com/export/sites/lottery/"
    "Games/Scratch_Offs/all.html"
)
USER_AGENT = "texas-lottery-dashboard/1.0 (personal project; one polite fetch/day)"
DB_PATH = Path(__file__).resolve().parent / "data" / "lottery.db"


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
@dataclass
class PrizeRow:
    game_number: str
    game_name: str
    price: int | None
    start_date: str
    tier_index: int          # 0 == top / grand prize
    prize_amount: str        # raw text, e.g. "$1,000,000"
    prize_value: int | None  # parsed int, e.g. 1000000
    printed: int
    claimed: int

    @property
    def remaining(self) -> int:
        return self.printed - self.claimed


def _money_to_int(text: str) -> int | None:
    """'$1,000,000' -> 1000000 ; returns None if not a clean dollar amount."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _int(text: str) -> int:
    return int(re.sub(r"[^\d]", "", text) or 0)


def fetch_html(url: str = DATA_URL, timeout: int = 30) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse(html: str) -> tuple[str, list[PrizeRow]]:
    """Return (snapshot_date_iso, rows). Raises ValueError if the page shape changed."""
    # Official "as of" date, e.g. "Prizes Claimed as of June 17, 2026"
    m = re.search(r"Prizes Claimed as of ([A-Za-z]+ \d{1,2}, \d{4})", html)
    if not m:
        raise ValueError("Could not find the 'Prizes Claimed as of' date; page layout may have changed.")
    snapshot_date = _dt.datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()

    # Use the stdlib parser (no external lxml dependency, so it works under launchd's
    # minimal environment too, where lxml's compiled library failed to load).
    soup = BeautifulSoup(html, "html.parser")
    rows: list[PrizeRow] = []
    cur_game_number = cur_game_name = cur_start = ""
    cur_price: int | None = None
    tier_index = 0

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        cells = [td.get_text(strip=True) for td in tds]
        game_number, start_date, price, _icon, game_name, prize_amount, printed, claimed = cells[:8]

        if not prize_amount:
            continue  # spacer / non-data row

        if game_number:  # start of a new game -> this is its top/grand prize
            cur_game_number = game_number
            cur_game_name = game_name
            cur_start = start_date
            cur_price = _money_to_int(price)
            tier_index = 0
        else:            # additional prize tier of the current game
            tier_index += 1

        rows.append(
            PrizeRow(
                game_number=cur_game_number,
                game_name=cur_game_name,
                price=cur_price,
                start_date=cur_start,
                tier_index=tier_index,
                prize_amount=prize_amount,
                prize_value=_money_to_int(prize_amount),
                printed=_int(printed),
                claimed=_int(claimed),
            )
        )

    if not rows:
        raise ValueError("Parsed zero prize rows; page layout may have changed.")
    return snapshot_date, rows


def parse_game_urls(html: str) -> dict[str, str]:
    """Map each game number to the absolute URL of its detail page."""
    soup = BeautifulSoup(html, "html.parser")
    urls: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        game_number = tds[0].get_text(strip=True)
        if not game_number:
            continue
        a = tr.find("a", href=re.compile("details"))
        if a and a.get("href"):
            href = a["href"]
            urls[game_number] = href if href.startswith("http") else SITE_ROOT + href
    return urls


def parse_detail_meta(html: str) -> tuple[int | None, float | None]:
    """From a game detail page, return (total_tickets, overall_odds).

    The page states e.g. "approximately 15,267,250 tickets" and
    "Overall odds of winning any prize ... are 1 in 3.41". Both are static
    per game (set at production), so we only need to fetch them once.
    """
    tt = re.search(r"approximately\s+([\d,]+)\*?\s+tickets", html, re.I)
    # Non-greedy across the sentence; the literal "1 in " only appears at the odds value
    # (game names like "$1,000,000" contain no " in "), so this won't catch a stray digit.
    oo = re.search(r"[Oo]verall odds.*?1 in ([\d.]+)", html, re.S)
    total_tickets = int(tt.group(1).replace(",", "")) if tt else None
    overall_odds = float(oo.group(1)) if oo else None
    return total_tickets, overall_odds


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prizes (
            snapshot_date TEXT NOT NULL,
            scraped_at    TEXT NOT NULL,
            game_number   TEXT NOT NULL,
            game_name     TEXT,
            price         INTEGER,
            start_date    TEXT,
            tier_index    INTEGER NOT NULL,
            prize_amount  TEXT,
            prize_value   INTEGER,
            printed       INTEGER,
            claimed       INTEGER,
            PRIMARY KEY (snapshot_date, game_number, tier_index)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prizes_game ON prizes(game_number, tier_index)")
    # Static per-game facts (total tickets, overall odds) scraped once from detail pages.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS game_meta (
            game_number   TEXT PRIMARY KEY,
            total_tickets INTEGER,
            overall_odds  REAL,
            detail_url    TEXT,
            fetched_at    TEXT
        )
        """
    )
    conn.commit()


def store(conn: sqlite3.Connection, snapshot_date: str, rows: list[PrizeRow]) -> int:
    """Upsert all rows for a snapshot. Re-running the same day is idempotent."""
    scraped_at = _dt.datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        """
        INSERT OR REPLACE INTO prizes
            (snapshot_date, scraped_at, game_number, game_name, price, start_date,
             tier_index, prize_amount, prize_value, printed, claimed)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                snapshot_date, scraped_at, r.game_number, r.game_name, r.price,
                r.start_date, r.tier_index, r.prize_amount, r.prize_value,
                r.printed, r.claimed,
            )
            for r in rows
        ],
    )
    conn.commit()
    return len(rows)


def games_missing_meta(conn: sqlite3.Connection, game_urls: dict[str, str]) -> dict[str, str]:
    """Subset of game_urls whose total_tickets we haven't successfully cached yet."""
    have = {
        r[0]
        for r in conn.execute(
            "SELECT game_number FROM game_meta WHERE total_tickets IS NOT NULL"
        )
    }
    return {g: u for g, u in game_urls.items() if g not in have}


def enrich_meta(
    conn: sqlite3.Connection,
    game_urls: dict[str, str],
    delay: float = 0.7,
    only_missing: bool = True,
) -> int:
    """Fetch + cache total tickets and overall odds from each game's detail page.

    Static data, so by default only games without cached meta are fetched. Polite:
    sleeps `delay` seconds between requests. Returns the number of games enriched.
    """
    import time

    targets = games_missing_meta(conn, game_urls) if only_missing else game_urls
    enriched = 0
    fetched_at = _dt.datetime.now().isoformat(timespec="seconds")
    for i, (game_number, url) in enumerate(targets.items()):
        try:
            total_tickets, overall_odds = parse_detail_meta(fetch_html(url))
        except Exception:  # noqa: BLE001 - skip a bad page, keep going
            total_tickets, overall_odds = None, None
        conn.execute(
            """
            INSERT OR REPLACE INTO game_meta
                (game_number, total_tickets, overall_odds, detail_url, fetched_at)
            VALUES (?,?,?,?,?)
            """,
            (game_number, total_tickets, overall_odds, url, fetched_at),
        )
        if total_tickets is not None:
            enriched += 1
        if delay and i < len(targets) - 1:
            time.sleep(delay)
    conn.commit()
    return enriched


def scrape_and_store(conn: sqlite3.Connection | None = None) -> tuple[str, int]:
    """Fetch, parse, and store the current snapshot, enriching any new games' meta.

    Returns (snapshot_date, n_rows).
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        html = fetch_html()
        snapshot_date, rows = parse(html)
        n = store(conn, snapshot_date, rows)
        # One-time-per-game detail scrape for total tickets / overall odds.
        enrich_meta(conn, parse_game_urls(html), only_missing=True)
        return snapshot_date, n
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------------------- #
# Queries used by the dashboard
# --------------------------------------------------------------------------- #
def snapshot_dates(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT snapshot_date FROM prizes ORDER BY snapshot_date DESC"
    )]


def last_scraped_at(conn: sqlite3.Connection, date: str | None = None) -> str | None:
    """When the latest snapshot was last fetched (max scraped_at for that date).

    Distinct from the snapshot's own date: the source can keep the same 'as of'
    date for a while, but we still re-check it, so this shows the real freshness.
    """
    if date is None:
        dates = snapshot_dates(conn)
        if not dates:
            return None
        date = dates[0]
    row = conn.execute(
        "SELECT MAX(scraped_at) FROM prizes WHERE snapshot_date = ?", (date,)
    ).fetchone()
    return row[0] if row else None


def grand_prize_overview(conn: sqlite3.Connection, date: str | None = None):
    """Top-tier (grand prize) row per game for the given snapshot (latest by default)."""
    if date is None:
        dates = snapshot_dates(conn)
        if not dates:
            return []
        date = dates[0]
    return conn.execute(
        """
        SELECT game_number, game_name, price, start_date,
               prize_amount, prize_value, printed, claimed,
               (printed - claimed) AS remaining
        FROM prizes
        WHERE snapshot_date = ? AND tier_index = 0
        ORDER BY remaining DESC, prize_value DESC
        """,
        (date,),
    ).fetchall()


def recent_winners(conn: sqlite3.Connection, top_tier_only: bool = True):
    """Games/tiers whose 'claimed' rose between the two most recent snapshots.

    Returns rows with the delta. Empty until at least two snapshots exist.
    """
    dates = snapshot_dates(conn)
    if len(dates) < 2:
        return []
    latest, prev = dates[0], dates[1]
    tier_clause = "AND a.tier_index = 0" if top_tier_only else ""
    return conn.execute(
        f"""
        SELECT a.game_number, a.game_name, a.price, a.tier_index,
               a.prize_amount, a.prize_value,
               b.claimed AS prev_claimed, a.claimed AS now_claimed,
               (a.claimed - b.claimed) AS newly_claimed,
               (a.printed - a.claimed) AS remaining,
               ? AS prev_date, ? AS latest_date
        FROM prizes a
        JOIN prizes b
          ON a.game_number = b.game_number AND a.tier_index = b.tier_index
        WHERE a.snapshot_date = ? AND b.snapshot_date = ?
          AND a.claimed > b.claimed {tier_clause}
        ORDER BY a.prize_value DESC, newly_claimed DESC
        """,
        (prev, latest, latest, prev),
    ).fetchall()


def game_meta_map(conn: sqlite3.Connection) -> dict[str, dict]:
    return {
        r["game_number"]: {"total_tickets": r["total_tickets"], "overall_odds": r["overall_odds"]}
        for r in conn.execute("SELECT game_number, total_tickets, overall_odds FROM game_meta")
    }


def all_tier_totals(conn: sqlite3.Connection, date: str | None = None) -> dict[str, tuple[int, int]]:
    """Per game: (sum printed, sum claimed) across ALL prize tiers for a snapshot."""
    if date is None:
        dates = snapshot_dates(conn)
        if not dates:
            return {}
        date = dates[0]
    return {
        r["game_number"]: (r["printed_total"], r["claimed_total"])
        for r in conn.execute(
            """
            SELECT game_number,
                   SUM(printed) AS printed_total,
                   SUM(claimed) AS claimed_total
            FROM prizes WHERE snapshot_date = ?
            GROUP BY game_number
            """,
            (date,),
        )
    }


def grand_prize_odds(
    total_tickets: int | None,
    grand_printed: int,
    grand_remaining: int,
    tiers_printed: int,
    tiers_claimed: int,
) -> dict:
    """Compute original and (estimated) live odds of winning the grand prize.

    - original = total_tickets / grand_printed                  (exact: 1 in N)
    - live     = est. tickets remaining / grand prizes remaining (ESTIMATE)

    Tickets remaining is not published, so it is estimated by assuming tickets sell
    in proportion to prizes claimed across all tiers:
        est_tickets_remaining = total_tickets * (1 - tiers_claimed / tiers_printed)

    Returns a dict with 'original_odds', 'live_odds' (the N in "1 in N", or None),
    and 'est_tickets_remaining'. None where it can't be computed.
    """
    out = {"original_odds": None, "live_odds": None, "est_tickets_remaining": None}
    if not total_tickets or grand_printed <= 0:
        return out
    out["original_odds"] = total_tickets / grand_printed
    if tiers_printed > 0:
        sold_fraction = min(max(tiers_claimed / tiers_printed, 0.0), 1.0)
        est_remaining = total_tickets * (1 - sold_fraction)
        out["est_tickets_remaining"] = est_remaining
        if grand_remaining > 0:
            out["live_odds"] = est_remaining / grand_remaining
    return out


def game_tiers(conn: sqlite3.Connection, game_number: str, date: str | None = None):
    if date is None:
        dates = snapshot_dates(conn)
        if not dates:
            return []
        date = dates[0]
    return conn.execute(
        """
        SELECT tier_index, prize_amount, prize_value, printed, claimed,
               (printed - claimed) AS remaining
        FROM prizes
        WHERE snapshot_date = ? AND game_number = ?
        ORDER BY tier_index
        """,
        (date, game_number),
    ).fetchall()

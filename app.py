"""Texas Lottery scratch-off dashboard.

Shows, from the daily snapshots the scraper accumulates:
  - how many grand prizes are still out there (per game and overall)
  - which games had a grand prize claimed recently (day-over-day diff)
  - a sortable/filterable table of every active game
  - per-game breakdown of all prize tiers

Run:  .venv/bin/streamlit run app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import lottery

st.set_page_config(page_title="TX Scratch-Off Tracker", page_icon="🎟️", layout="wide")


@st.cache_data(ttl=600)
def load():
    conn = lottery.get_conn()
    try:
        dates = lottery.snapshot_dates(conn)
        overview = [dict(r) for r in lottery.grand_prize_overview(conn)]
        winners = [dict(r) for r in lottery.recent_winners(conn, top_tier_only=True)]
        tier_winners = [dict(r) for r in lottery.recent_winners(conn, top_tier_only=False)]
        meta = lottery.game_meta_map(conn)
        tier_totals = lottery.all_tier_totals(conn)
        return dates, overview, winners, tier_winners, meta, tier_totals
    finally:
        conn.close()


def _fmt_odds(n):
    """Format an odds value N (the N in '1 in N') for display."""
    if n is None:
        return "—"
    return f"1 in {n:,.0f}"


dates, overview, winners, tier_winners, meta, tier_totals = load()

st.title("🎟️ Texas Scratch-Off Tracker")

if not dates:
    st.warning("No data yet. Run `.venv/bin/python scraper.py` to pull the first snapshot.")
    st.stop()

st.caption(
    f"Official prize data as of **{dates[0]}** · {len(dates)} daily snapshot(s) on file · "
    "source: texaslottery.com (updated once daily)"
)

df = pd.DataFrame(overview)
df["pct_remaining"] = (df["remaining"] / df["printed"] * 100).round(1)


def _odds_for(row):
    m = meta.get(row["game_number"], {})
    tp, tc = tier_totals.get(row["game_number"], (0, 0))
    return lottery.grand_prize_odds(
        m.get("total_tickets"), row["printed"], row["remaining"], tp, tc
    )


_odds = df.apply(_odds_for, axis=1)
df["live_odds_n"] = [o["live_odds"] for o in _odds]
df["orig_odds_n"] = [o["original_odds"] for o in _odds]
df["live_odds"] = df["live_odds_n"].map(_fmt_odds)
df["orig_odds"] = df["orig_odds_n"].map(_fmt_odds)


def _edge(row):
    """How much better (or worse) the live grand-prize odds are vs the printed odds.
    >1 means the grand prize is currently MORE likely than at print (under-claimed = value)."""
    o, l = row["orig_odds_n"], row["live_odds_n"]
    if o and l and l > 0:
        return o / l
    return None


df["edge_n"] = df.apply(_edge, axis=1)
df["edge"] = df["edge_n"].map(lambda x: f"{x:.2f}×" if x else "—")

# ---- headline metrics ------------------------------------------------------ #
c1, c2, c3, c4 = st.columns(4)
c1.metric("Active games", len(df))
c2.metric("Grand prizes printed", f"{int(df['printed'].sum()):,}")
c3.metric("Grand prizes claimed", f"{int(df['claimed'].sum()):,}")
c4.metric("Grand prizes still out there", f"{int(df['remaining'].sum()):,}")

st.divider()

# ---- recent winners -------------------------------------------------------- #
st.subheader("🏆 Recently claimed grand prizes")
if len(dates) < 2:
    st.info(
        "Recent-winner tracking compares two days of snapshots. Only one snapshot exists "
        "so far — this fills in automatically after the next daily run."
    )
else:
    if winners:
        wdf = pd.DataFrame(winners)[
            ["game_number", "game_name", "prize_amount", "newly_claimed", "remaining"]
        ].rename(
            columns={
                "game_number": "Game #",
                "game_name": "Game",
                "prize_amount": "Grand prize",
                "newly_claimed": "Claimed since prev. day",
                "remaining": "Still out there",
            }
        )
        st.success(
            f"{len(winners)} game(s) had a grand prize claimed between "
            f"{winners[0]['prev_date']} and {winners[0]['latest_date']}."
        )
        st.dataframe(wdf, hide_index=True, width="stretch")
    else:
        st.write("No grand prizes were claimed between the two most recent snapshots.")

    with st.expander("Show all prize tiers claimed recently (not just grand prizes)"):
        if tier_winners:
            tdf = pd.DataFrame(tier_winners)[
                ["game_number", "game_name", "prize_amount", "newly_claimed", "remaining"]
            ].rename(
                columns={
                    "game_number": "Game #",
                    "game_name": "Game",
                    "prize_amount": "Prize",
                    "newly_claimed": "Claimed since prev. day",
                    "remaining": "Still out there",
                }
            )
            st.dataframe(tdf, hide_index=True, width="stretch")
        else:
            st.write("No prize claims recorded between the two most recent snapshots.")

st.divider()

# ---- best value right now -------------------------------------------------- #
st.subheader("💎 Best value right now")
st.caption(
    "Games where the grand prize is currently **more likely than at print** — i.e. grand "
    "prizes are being claimed slower than tickets are selling. 'Value vs print' is how many "
    "times better the live odds are than the original odds."
)
best = df[df["edge_n"].notna() & (df["remaining"] > 0)].sort_values("edge_n", ascending=False).head(10)
if best.empty:
    st.write("Not enough data yet to rank value.")
else:
    btable = best[
        ["game_number", "game_name", "price", "prize_amount", "remaining", "live_odds", "orig_odds", "edge"]
    ].rename(
        columns={
            "game_number": "Game #",
            "game_name": "Game",
            "price": "Price $",
            "prize_amount": "Grand prize",
            "remaining": "Grand prizes left",
            "live_odds": "Live odds (est.)",
            "orig_odds": "Original odds",
            "edge": "Value vs print",
        }
    )
    st.dataframe(btable, hide_index=True, width="stretch")

st.divider()

# ---- all games ------------------------------------------------------------- #
st.subheader("All active games")
st.caption(
    "**Live odds (est.)** = estimated tickets remaining ÷ grand prizes remaining. "
    "Tickets remaining isn't published, so it's estimated from how many prizes have been "
    "claimed across all tiers. **Original odds** = total tickets ÷ grand prizes printed (exact)."
)

prices = sorted(p for p in df["price"].dropna().unique())
SORTS = {
    "Most grand prizes left": ("remaining", False),
    "Best value vs print": ("edge_n", False),
    "Best live odds (easiest to win)": ("live_odds_n", True),
    "Highest % left": ("pct_remaining", False),
}

left, right = st.columns([1, 3])
with left:
    chosen_prices = st.multiselect(
        "Filter by ticket price ($)",
        options=[int(p) for p in prices],
        default=[int(p) for p in prices],
    )
    sort_by = st.selectbox("Sort by", list(SORTS.keys()))
    only_available = st.checkbox("Only games with grand prizes left", value=True)

view = df[df["price"].isin(chosen_prices)]
if only_available:
    view = view[view["remaining"] > 0]

sort_col, ascending = SORTS[sort_by]
view = view.sort_values(sort_col, ascending=ascending, na_position="last")

# Use the raw numeric columns (live_odds_n / orig_odds_n / edge_n) rather than the
# pre-formatted strings, so clicking a column header sorts numerically instead of
# lexicographically ("1 in 1,000" vs "1 in 9"). Formatting is applied via column_config.
table = view[
    ["game_number", "game_name", "price", "prize_amount", "remaining", "printed",
     "pct_remaining", "live_odds_n", "orig_odds_n", "edge_n"]
].rename(
    columns={
        "game_number": "Game #",
        "game_name": "Game",
        "price": "Price $",
        "prize_amount": "Grand prize",
        "remaining": "Grand prizes left",
        "printed": "Printed",
        "pct_remaining": "% left",
        "live_odds_n": "Live odds (1 in)",
        "orig_odds_n": "Original odds (1 in)",
        "edge_n": "Value vs print",
    }
)
# Round odds to whole numbers so the comma-grouped ("localized") format renders e.g.
# "1,005" rather than "1,005.05"; nullable Int64 keeps missing values blank.
for col in ["Live odds (1 in)", "Original odds (1 in)"]:
    table[col] = table[col].round(0).astype("Int64")

st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    height=460,
    column_config={
        # "localized" adds thousands separators (commas). printf formats (e.g. "1 in %d")
        # can't do grouping, which is why "1 in" lives in the column header instead.
        "Printed": st.column_config.NumberColumn(format="localized"),
        "Grand prizes left": st.column_config.NumberColumn(format="localized"),
        "% left": st.column_config.NumberColumn(format="%.1f%%"),
        "Live odds (1 in)": st.column_config.NumberColumn(
            format="localized",
            help="Estimated current odds of winning the grand prize. Lower = easier to win.",
        ),
        "Original odds (1 in)": st.column_config.NumberColumn(
            format="localized",
            help="Odds of the grand prize at print. Lower = easier to win.",
        ),
        "Value vs print": st.column_config.NumberColumn(format="%.2f×"),
    },
)

# ---- per-game detail ------------------------------------------------------- #
st.divider()
st.subheader("Per-game prize breakdown")
options = {f"#{r['game_number']} — {r['game_name']}": r["game_number"] for r in overview}
label = st.selectbox("Pick a game", options.keys())
if label:
    conn = lottery.get_conn()
    try:
        tiers = [dict(r) for r in lottery.game_tiers(conn, options[label])]
    finally:
        conn.close()
    tdf = pd.DataFrame(tiers)[["prize_amount", "printed", "claimed", "remaining"]].rename(
        columns={"prize_amount": "Prize", "printed": "Printed", "claimed": "Claimed", "remaining": "Remaining"}
    )
    st.dataframe(tdf, hide_index=True, width="stretch")

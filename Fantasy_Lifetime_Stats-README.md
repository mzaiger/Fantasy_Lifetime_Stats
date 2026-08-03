# Fantasy League Stats — Since 2006

A lifetime stats dashboard for a Yahoo Fantasy Baseball league running
since 2006 — every season, every week, every trade, all in one static
page.

## Features (`index.html`)

- Season and weekly rankings, records, and medal counts
- Head-to-head matchup history, including per-league win/loss streak
  tracking
- "Worst trades" scoring, ranking historical trades by outcome
- FAAB (waiver budget) pages
- Manager and season summary pages, power rankings
- Full transaction history pages
- Light/dark mode, pill-style nav with submenus

## Data pipeline

A set of Python scripts against the Yahoo Fantasy Sports API (OAuth2)
pull the underlying history:

| Script | Output | Purpose |
|---|---|---|
| `yahoo_fantasy_ranks.py` | season stats | Season-level rankings across all historical leagues tied to the account |
| `yahoo_fantasy_weekly_stats.py` | `all_seasons_weeks_stats.csv/json` | Per-week stats for every historical season, including calendar days per fantasy week |
| `yahoo_fantasy_trade_ranks.py` | `trade_ranks.csv/json` | Every trade transaction, enriched with each traded player's current rank, preseason rank, ownership %, and the managers on each side |
| `yahoo_fantasy_head_to_head_record.py` | `weekly_head_to_head_record.csv/json` | Weekly head-to-head results |
| `yahoo_fantasy_transactions.py` | `all_seasons_transactions.csv/json` | Full transaction log across all seasons |

Historical league traversal walks Yahoo's `renew` chain to reach every
past season tied to the current league.

## Setup

Requires Yahoo Fantasy API OAuth2 credentials:

```bash
export YAHOO_CLIENT_ID="your_client_id"
export YAHOO_CLIENT_SECRET="your_client_secret"
```

## Running locally

```bash
pip install requests requests-oauthlib

python yahoo_fantasy_ranks.py
python yahoo_fantasy_weekly_stats.py
python yahoo_fantasy_trade_ranks.py
python yahoo_fantasy_head_to_head_record.py
python yahoo_fantasy_transactions.py
```

Then open `index.html` (or serve the folder with
`python -m http.server`) to view the dashboard.

## Structure

```
Fantasy_Lifetime_Stats-main/
├── index.html                             # dashboard front-end
├── yahoo_fantasy_ranks.py
├── yahoo_fantasy_weekly_stats.py
├── yahoo_fantasy_trade_ranks.py
├── yahoo_fantasy_head_to_head_record.py
├── yahoo_fantasy_transactions.py
└── all_seasons_*.csv/json, trade_ranks.*, weekly_head_to_head_record.*  # generated data
```

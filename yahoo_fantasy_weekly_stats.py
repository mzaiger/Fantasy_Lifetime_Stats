"""
Fetches per-week stats for ALL seasons across ALL historical MLB fantasy leagues
tied to the authenticated Yahoo account.

Discovery strategy:
  1. Calls the same /users;use_login=1/games endpoint used in yahoo_fantasy_ranks.py
     to auto-discover every MLB league on the account (all years, not just current).
  2. For each league it determines the week range from the league metadata
     (start_week / end_week / current_week).
  3. For each league × week it fetches /league/{key}/teams;out=stats with
     type=week&week={N} to get per-category weekly totals.

Output files (created/updated in the working directory):
  all_seasons_weeks_stats.json  — nested: { "2024": { "1": [team_rows], … }, … }
  all_seasons_weeks_stats.csv   — flat rows; columns include 'season' and 'week'

Incremental behaviour:
  - Loads existing JSON on startup.
  - Skips any (season, week) pair already present — UNLESS it belongs to the
    current calendar year's current_week (still in progress).
  - This makes re-runs fast: only new / in-progress weeks are fetched.

Credentials (same env-var convention as yahoo_fantasy_ranks.py):
  YAHOO_CLIENT_ID     – Yahoo app client ID
  YAHOO_CLIENT_SECRET – Yahoo app client secret
  YAHOO_TOKEN         – (optional) pre-serialised JSON OAuth token;
                        used by CI/GitHub Actions to skip the browser flow.
"""

import csv
import json
import os
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from requests_oauthlib import OAuth2Session

# ---------------------------------------------------------------------------
# CONFIG  (credentials from environment — never hard-coded)
# ---------------------------------------------------------------------------
CLIENT_ID     = os.getenv("YAHOO_CLIENT_ID")
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET")
REDIRECT_URI      = "https://localhost"
AUTHORIZATION_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL         = "https://api.login.yahoo.com/oauth2/get_token"
TOKEN_CACHE       = Path("token_cache.json")
BASE_URL          = "https://fantasysports.yahooapis.com/fantasy/v2"

# Seconds to sleep between API calls — stays well under Yahoo's rate limit.
API_DELAY = 0.5

# ---------------------------------------------------------------------------
# Stat ID → column label  (identical to yahoo_fantasy_ranks.py)
# ---------------------------------------------------------------------------
STAT_MAP = {
    "7":  "R",
    "12": "HR",
    "13": "RBI",
    "16": "SB",
    "23": "TB",
    "53": "E",
    "4":  "OBP",
    "50": "IP",
    "28": "W",
    "30": "CG",
    "32": "SV",
    "26": "ERA",
    "27": "WHIP",
    "57": "K/9",
    "83": "QS",
}

STAT_COLS = ["R", "HR", "RBI", "SB", "TB", "E", "OBP",
             "IP", "W", "CG", "SV", "ERA", "WHIP", "K/9", "QS"]

OUTPUT_JSON = Path("all_seasons_weeks_stats.json")
OUTPUT_CSV  = Path("all_seasons_weeks_stats.csv")

# ---------------------------------------------------------------------------
# OAuth helpers  (identical pattern to yahoo_fantasy_ranks.py)
# ---------------------------------------------------------------------------
def _save_token(token: dict) -> None:
    TOKEN_CACHE.write_text(json.dumps(token, indent=2))


def _load_token() -> dict | None:
    # Prefer the env var (CI / GitHub Actions workflow)
    token_from_env = os.getenv("YAHOO_TOKEN")
    if token_from_env:
        try:
            return json.loads(token_from_env)
        except Exception:
            pass
    # Fall back to a local cache file (interactive dev)
    if TOKEN_CACHE.exists():
        try:
            return json.loads(TOKEN_CACHE.read_text())
        except Exception:
            pass
    return None


def get_session() -> OAuth2Session:
    session = OAuth2Session(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        auto_refresh_url=TOKEN_URL,
        auto_refresh_kwargs={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        token_updater=_save_token,
    )
    cached = _load_token()
    if cached:
        session.token = cached
        print("Using cached OAuth token.")
    else:
        auth_url, _ = session.authorization_url(AUTHORIZATION_URL)
        webbrowser.open(auth_url)
        print(f"\nIf browser didn't open, visit:\n  {auth_url}\n")
        redirect = input("Paste the full redirect URL (or just the code): ").strip()
        token = (
            session.fetch_token(
                TOKEN_URL, authorization_response=redirect, client_secret=CLIENT_SECRET
            )
            if redirect.startswith("http")
            else session.fetch_token(
                TOKEN_URL, code=redirect, client_secret=CLIENT_SECRET
            )
        )
        _save_token(token)
        print("Token saved.")
    return session


def api_get(session: OAuth2Session, url: str, extra_params: dict | None = None) -> dict:
    params = {"format": "json"}
    if extra_params:
        params.update(extra_params)
    resp = session.get(url, params=params)
    if resp.status_code != 200:
        print(f"  ERROR {resp.status_code}: {resp.text[:300]}")
        return {}
    return resp.json()

# ---------------------------------------------------------------------------
# League discovery — returns ALL MLB leagues on the account across all years
# ---------------------------------------------------------------------------
def get_all_leagues(session: OAuth2Session) -> list[dict]:
    """
    Returns a list of dicts, one per league, each containing:
      season, league_key, league_name
    Sorted oldest → newest so we process history in chronological order.
    """
    print("Discovering all MLB fantasy leagues on this account…")
    url  = f"{BASE_URL}/users;use_login=1/games;game_codes=mlb/leagues"
    data = api_get(session, url)

    try:
        games = (
            data.get("fantasy_content", {})
                .get("users", {})
                .get("0", {})
                .get("user", [{}])[1]
                .get("games", {})
        )
    except (IndexError, KeyError, TypeError):
        raise ValueError("Unexpected API response structure when fetching user games.")

    leagues: list[dict] = []
    for i in range(int(games.get("count", 0))):
        game = games.get(str(i), {}).get("game", [])
        if not game:
            continue

        game_meta = game[0] if isinstance(game[0], dict) else {}
        season    = game_meta.get("season", "unknown")

        league_block = game[1].get("leagues", {}) if len(game) > 1 else {}
        for j in range(int(league_block.get("count", 0))):
            entry       = league_block.get(str(j), {}).get("league", [{}])
            meta        = entry[0] if entry else {}
            league_key  = meta.get("league_key", "")
            league_name = meta.get("name", "?")
            if league_key:
                leagues.append(
                    {"season": season, "league_key": league_key, "league_name": league_name}
                )
                print(f"  {season}  {league_name:<40}  {league_key}")

    leagues.sort(key=lambda x: x["season"])
    print(f"\nFound {len(leagues)} league(s) across {len({l['season'] for l in leagues})} season(s).\n")
    return leagues


# ---------------------------------------------------------------------------
# League metadata — week range
# ---------------------------------------------------------------------------
def get_league_week_info(session: OAuth2Session, league_key: str) -> dict:
    """
    Returns league metadata including:
      season, league_name, start_week, end_week, current_week
    current_week is capped at end_week for completed seasons.
    """
    url  = f"{BASE_URL}/league/{league_key}"
    data = api_get(session, url)
    time.sleep(API_DELAY)

    meta = data.get("fantasy_content", {}).get("league", [{}])[0]

    start_week   = int(meta.get("start_week",   1))
    end_week     = int(meta.get("end_week",     26))
    current_week = int(meta.get("current_week", end_week))
    # For completed seasons Yahoo still reports the last week as current_week;
    # cap it so we never request a week beyond end_week.
    current_week = min(current_week, end_week)

    return {
        "season":       meta.get("season",  "unknown"),
        "league_name":  meta.get("name",    league_key),
        "start_week":   start_week,
        "end_week":     end_week,
        "current_week": current_week,
    }


# ---------------------------------------------------------------------------
# Per-week team stats parser (mirrors parse_teams in yahoo_fantasy_ranks.py)
# ---------------------------------------------------------------------------
def parse_teams_for_week(data: dict, season: str, week: int) -> list[dict]:
    """
    Parses the API response for a single league/week and returns a list of
    team-stat dicts, each with 'season' and 'week' fields.
    """
    league_content = data.get("fantasy_content", {}).get("league", [])
    if len(league_content) < 2:
        return []

    teams_data = league_content[1].get("teams", {})
    count      = int(teams_data.get("count", 0))
    results    = []

    for i in range(count):
        team_entry = teams_data.get(str(i), {}).get("team", [])
        if not team_entry:
            continue

        meta_list = team_entry[0]
        team_info: dict = {"season": season, "week": week}

        # ── identity fields ───────────────────────────────────────────────
        for item in meta_list:
            if not isinstance(item, dict):
                continue
            if "team_key" in item:
                team_info["team_key"] = item["team_key"]
            if "name" in item:
                team_info["team_name"] = item["name"]
            if "managers" in item:
                for m_wrap in item["managers"]:
                    mgr = m_wrap.get("manager", {})
                    team_info["manager_nickname"] = mgr.get("nickname",  "-")
                    team_info["manager_email"]    = mgr.get("email", "Private")

        # ── stats and standings blocks ────────────────────────────────────
        for block in team_entry[1:]:
            if not isinstance(block, dict):
                continue

            if "team_stats" in block:
                for s in block["team_stats"].get("stats", []):
                    stat    = s.get("stat", {})
                    stat_id = str(stat.get("stat_id", ""))
                    if stat_id in STAT_MAP:
                        team_info[STAT_MAP[stat_id]] = stat.get("value", "-")

            if "team_standings" in block:
                ts      = block["team_standings"]
                outcome = ts.get("outcome_totals", {})
                wins    = outcome.get("wins",   "-")
                losses  = outcome.get("losses", "-")
                ties    = outcome.get("ties",   "0")
                try:
                    total   = int(wins) + int(losses) + int(ties)
                    win_pct = f"{int(wins)/total:.3f}" if total > 0 else "-"
                except (ValueError, ZeroDivisionError):
                    win_pct = "-"
                team_info["wins"]         = wins
                team_info["losses"]       = losses
                team_info["ties"]         = ties
                team_info["win_pct"]      = win_pct
                team_info["playoff_seed"] = str(ts.get("playoff_seed", "-"))
                team_info["final_rank"]   = str(ts.get("rank",         "-"))

        for field in ("wins", "losses", "ties", "win_pct", "playoff_seed", "final_rank"):
            team_info.setdefault(field, "-")

        results.append(team_info)

    return results


# ---------------------------------------------------------------------------
# CSV writer — rewrites the entire file from the in-memory dict
# ---------------------------------------------------------------------------
def write_csv(all_data: dict) -> None:
    record_cols = ["wins", "losses", "ties", "win_pct", "playoff_seed", "final_rank"]
    fieldnames  = (
        ["season", "week", "team_key", "team_name", "manager_nickname", "manager_email"]
        + record_cols
        + STAT_COLS
    )
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for season in sorted(all_data.keys(), reverse=True):
            for week_str in sorted(all_data[season].keys(), key=lambda x: int(x)):
                for row in all_data[season][week_str]:
                    writer.writerow({k: row.get(k, "-") for k in fieldnames})
    print(f"  → Saved CSV : {OUTPUT_CSV}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def fetch_all_weeks() -> None:
    current_year = str(datetime.now().year)
    session      = get_session()

    # ── Load existing data ─────────────────────────────────────────────────
    if OUTPUT_JSON.exists():
        with open(OUTPUT_JSON, "r", encoding="utf-8") as f:
            all_data: dict = json.load(f)
        print(f"Loaded existing JSON — seasons present: {sorted(all_data.keys())}\n")
    else:
        all_data = {}
        print("No existing JSON found — starting fresh.\n")

    # ── Discover all leagues on the account ───────────────────────────────
    leagues = get_all_leagues(session)
    if not leagues:
        print("No leagues found. Exiting.")
        return

    total_weeks_fetched = 0

    for league in leagues:
        league_key  = league["league_key"]
        league_name = league["league_name"]

        # ── Get week range for this league ─────────────────────────────────
        info         = get_league_week_info(session, league_key)
        season       = info["season"]
        start_week   = info["start_week"]
        last_week    = info["current_week"]   # for current year = week in progress
        end_week     = info["end_week"]       # hard cap

        print(f"── {season}  {league_name}  ({league_key})")
        print(f"   weeks {start_week}–{last_week}  (season end_week={end_week})")

        all_data.setdefault(season, {})

        for week in range(start_week, last_week + 1):
            week_str = str(week)

            # Skip already-fetched weeks unless it's the current season's
            # current week (stats may still be updating).
            already_have = week_str in all_data[season]
            is_live_week = (season == current_year and week == last_week)

            if already_have and not is_live_week:
                print(f"   week {week:>2}  [skip — already cached]")
                continue

            # Fetch weekly stats from the API
            url  = f"{BASE_URL}/league/{league_key}/teams;out=stats,standings"
            data = api_get(session, url, extra_params={"type": "week", "week": week})
            time.sleep(API_DELAY)

            teams = parse_teams_for_week(data, season, week)

            if teams:
                all_data[season][week_str] = teams
                total_weeks_fetched += 1
                tag = "[refreshed]" if already_have else "[fetched]"
                print(f"   week {week:>2}  {tag}  {len(teams)} teams")
            else:
                print(f"   week {week:>2}  WARNING — no data returned, skipping")

        # ── Checkpoint: save JSON after every league ───────────────────────
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(all_data, f, indent=2, ensure_ascii=False)
        print(f"   Checkpoint saved → {OUTPUT_JSON}\n")

    # ── Final CSV write ────────────────────────────────────────────────────
    write_csv(all_data)

    season_count = len(all_data)
    week_count   = sum(len(v) for v in all_data.values())
    print(f"\nDone — {season_count} season(s), {week_count} total week entries, "
          f"{total_weeks_fetched} week(s) fetched this run.")


if __name__ == "__main__":
    fetch_all_weeks()

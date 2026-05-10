"""
Fetches per-week stats for ALL seasons across ALL historical MLB fantasy leagues
tied to the authenticated Yahoo account. Corrected to handle Yahoo's matrix 
parameter requirements for weekly stat filtering.
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
# CONFIG
# ---------------------------------------------------------------------------
CLIENT_ID     = os.getenv("YAHOO_CLIENT_ID")
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET")
REDIRECT_URI      = "https://localhost"
AUTHORIZATION_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL         = "https://api.login.yahoo.com/oauth2/get_token"
TOKEN_CACHE       = Path("token_cache.json")
BASE_URL          = "https://fantasysports.yahooapis.com/fantasy/v2"

API_DELAY = 0.5

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
# OAuth helpers
# ---------------------------------------------------------------------------
def _save_token(token: dict) -> None:
    TOKEN_CACHE.write_text(json.dumps(token, indent=2))

def _load_token() -> dict | None:
    token_from_env = os.getenv("YAHOO_TOKEN")
    if token_from_env:
        try:
            return json.loads(token_from_env)
        except Exception:
            pass
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

def api_get(session: OAuth2Session, url: str) -> dict:
    # We use format=json as a standard query param, but week filters MUST be matrix params in the URL string
    resp = session.get(url, params={"format": "json"})
    if resp.status_code != 200:
        print(f"  ERROR {resp.status_code}: {resp.text[:300]}")
        return {}
    return resp.json()

# ---------------------------------------------------------------------------
# League discovery
# ---------------------------------------------------------------------------
def get_all_leagues(session: OAuth2Session) -> list[dict]:
    print("Discovering all MLB fantasy leagues on this account...")
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
        raise ValueError("Unexpected API response structure.")

    leagues: list[dict] = []
    for i in range(int(games.get("count", 0))):
        game = games.get(str(i), {}).get("game", [])
        if not game: continue

        game_meta = game[0] if isinstance(game[0], dict) else {}
        season    = game_meta.get("season", "unknown")

        league_block = game[1].get("leagues", {}) if len(game) > 1 else {}
        for j in range(int(league_block.get("count", 0))):
            entry       = league_block.get(str(j), {}).get("league", [{}])
            meta        = entry[0] if entry else {}
            league_key  = meta.get("league_key", "")
            league_name = meta.get("name", "?")
            if league_key:
                leagues.append({"season": season, "league_key": league_key, "league_name": league_name})

    leagues.sort(key=lambda x: x["season"])
    return leagues

def get_league_week_info(session: OAuth2Session, league_key: str) -> dict:
    url  = f"{BASE_URL}/league/{league_key}"
    data = api_get(session, url)
    time.sleep(API_DELAY)

    meta = data.get("fantasy_content", {}).get("league", [{}])[0]
    start_week   = int(meta.get("start_week",   1))
    end_week     = int(meta.get("end_week",     26))
    current_week = int(meta.get("current_week", end_week))
    current_week = min(current_week, end_week)

    return {
        "season":       meta.get("season",  "unknown"),
        "league_name":  meta.get("name",    league_key),
        "start_week":   start_week,
        "end_week":     end_week,
        "current_week": current_week,
    }

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def parse_teams_for_week(data: dict, season: str, week: int) -> list[dict]:
    league_content = data.get("fantasy_content", {}).get("league", [])
    if len(league_content) < 2:
        return []

    teams_data = league_content[1].get("teams", {})
    count      = int(teams_data.get("count", 0))
    results    = []

    for i in range(count):
        team_entry = teams_data.get(str(i), {}).get("team", [])
        if not team_entry: continue

        meta_list = team_entry[0]
        team_info: dict = {"season": season, "week": week}

        for item in meta_list:
            if not isinstance(item, dict): continue
            if "team_key" in item: team_info["team_key"] = item["team_key"]
            if "name" in item: team_info["team_name"] = item["name"]
            if "managers" in item:
                for m_wrap in item["managers"]:
                    mgr = m_wrap.get("manager", {})
                    team_info["manager_nickname"] = mgr.get("nickname", "-")
                    team_info["manager_email"] = mgr.get("email", "Private")

        for block in team_entry[1:]:
            if not isinstance(block, dict): continue
            if "team_stats" in block:
                for s in block["team_stats"].get("stats", []):
                    stat = s.get("stat", {})
                    stat_id = str(stat.get("stat_id", ""))
                    if stat_id in STAT_MAP:
                        team_info[STAT_MAP[stat_id]] = stat.get("value", "-")

            if "team_standings" in block:
                ts = block["team_standings"]
                outcome = ts.get("outcome_totals", {})
                team_info["wins"]   = outcome.get("wins", "-")
                team_info["losses"] = outcome.get("losses", "-")
                team_info["ties"]   = outcome.get("ties", "0")
                team_info["playoff_seed"] = str(ts.get("playoff_seed", "-"))
                team_info["final_rank"]   = str(ts.get("rank", "-"))

        results.append(team_info)
    return results

def write_csv(all_data: dict) -> None:
    record_cols = ["wins", "losses", "ties", "playoff_seed", "final_rank"]
    fieldnames  = ["season", "week", "team_key", "team_name", "manager_nickname", "manager_email"] + record_cols + STAT_COLS
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for season in sorted(all_data.keys(), reverse=True):
            for week_str in sorted(all_data[season].keys(), key=int):
                for row in all_data[season][week_str]:
                    writer.writerow({k: row.get(k, "-") for k in fieldnames})

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def fetch_all_weeks() -> None:
    current_year = str(datetime.now().year)
    session      = get_session()

    if OUTPUT_JSON.exists():
        with open(OUTPUT_JSON, "r", encoding="utf-8") as f:
            all_data = json.load(f)
    else:
        all_data = {}

    leagues = get_all_leagues(session)
    total_weeks_fetched = 0

    for league in leagues:
        lkey, lname = league["league_key"], league["league_name"]
        info = get_league_week_info(session, lkey)
        season, start, last = info["season"], info["start_week"], info["current_week"]

        print(f"\n>> Processing {season}: {lname}")
        all_data.setdefault(season, {})

        for week in range(start, last + 1):
            week_str = str(week)
            is_live_week = (season == current_year and week == last)

            if week_str in all_data[season] and not is_live_week:
                continue

            # CRITICAL FIX: Matrix parameters (;type=week;week=N)
            url = f"{BASE_URL}/league/{lkey}/teams;out=stats,standings;type=week;week={week}"
            data = api_get(session, url)
            time.sleep(API_DELAY)

            teams = parse_teams_for_week(data, season, week)
            if teams:
                all_data[season][week_str] = teams
                total_weeks_fetched += 1
                print(f"   Week {week:>2}: Fetched {len(teams)} teams")

        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(all_data, f, indent=2, ensure_ascii=False)

    write_csv(all_data)
    print(f"\nDone. Fetched {total_weeks_fetched} new/updated weeks.")

if __name__ == "__main__":
    fetch_all_weeks()

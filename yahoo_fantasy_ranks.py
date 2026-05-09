"""
Fetches season stats, W/L/T records, and playoff placement for the CURRENT
season only, then merges the result into all_seasons_stats.json.

Behavior:
  - Loads all_seasons_stats.json if it exists (preserves all historical seasons)
  - Removes only the current season's entry from the loaded data
  - Re-fetches the current season from the Yahoo Fantasy API
  - Writes the updated data back to all_seasons_stats.json and all_seasons_stats.csv

Old seasons are NEVER deleted — only the current season is refreshed.
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

# ---------------------------------------------------------------------------
# Stat ID → label map
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

# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------
def _save_token(token): TOKEN_CACHE.write_text(json.dumps(token, indent=2))

def _load_token():
    # Check for token in GitHub Secrets (or any env var) first
    token_from_env = os.getenv("YAHOO_TOKEN")
    if token_from_env:
        try:
            return json.loads(token_from_env)
        except Exception:
            pass
    # Fallback to local file for testing
    if TOKEN_CACHE.exists():
        try:
            return json.loads(TOKEN_CACHE.read_text())
        except Exception:
            pass
    return None

def get_session() -> OAuth2Session:
    session = OAuth2Session(
        client_id=CLIENT_ID, redirect_uri=REDIRECT_URI,
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
        print(f"\nIf browser didn't open:\n  {auth_url}\n")
        redirect = input("Paste the full redirect URL: ").strip()
        token = (
            session.fetch_token(TOKEN_URL, authorization_response=redirect, client_secret=CLIENT_SECRET)
            if redirect.startswith("http")
            else session.fetch_token(TOKEN_URL, code=redirect, client_secret=CLIENT_SECRET)
        )
        _save_token(token)
        print("Token saved.")
    return session

def api_get(session: OAuth2Session, url: str) -> dict:
    resp = session.get(url, params={"format": "json"})
    if resp.status_code != 200:
        print(f"  ERROR {resp.status_code}: {resp.text[:300]}")
        return {}
    return resp.json()

# ---------------------------------------------------------------------------
# Auto-discover the current season's league key from the user's account
# ---------------------------------------------------------------------------
def get_current_league_key(session: OAuth2Session) -> str:
    """
    Queries the authenticated user's MLB fantasy leagues and returns the
    league key whose season matches the current calendar year.
    Raises ValueError if no matching league is found.
    """
    current_year = str(datetime.now().year)
    print(f"Searching for MLB fantasy league for season {current_year}...")

    url  = f"{BASE_URL}/users;use_login=1/games;game_codes=mlb/leagues"
    data = api_get(session, url)

    try:
        games = (data.get("fantasy_content", {})
                     .get("users", {})
                     .get("0", {})
                     .get("user", [{}])[1]
                     .get("games", {}))
    except (IndexError, KeyError, TypeError):
        raise ValueError("Unexpected API response structure when fetching user games.")

    for i in range(int(games.get("count", 0))):
        game = games.get(str(i), {}).get("game", [])
        if not game:
            continue

        game_meta = game[0] if isinstance(game[0], dict) else {}
        if game_meta.get("season") != current_year:
            continue

        leagues = game[1].get("leagues", {}) if len(game) > 1 else {}
        for j in range(int(leagues.get("count", 0))):
            league      = leagues.get(str(j), {}).get("league", [{}])
            league_meta = league[0] if league else {}
            league_key  = league_meta.get("league_key", "")
            league_name = league_meta.get("name", "?")
            if league_key:
                print(f"  Found: {league_name} → {league_key}")
                return league_key

    raise ValueError(
        f"No MLB fantasy league found for season {current_year}. "
        "Make sure your Yahoo account has an active league this year."
    )

# ---------------------------------------------------------------------------
# Parse one season's teams response.
# ---------------------------------------------------------------------------
def parse_teams(data: dict, season: str) -> list[dict]:
    league_content = data.get("fantasy_content", {}).get("league", [])
    if len(league_content) < 2:
        print(f"  [parse] WARNING: league_content only has {len(league_content)} element(s)")
        return []

    teams_data = league_content[1].get("teams", {})
    count      = int(teams_data.get("count", 0))
    results    = []

    for i in range(count):
        team_entry = teams_data.get(str(i), {}).get("team", [])
        if not team_entry:
            continue

        meta_list = team_entry[0]
        team_info = {"season": season}

        # ── metadata ──────────────────────────────────────────────────────
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
                    team_info["manager_nickname"] = mgr.get("nickname", "-")
                    team_info["manager_email"]    = mgr.get("email", "Private")

        # ── scan every element after [0] for stats and standings ──────────
        for block in team_entry[1:]:
            if not isinstance(block, dict):
                continue

            # --- stats ---
            if "team_stats" in block:
                for s in block["team_stats"].get("stats", []):
                    stat    = s.get("stat", {})
                    stat_id = str(stat.get("stat_id", ""))
                    if stat_id in STAT_MAP:
                        team_info[STAT_MAP[stat_id]] = stat.get("value", "-")

            # --- standings ---
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

        # default standings fields if block wasn't found
        for field in ("wins", "losses", "ties", "win_pct", "playoff_seed", "final_rank"):
            team_info.setdefault(field, "-")

        results.append(team_info)

    return results

# ---------------------------------------------------------------------------
# Main — fetch ONLY the current season, merge into existing JSON
# ---------------------------------------------------------------------------
def fetch_current_season() -> None:
    session = get_session()

    # ── Step 1: load existing JSON (all historical seasons) ───────────────
    json_file = Path("all_seasons_stats.json")
    if json_file.exists():
        with open(json_file, "r", encoding="utf-8") as f:
            all_data = json.load(f)
        print(f"Loaded existing JSON with seasons: {sorted(all_data.keys())}")
    else:
        all_data = {}
        print("No existing JSON found — starting fresh.")

    # ── Step 2: auto-discover the current season's league key ─────────────
    league_key = get_current_league_key(session)
    time.sleep(0.4)

    # ── Step 3: fetch league metadata to confirm season year + name ───────
    meta_url  = f"{BASE_URL}/league/{league_key}"
    meta_data = api_get(session, meta_url)

    league_list = meta_data.get("fantasy_content", {}).get("league", [{}])
    league_meta = league_list[0] if league_list else {}

    season      = league_meta.get("season", "unknown")
    league_name = league_meta.get("name", league_key)

    print(f"\n── Current Season: {season}  |  {league_name}  ({league_key}) ──")

    # ── Step 4: drop only the current season from the loaded data ─────────
    if season in all_data:
        print(f"Removing existing '{season}' entry from JSON (will be replaced with fresh data).")
        del all_data[season]

    # ── Step 5: fetch fresh stats + standings for the current season ──────
    combined_url  = f"{BASE_URL}/league/{league_key}/teams;out=stats,standings"
    combined_data = api_get(session, combined_url)
    time.sleep(0.4)

    teams = parse_teams(combined_data, season)

    if teams:
        all_data[season] = teams
        print(f"\nFetched {len(teams)} teams for season {season}:")
        for t in teams:
            print(f"  + {t.get('team_name','?'):<32} ({t.get('manager_nickname','?'):<20})"
                  f"  {t.get('wins','-')}W-{t.get('losses','-')}L"
                  f"  seed={t.get('playoff_seed','-')}"
                  f"  final={t.get('final_rank','-')}")
    else:
        print(f"  WARNING: No team data returned for season {season}. JSON not updated for this season.")

    # ── Step 6: save merged JSON (all old seasons + refreshed current) ────
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)
    print(f"\nSaved JSON → {json_file}  (seasons in file: {sorted(all_data.keys())})")

    # ── Step 7: rewrite CSV with all seasons ──────────────────────────────
    csv_file    = Path("all_seasons_stats.csv")
    record_cols = ["wins", "losses", "ties", "win_pct", "playoff_seed", "final_rank"]
    fieldnames  = ["season", "team_key", "team_name", "manager_nickname", "manager_email"] \
                + record_cols + STAT_COLS

    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for yr in sorted(all_data.keys(), reverse=True):
            for row in all_data[yr]:
                writer.writerow({k: row.get(k, "-") for k in fieldnames})

    print(f"Saved CSV  → {csv_file}")
    print(f"\nDone — {len(all_data)} total seasons in file.")


if __name__ == "__main__":
    fetch_current_season()

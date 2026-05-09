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

# Expanded STAT_MAP to catch more common Yahoo MLB IDs
STAT_MAP = {
    "7":  "R", "12": "HR", "13": "RBI", "16": "SB", "23": "TB", "53": "E", "4":  "OBP", "3": "AVG",
    "50": "IP", "28": "W", "30": "CG", "32": "SV", "26": "ERA", "27": "WHIP", "57": "K/9", "83": "QS", "31": "K"
}

STAT_COLS = ["R", "HR", "RBI", "SB", "TB", "E", "OBP", "AVG", "IP", "W", "CG", "SV", "ERA", "WHIP", "K/9", "QS", "K"]

OUTPUT_JSON = Path("all_seasons_weeks_stats.json")
OUTPUT_CSV  = Path("all_seasons_weeks_stats.csv")

# ---------------------------------------------------------------------------
# OAuth & API Helpers
# ---------------------------------------------------------------------------
def _save_token(token): TOKEN_CACHE.write_text(json.dumps(token, indent=2))
def _load_token():
    env_t = os.getenv("YAHOO_TOKEN")
    if env_t: return json.loads(env_t)
    return json.loads(TOKEN_CACHE.read_text()) if TOKEN_CACHE.exists() else None

def get_session():
    session = OAuth2Session(CLIENT_ID, redirect_uri=REDIRECT_URI, auto_refresh_url=TOKEN_URL,
                            auto_refresh_kwargs={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
                            token_updater=_save_token)
    cached = _load_token()
    if cached: session.token = cached
    else:
        auth_url, _ = session.authorization_url(AUTHORIZATION_URL)
        webbrowser.open(auth_url)
        redirect = input("Paste redirect URL: ").strip()
        session.token = session.fetch_token(TOKEN_URL, authorization_response=redirect, client_secret=CLIENT_SECRET) if "code=" in redirect else session.fetch_token(TOKEN_URL, code=redirect, client_secret=CLIENT_SECRET)
        _save_token(session.token)
    return session

def api_get(session, url):
    resp = session.get(url, params={"format": "json"})
    return resp.json() if resp.status_code == 200 else {}

# ---------------------------------------------------------------------------
# Data Processing
# ---------------------------------------------------------------------------
def get_all_leagues(session):
    url = f"{BASE_URL}/users;use_login=1/games;game_codes=mlb/leagues"
    data = api_get(session, url)
    leagues = []
    try:
        games = data["fantasy_content"]["users"]["0"]["user"][1]["games"]
        for i in range(int(games["count"])):
            g = games[str(i)]["game"]
            season = g[0]["season"]
            l_block = g[1]["leagues"]
            for j in range(int(l_block["count"])):
                m = l_block[str(j)]["league"][0]
                leagues.append({"season": season, "key": m["league_key"], "name": m["name"]})
    except: pass
    return sorted(leagues, key=lambda x: x["season"])

def parse_teams_for_week(data, season, week):
    try:
        teams_data = data["fantasy_content"]["league"][1]["teams"]
    except (KeyError, IndexError): return []
    
    results = []
    for i in range(int(teams_data["count"])):
        t_entry = teams_data[str(i)]["team"]
        team_info = {"season": season, "week": week}
        
        # Identity
        for item in t_entry[0]:
            if "team_key" in item: team_info["team_key"] = item["team_key"]
            if "name" in item: team_info["team_name"] = item["name"]

        # Stats
        for block in t_entry[1:]:
            if "team_stats" in block:
                for s in block["team_stats"].get("stats", []):
                    sid = str(s["stat"]["stat_id"])
                    val = s["stat"]["value"]
                    col = STAT_MAP.get(sid, f"stat_{sid}") # Fallback so no empty spots
                    team_info[col] = val
            if "team_standings" in block:
                ts = block["team_standings"]
                team_info["wins"] = ts.get("outcome_totals", {}).get("wins", "-")
                team_info["losses"] = ts.get("outcome_totals", {}).get("losses", "-")
        results.append(team_info)
    return results

def fetch_all_weeks():
    session = get_session()
    # DELETE YOUR JSON IF IT HAS WRONG DATA
    all_data = json.loads(OUTPUT_JSON.read_text()) if OUTPUT_JSON.exists() else {}
    
    leagues = get_all_leagues(session)
    for league in leagues:
        lkey = league["key"]
        print(f"Checking {league['season']}: {league['name']}")
        
        l_data = api_get(session, f"{BASE_URL}/league/{lkey}")
        meta = l_data["fantasy_content"]["league"][0]
        curr_w = min(int(meta["current_week"]), int(meta["end_week"]))
        
        all_data.setdefault(league["season"], {})
        
        for w in range(int(meta["start_week"]), curr_w + 1):
            w_str = str(w)
            # Only skip if we already have it and it's not the live week
            if w_str in all_data[league["season"]] and not (league["season"] == str(datetime.now().year) and w == curr_w):
                continue

            # THE FIX: Matrix parameters (;) for the stats sub-resource
            url = f"{BASE_URL}/league/{lkey}/teams;out=stats,standings;type=week;week={w}"
            raw = api_get(session, url)
            time.sleep(API_DELAY)
            
            teams = parse_teams_for_week(raw, league["season"], w)
            if teams:
                all_data[league["season"]][w_str] = teams
                print(f"  Week {w} [Weekly Stats Fetched]")

        with open(OUTPUT_JSON, "w") as f: json.dump(all_data, f, indent=2)

    # Write CSV
    if all_data:
        all_rows = []
        for s in all_data:
            for w in all_data[s]: all_rows.extend(all_data[s][w])
        if all_rows:
            keys = all_rows[0].keys()
            with open(OUTPUT_CSV, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(all_rows)

if __name__ == "__main__":
    fetch_all_weeks()
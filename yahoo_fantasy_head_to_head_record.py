"""
yahoo_fantasy_weekly_head_to_head_record.py

Fetches weekly Yahoo Fantasy Baseball head-to-head matchup results for all
historical MLB leagues tied to the authenticated Yahoo account.

Outputs:
    weekly_head_to_head_record.json
    weekly_head_to_head_record.csv
"""

import csv
import json
import os
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from requests_oauthlib import OAuth2Session

CLIENT_ID = os.getenv("YAHOO_CLIENT_ID")
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET")

REDIRECT_URI = "https://localhost"
AUTHORIZATION_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
TOKEN_CACHE = Path("token_cache.json")

BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"
API_DELAY = 0.5

OUTPUT_JSON = Path("weekly_head_to_head_record.json")
OUTPUT_CSV = Path("weekly_head_to_head_record.csv")


def _save_token(token):
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


def get_session():
    session = OAuth2Session(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        auto_refresh_url=TOKEN_URL,
        auto_refresh_kwargs={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET
        },
        token_updater=_save_token,
    )

    cached = _load_token()

    if cached:
        session.token = cached
    else:
        auth_url, _ = session.authorization_url(AUTHORIZATION_URL)
        webbrowser.open(auth_url)

        print(f"Open:\n{auth_url}\n", flush=True)
        redirect = input("Paste redirect URL or code: ").strip()

        if redirect.startswith("http"):
            token = session.fetch_token(
                TOKEN_URL,
                authorization_response=redirect,
                client_secret=CLIENT_SECRET,
                timeout=10
            )
        else:
            token = session.fetch_token(
                TOKEN_URL,
                code=redirect,
                client_secret=CLIENT_SECRET,
                timeout=10
            )

        _save_token(token)

    return session


def api_get(session, url):
    try:
        r = session.get(url, params={"format": "json"}, timeout=10)
    except Exception as e:
        print(f"\nERROR: Request timed out or failed: {e}", flush=True)
        return {}

    if r.status_code != 200:
        print(f"\nERROR {r.status_code}: {r.text[:300]}", flush=True)
        return {}

    return r.json()


def find_key_recursive(data, target_key):
    """Safely traverses Yahoo's mixed list/dict JSON format to find a target key."""
    if isinstance(data, dict):
        if target_key in data:
            return data[target_key]
        for value in data.values():
            res = find_key_recursive(value, target_key)
            if res is not None:
                return res
    elif isinstance(data, list):
        for item in data:
            res = find_key_recursive(item, target_key)
            if res is not None:
                return res
    return None


def extract_team_data(team_obj):
    """Recursively crawls the team object to gather metadata and weekly scores."""
    info = {
        "name": "",
        "manager": "",
        "points": "",
        "outcome": ""
    }
    
    def walk(data):
        if isinstance(data, dict):
            if "name" in data:
                info["name"] = data["name"]
            if "nickname" in data:
                info["manager"] = data["nickname"]
            if "total" in data:
                info["points"] = str(data["total"])
            if "outcome" in data:
                info["outcome"] = str(data["outcome"])
            
            for v in data.values():
                walk(v)
        elif isinstance(data, list):
            for item in data:
                walk(item)
                
    walk(team_obj)
    return info


def get_all_leagues(session):
    print("Fetching all historical MLB leagues...", flush=True)
    url = f"{BASE_URL}/users;use_login=1/games;game_codes=mlb/leagues"
    data = api_get(session, url)

    games = (
        data.get("fantasy_content", {})
        .get("users", {})
        .get("0", {})
        .get("user", [{}])[1]
        .get("games", {})
    )

    leagues = []

    for i in range(int(games.get("count", 0))):
        game = games.get(str(i), {}).get("game", [])
        if not game:
            continue

        game_meta = game[0]

        season = game_meta.get("season")
        game_key = game_meta.get("game_key")

        leagues_block = game[1].get("leagues", {})

        for j in range(int(leagues_block.get("count", 0))):
            league = leagues_block.get(str(j), {}).get("league", [{}])[0]

            leagues.append({
                "season": season,
                "game_key": game_key,
                "league_key": league.get("league_key"),
                "league_name": league.get("name"),
            })

    leagues.sort(key=lambda x: x["season"])
    print(f"Found {len(leagues)} leagues to process.\n", flush=True)
    return leagues


def parse_matchups(data, season, league_name, league_key, week):
    results = []

    matchups_block = find_key_recursive(data, "matchups")
    if not matchups_block:
        return results

    matchup_count = int(matchups_block.get("count", 0))

    for idx in range(matchup_count):
        matchup = matchups_block.get(str(idx), {}).get("matchup")
        if not matchup:
            continue

        teams_block = find_key_recursive(matchup, "teams")
        if not teams_block:
            continue

        team_a_obj = teams_block.get("0", {}).get("team", [])
        team_b_obj = teams_block.get("1", {}).get("team", [])

        team_a_info = extract_team_data(team_a_obj)
        team_b_info = extract_team_data(team_b_obj)

        # Construct highly readable weekly matchup record lines based on league types
        rec_a = team_a_info["points"]
        if team_a_info["outcome"]:
            rec_a = f'{rec_a} ({team_a_info["outcome"].capitalize()})' if rec_a else team_a_info["outcome"].capitalize()
        if not rec_a: 
            rec_a = "0"

        rec_b = team_b_info["points"]
        if team_b_info["outcome"]:
            rec_b = f'{rec_b} ({team_b_info["outcome"].capitalize()})' if rec_b else team_b_info["outcome"].capitalize()
        if not rec_b: 
            rec_b = "0"

        results.append({
            "season": season,
            "week": week,
            "league_key": league_key,
            "league_name": league_name,

            "team_a_name": team_a_info["name"],
            "team_a_manager": team_a_info["manager"],
            "team_a_record": rec_a,

            "team_b_name": team_b["team_name"],
            "team_b_manager": team_b["manager"],
            "team_b_record": rec_b,
        })

    return results


def write_csv(rows):
    fields = [
        "season",
        "week",
        "league_key",
        "league_name",
        "team_a_name",
        "team_a_manager",
        "team_a_record",
        "team_b_name",
        "team_b_manager",
        "team_b_record",
    ]

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for row in rows:
            writer.writerow(row)


def main():
    session = get_session()

    all_rows = []

    leagues = get_all_leagues(session)

    for league in leagues:
        print(f'Processing {league["season"]}: {league["league_name"]}', flush=True)

        scoreboard_url = (
            f'{BASE_URL}/league/{league["league_key"]}'
        )

        meta = api_get(session, scoreboard_url)
        time.sleep(API_DELAY)

        league_meta = find_key_recursive(meta, "league")
        if isinstance(league_meta, list):
            league_meta = league_meta[0]
        elif not league_meta:
            league_meta = {}

        start_week = int(league_meta.get("start_week", 1))
        end_week = int(league_meta.get("current_week", league_meta.get("end_week", 1)))

        for week in range(start_week, end_week + 1):
            print(f'  -> Fetching Week {week}/{end_week}... ', end='', flush=True)

            url = (
                f'{BASE_URL}/league/{league["league_key"]}'
                f'/scoreboard;week={week};out=matchups'
            )

            data = api_get(session, url)
            time.sleep(API_DELAY)

            rows = parse_matchups(
                data=data,
                season=league["season"],
                league_name=league["league_name"],
                league_key=league["league_key"],
                week=week,
            )

            all_rows.extend(rows)
            print("Done", flush=True)

        print(f'Finished {league["season"]} league.\n', flush=True)

    OUTPUT_JSON.write_text(
        json.dumps(all_rows, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    write_csv(all_rows)

    print(f"Saved {len(all_rows)} matchups total.", flush=True)


if __name__ == "__main__":
    main()

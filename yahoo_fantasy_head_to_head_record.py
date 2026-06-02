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

        print(f"Open:\n{auth_url}\n")
        redirect = input("Paste redirect URL or code: ").strip()

        if redirect.startswith("http"):
            token = session.fetch_token(
                TOKEN_URL,
                authorization_response=redirect,
                client_secret=CLIENT_SECRET,
            )
        else:
            token = session.fetch_token(
                TOKEN_URL,
                code=redirect,
                client_secret=CLIENT_SECRET,
            )

        _save_token(token)

    return session


def api_get(session, url):
    r = session.get(url, params={"format": "json"})

    if r.status_code != 200:
        print(f"ERROR {r.status_code}: {r.text[:300]}")
        return {}

    return r.json()


def get_all_leagues(session):
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
    return leagues


def get_week_days(matchup_meta):
    try:
        start = datetime.strptime(matchup_meta["week_start"], "%Y-%m-%d").date()
        end = datetime.strptime(matchup_meta["week_end"], "%Y-%m-%d").date()
        return (end - start).days + 1
    except Exception:
        return 7


def extract_team(team_obj):
    result = {
        "team_key": "",
        "team_name": "",
        "manager": "",
        "wins": 0,
        "losses": 0,
        "ties": 0,
    }

    if isinstance(team_obj, list):
        for item in team_obj:
            if not isinstance(item, dict):
                continue

            if "team_key" in item:
                result["team_key"] = item["team_key"]

            if "name" in item:
                result["team_name"] = item["name"]

            if "managers" in item:
                for mgr_wrap in item["managers"]:
                    mgr = mgr_wrap.get("manager", {})
                    result["manager"] = mgr.get("nickname", "")

            if "team_standings" in item:
                totals = item["team_standings"].get("outcome_totals", {})
                result["wins"] = totals.get("wins", 0)
                result["losses"] = totals.get("losses", 0)
                result["ties"] = totals.get("ties", 0)

    return result


def parse_matchups(data, season, league_name, league_key, week):
    results = []

    league = data.get("fantasy_content", {}).get("league", [])
    if len(league) < 2:
        return results

    scoreboard = league[1].get("scoreboard", {})
    matchups = scoreboard.get("0", {}).get("matchups") or scoreboard.get("matchups", {})

    matchup_count = int(matchups.get("count", 0))

    for idx in range(matchup_count):
        matchup = matchups.get(str(idx), {}).get("matchup", {})

        matchup_meta = matchup[0] if isinstance(matchup, list) else matchup

        week_days = get_week_days(matchup_meta)

        teams_block = matchup_meta.get("teams", {})

        team_a = extract_team(
            teams_block.get("0", {}).get("team", [])
        )

        team_b = extract_team(
            teams_block.get("1", {}).get("team", [])
        )

        team_a_categories = []
        team_b_categories = []

        stat_winners = matchup_meta.get("stat_winners", {})

        for s in stat_winners.get("stats", []):
            stat = s.get("stat", {})

            stat_name = stat.get("display_name") or stat.get("name")
            winner_team_key = stat.get("winner_team_key")

            if not stat_name:
                continue

            if winner_team_key == team_a["team_key"]:
                team_a_categories.append(stat_name)
            elif winner_team_key == team_b["team_key"]:
                team_b_categories.append(stat_name)

        results.append({
            "season": season,
            "week": week,
            "week_days": week_days,
            "league_key": league_key,
            "league_name": league_name,

            "team_a_name": team_a["team_name"],
            "team_a_manager": team_a["manager"],
            "team_a_record": f'{team_a["wins"]}-{team_a["losses"]}-{team_a["ties"]}',

            "team_b_name": team_b["team_name"],
            "team_b_manager": team_b["manager"],
            "team_b_record": f'{team_b["wins"]}-{team_b["losses"]}-{team_b["ties"]}',

            "team_a_categories_won": team_a_categories,
            "team_b_categories_won": team_b_categories,

            "team_a_category_wins": len(team_a_categories),
            "team_b_category_wins": len(team_b_categories),
            "category_ties": max(
                0,
                15 - len(team_a_categories) - len(team_b_categories)
            )
        })

    return results


def write_csv(rows):
    fields = [
        "season",
        "week",
        "week_days",
        "league_key",
        "league_name",
        "team_a_name",
        "team_a_manager",
        "team_a_record",
        "team_b_name",
        "team_b_manager",
        "team_b_record",
        "team_a_category_wins",
        "team_b_category_wins",
        "category_ties",
        "team_a_categories_won",
        "team_b_categories_won",
    ]

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for row in rows:
            row = row.copy()
            row["team_a_categories_won"] = ", ".join(row["team_a_categories_won"])
            row["team_b_categories_won"] = ", ".join(row["team_b_categories_won"])
            writer.writerow(row)


def main():
    session = get_session()

    all_rows = []

    leagues = get_all_leagues(session)

    for league in leagues:
        print(f'Processing {league["season"]}: {league["league_name"]}')

        scoreboard_url = (
            f'{BASE_URL}/league/{league["league_key"]}'
        )

        meta = api_get(session, scoreboard_url)
        time.sleep(API_DELAY)

        league_meta = meta.get("fantasy_content", {}).get("league", [{}])[0]

        start_week = int(league_meta.get("start_week", 1))
        end_week = int(league_meta.get("current_week", league_meta.get("end_week", 1)))

        for week in range(start_week, end_week + 1):

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

    OUTPUT_JSON.write_text(
        json.dumps(all_rows, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    write_csv(all_rows)

    print(f"Saved {len(all_rows)} matchups.")


if __name__ == "__main__":
    main()

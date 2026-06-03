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
    """
    Extract team identity and weekly matchup record from a Yahoo team object.

    Yahoo's team list is structured as:
        [ {metadata dict: name, managers, ...}, {stats dict: team_points, ...} ]

    Identity (name, manager nickname) is read ONLY from team_obj[0] — the
    metadata dict at the head of the list.  This prevents the recursive stats
    walk from accidentally picking up the opponent's nickname when Yahoo embeds
    adjacent team data inside the stats block.

    Stats (points or outcome_totals) are found by a recursive walk of the
    remainder of the list (team_obj[1:]), which never touches name/nickname.
    """
    info = {
        "name": "",
        "manager": "",
        "wins": None,
        "losses": None,
        "ties": None,
        "points": None
    }

    # ── Step 1: identity — scoped to team_obj[0] only ───────────────────────
    # Yahoo's scoreboard response embeds ALL teams' data in a shared structure.
    # A full recursive walk bleeds sibling team nicknames into the wrong team
    # (e.g. MZ's nickname from YoMamma contaminates Dirty Sanchez and Steins Boys).
    #
    # team_obj[0] is the metadata list for THIS team only:
    #   [{team_key}, {name}, {url}, ..., {managers: {0: {manager: {nickname}}}}]
    # team_obj[1+] is stats — never contains this team's identity fields cleanly.
    #
    # We search only team_obj[0] for name and nickname. If nickname is absent
    # (hidden manager), we leave it as "" and the caller will show "--hidden--".
    meta_block = team_obj[0] if isinstance(team_obj, list) and len(team_obj) > 0 else {}

    def find_in_meta(node, target_key):
        """Recursively search only the metadata block for target_key."""
        if isinstance(node, dict):
            if target_key in node:
                return node[target_key]
            for v in node.values():
                result = find_in_meta(v, target_key)
                if result is not None:
                    return result
        elif isinstance(node, list):
            for item in node:
                result = find_in_meta(item, target_key)
                if result is not None:
                    return result
        return None

    found_name     = find_in_meta(meta_block, "name")
    found_nickname = find_in_meta(meta_block, "nickname")
    # is_owned_by_current_login marks the authenticated user's real/primary team.
    # When a manager has multiple team entries (Yahoo data quirk), this flag
    # identifies which one is canonical. We store it so deduplicate_matchups
    # can prefer the primary team row when the same manager appears twice in a week.
    is_primary     = bool(find_in_meta(meta_block, "is_owned_by_current_login"))

    if found_name:
        info["name"] = found_name
    if found_nickname:
        info["manager"] = found_nickname
    else:
        info["manager"] = "-- hidden --"
    info["is_primary"] = is_primary

    # ── Step 2: stats walk — never touches name or nickname ─────────────────
    def _apply_outcome_totals(totals):
        if not isinstance(totals, dict):
            return
        if "wins" in totals and "losses" in totals:
            if info["wins"] is None:
                info["wins"] = int(totals["wins"])
                info["losses"] = int(totals["losses"])
                info["ties"] = int(totals.get("ties", 0))

    def walk_stats(data):
        if isinstance(data, dict):
            if "outcome_totals" in data:
                _apply_outcome_totals(data["outcome_totals"])
            if "team_outcome" in data:
                to = data["team_outcome"]
                if isinstance(to, dict):
                    _apply_outcome_totals(to.get("outcome_totals", {}))
            if info["wins"] is None and "team_points" in data:
                tp = data["team_points"]
                if isinstance(tp, dict) and "total" in tp:
                    info["points"] = tp["total"]
            for v in data.values():
                walk_stats(v)
        elif isinstance(data, list):
            for item in data:
                walk_stats(item)

    stats_block = team_obj[1:] if isinstance(team_obj, list) else team_obj
    walk_stats(stats_block)

    is_category = False
    if info["wins"] is not None and info["losses"] is not None:
        is_category = True
        w = info["wins"]
        l = info["losses"]
        t = info["ties"] if info["ties"] is not None else 0
        record_str = f"{w}-{l}-{t}"
    elif info["points"] is not None:
        record_str = str(info["points"])
        w, l, t = 0, 0, 0
    else:
        record_str = "0-0-0"
        w, l, t = 0, 0, 0

    return {
        "name": info["name"],
        "manager": info["manager"],
        "is_primary": info.get("is_primary", False),
        "record": record_str,
        "is_category": is_category,
        "wins": w,
        "losses": l,
        "ties": t
    }


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

        # Skip phantom matchups where the same manager appears on both sides.
        # The Yahoo API occasionally registers a manager with multiple team
        # entries in one league, producing bogus self-matchup rows.
        if team_a_info["manager"] == team_b_info["manager"]:
            continue

        # NOTE: The Yahoo scoreboard API never returns outcome_totals (W-L-T)
        # at the matchup level. For category leagues, team_points.total holds
        # the number of categories won that week. Ties are inferred in
        # backfill_ties() after all weeks are collected, using the formula:
        #   ties = league_total_categories - team_a_cats - team_b_cats
        # The "ties" field is left as 0 here and filled in by backfill_ties().
        results.append({
            "season": season,
            "week": week,
            "league_key": league_key,
            "league_name": league_name,
            "ties": 0,  # filled in by backfill_ties() after collection

            "manager_a": team_a_info["manager"],
            "manager_a_record": team_a_info["record"],
            "manager_a_primary": team_a_info.get("is_primary", False),

            "manager_b": team_b_info["manager"],
            "manager_b_record": team_b_info["record"],
            "manager_b_primary": team_b_info.get("is_primary", False),
        })

    return results


def mask_duplicate_managers(all_rows):
    """
    When Yahoo registers a manager with multiple team entries in the same league,
    every team gets that manager's nickname. Only one team is their real entry
    (flagged by is_owned_by_current_login = manager_a_primary / manager_b_primary).
    The extra team slots are effectively hidden/ghost teams — replace their
    manager name with '-- hidden --' so they don't inflate H2H records.

    Strategy: for each (league_key, manager_name), find the primary team row.
    Any other rows in that league where that manager name appears on a non-primary
    team get their manager name replaced with '-- hidden --'.
    """
    from collections import defaultdict

    # Collect all (league, manager) combos and whether any row has the primary flag
    league_manager_has_primary = defaultdict(bool)
    for row in all_rows:
        if row.get("manager_a_primary"):
            league_manager_has_primary[(row["league_key"], row["manager_a"])] = True
        if row.get("manager_b_primary"):
            league_manager_has_primary[(row["league_key"], row["manager_b"])] = True

    # For each (league, manager) that HAS a primary, mask all non-primary appearances
    masked = 0
    for row in all_rows:
        lk = row["league_key"]
        ma, mb = row["manager_a"], row["manager_b"]

        if league_manager_has_primary.get((lk, ma)) and not row.get("manager_a_primary"):
            row["manager_a"] = "-- hidden --"
            masked += 1

        if league_manager_has_primary.get((lk, mb)) and not row.get("manager_b_primary"):
            row["manager_b"] = "-- hidden --"
            masked += 1

    if masked:
        print(f"  Masked {masked} non-primary duplicate manager entries as '-- hidden --'.", flush=True)


def backfill_ties(all_rows):
    """
    Infer tied categories for every matchup in a category league.

    The Yahoo scoreboard endpoint returns the number of stat categories each
    team WON that week via team_points.total (e.g. 8 and 5 in a 14-cat league).
    Any categories that neither team won are ties:
        ties = total_cats - team_a_cats - team_b_cats

    Strategy:
      1. For each league, find the highest (team_a + team_b) sum ever seen
         across all its matchups — that maximum is the total category count.
      2. Skip leagues where the max sum is 0 (points leagues store decimal
         totals as strings, or the season had no data, e.g. COVID 2020).
      3. For each matchup whose record values are both non-negative integers,
         compute and set ties = max_cats - a - b  (floor at 0).
    """
    from collections import defaultdict

    # Pass 1: determine total categories per league
    league_max_cats = defaultdict(int)
    for row in all_rows:
        try:
            a = int(row["manager_a_record"])
            b = int(row["manager_b_record"])
            league_max_cats[row["league_key"]] = max(
                league_max_cats[row["league_key"]], a + b
            )
        except (ValueError, TypeError):
            pass  # points league — decimal string, skip

    # Pass 2: write ties
    for row in all_rows:
        max_cats = league_max_cats.get(row["league_key"], 0)
        if max_cats == 0:
            continue  # points league or no data
        try:
            a = int(row["manager_a_record"])
            b = int(row["manager_b_record"])
            row["ties"] = max(0, max_cats - a - b)
        except (ValueError, TypeError):
            pass


def write_csv(rows):
    fields = [
        "season",
        "week",
        "league_key",
        "league_name",
        "ties",
        "manager_a",
        "manager_a_record",
        "manager_b",
        "manager_b_record",
        # manager_a_primary / manager_b_primary are internal dedup flags; omitted from CSV
    ]

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for row in rows:
            writer.writerow({k: v for k, v in row.items() if k in fields})


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

    print("Masking non-primary duplicate manager entries...", flush=True)
    mask_duplicate_managers(all_rows)

    print("Backfilling tied categories...", flush=True)
    backfill_ties(all_rows)

    OUTPUT_JSON.write_text(
        json.dumps(all_rows, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    write_csv(all_rows)

    print(f"Saved {len(all_rows)} matchups total.", flush=True)


if __name__ == "__main__":
    main()

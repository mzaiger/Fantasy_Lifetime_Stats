"""
Fetches all TRADE transactions across every historical MLB fantasy season and
enriches each traded player with:

  - current_rank   (Yahoo season rank, rank_type="S")
  - preseason_rank (Yahoo overall pre-draft rank, rank_type="OR")
  - roster_pct     (Yahoo ownership %)
  - from_manager
  - to_manager

Outputs:
  trade_ranks.json
  trade_ranks.csv
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

CLIENT_ID = os.getenv("YAHOO_CLIENT_ID")
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET")

REDIRECT_URI = "https://localhost"

AUTHORIZATION_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

TOKEN_CACHE = Path("token_cache.json")

BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"

CURRENT_YEAR = str(datetime.now().year)

# ---------------------------------------------------------------------------
# AUTH HELPERS
# ---------------------------------------------------------------------------


def _save_token(token):
    TOKEN_CACHE.write_text(json.dumps(token, indent=2))


def _load_token():
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
        auto_refresh_kwargs={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        token_updater=_save_token,
    )

    cached = _load_token()

    if cached:
        session.token = cached
        print("Using cached OAuth token.")

    else:
        auth_url, _ = session.authorization_url(AUTHORIZATION_URL)

        webbrowser.open(auth_url)

        print(f"\nAuthorize here:\n{auth_url}\n")

        redirect = input("Paste redirect URL: ").strip()

        token = (
            session.fetch_token(
                TOKEN_URL,
                authorization_response=redirect,
                client_secret=CLIENT_SECRET,
            )
            if redirect.startswith("http")
            else session.fetch_token(
                TOKEN_URL,
                code=redirect,
                client_secret=CLIENT_SECRET,
            )
        )

        _save_token(token)

    return session


def api_get(session, url):
    resp = session.get(url, params={"format": "json"})

    if resp.status_code != 200:
        print(f"ERROR {resp.status_code}: {resp.text[:300]}")
        return {}

    return resp.json()


# ---------------------------------------------------------------------------
# BUILD TEAM MAP
# ---------------------------------------------------------------------------


def build_team_map(session, league_key):
    """
    Returns:
        {
            team_key: manager_nickname
        }
    """

    url = f"{BASE_URL}/league/{league_key}/teams"

    data = api_get(session, url)

    time.sleep(0.3)

    team_map = {}

    try:
        teams_dict = (
            data.get("fantasy_content", {})
            .get("league", [{}, {}])[1]
            .get("teams", {})
        )

        for i in range(int(teams_dict.get("count", 0))):

            team_data = teams_dict.get(str(i), {}).get("team", [])

            team_info = {}

            for entry in team_data:

                if isinstance(entry, list):
                    for sub in entry:
                        if isinstance(sub, dict):
                            team_info.update(sub)

                elif isinstance(entry, dict):
                    team_info.update(entry)

            team_key = team_info.get("team_key")

            manager_name = "-"

            managers = team_info.get("managers")

            if isinstance(managers, list):

                for mgr_wrap in managers:

                    if isinstance(mgr_wrap, dict):

                        mgr = mgr_wrap.get("manager", mgr_wrap)

                        if isinstance(mgr, dict):
                            manager_name = (
                                mgr.get("nickname")
                                or mgr.get("guid")
                                or "-"
                            )

            if team_key:
                team_map[team_key] = manager_name

    except Exception as e:
        print("build_team_map error:", e)

    return team_map


# ---------------------------------------------------------------------------
# FETCH LEAGUE TRADES
# ---------------------------------------------------------------------------


def fetch_league_trades(
    session,
    league_key,
    season,
    team_map,
):

    url = f"{BASE_URL}/league/{league_key}/transactions;types=trade"

    data = api_get(session, url)

    time.sleep(0.4)

    records = []

    try:
        league_list = data.get("fantasy_content", {}).get("league", [])

        tx_dict = (
            league_list[1].get("transactions", {})
            if len(league_list) > 1
            else {}
        )

    except Exception:
        return records

    if not isinstance(tx_dict, dict):
        return records

    tx_count = int(tx_dict.get("count", 0))

    for i in range(tx_count):

        tx_entry = tx_dict.get(str(i), {}).get("transaction", [])

        if not tx_entry:
            continue

        tx_meta = tx_entry[0] if isinstance(tx_entry[0], dict) else {}

        tx_id = tx_meta.get("transaction_id", "-")
        tx_ts = tx_meta.get("timestamp", "-")
        tx_type = tx_meta.get("type", "").lower()

        if tx_type != "trade":
            continue

        for block in tx_entry[1:]:

            if not isinstance(block, dict):
                continue

            if "players" not in block:
                continue

            players_dict = block["players"]

            p_count = int(players_dict.get("count", 0))

            for j in range(p_count):

                p_entry = players_dict.get(str(j), {}).get("player", [])

                if not p_entry:
                    continue

                player_key = "-"
                player_name = "-"
                player_pos = "-"

                from_manager = "-"
                to_manager = "-"

                for item in p_entry:

                    # -------------------------------------------------------
                    # METADATA LIST
                    # -------------------------------------------------------

                    if isinstance(item, list):

                        for sub in item:

                            if not isinstance(sub, dict):
                                continue

                            if "player_key" in sub:
                                player_key = sub["player_key"]

                            if "name" in sub:
                                player_name = sub["name"].get("full", "-")

                            if "display_position" in sub:
                                player_pos = sub.get(
                                    "display_position",
                                    "-",
                                )

                    # -------------------------------------------------------
                    # TRANSACTION DATA
                    # -------------------------------------------------------

                    elif isinstance(item, dict):

                        tx_data = item.get("transaction_data")
                        
                        # Yahoo returns transaction_data as a list for trades
                        if isinstance(tx_data, list) and len(tx_data) > 0:
                            tx_data = tx_data[0]

                        if isinstance(tx_data, dict):

                            source_key = tx_data.get(
                                "source_team_key",
                                "",
                            )

                            dest_key = tx_data.get(
                                "destination_team_key",
                                "",
                            )

                            from_manager = team_map.get(
                                source_key,
                                source_key or "-",
                            )

                            to_manager = team_map.get(
                                dest_key,
                                dest_key or "-",
                            )

                records.append(
                    {
                        "season": season,
                        "league_key": league_key,
                        "trade_id": tx_id,
                        "timestamp": tx_ts,
                        "from_manager": from_manager,
                        "to_manager": to_manager,
                        "player_name": player_name,
                        "player_position": player_pos,
                        "player_key": player_key,
                        "current_rank": "-",
                        "preseason_rank": "-",
                        "roster_pct": "-",
                    }
                )

    return records


# ---------------------------------------------------------------------------
# OWNERSHIP LOOKUP
# ---------------------------------------------------------------------------


def get_roster_percent_map(
    session,
    player_keys,
    batch_size=25,
):

    roster_map = {}

    for start in range(0, len(player_keys), batch_size):

        batch = player_keys[start : start + batch_size]

        keys_str = ",".join(batch)

        url = f"{BASE_URL}/players;player_keys={keys_str}/percent_owned"

        data = api_get(session, url)

        time.sleep(0.35)

        players_block = (
            data.get("fantasy_content", {})
            .get("players", {})
        )

        for i in range(int(players_block.get("count", 0))):

            p_data = players_block.get(str(i), {}).get("player", [])

            p_key = None
            roster_pct = "-"

            for item in p_data:

                # -----------------------------------------------------------
                # METADATA LIST
                # -----------------------------------------------------------

                if isinstance(item, list):

                    for sub in item:

                        if (
                            isinstance(sub, dict)
                            and "player_key" in sub
                        ):
                            p_key = sub["player_key"]

                # -----------------------------------------------------------
                # OWNERSHIP
                # -----------------------------------------------------------

                elif isinstance(item, dict) and "percent_owned" in item:
                    
                    po_data = item["percent_owned"]
                    
                    if isinstance(po_data, list):
                        for entry in po_data:
                            if isinstance(entry, dict) and "value" in entry:
                                roster_pct = entry["value"]
                                break
                    elif isinstance(po_data, dict):
                        roster_pct = po_data.get("value", "-")

            if p_key:
                roster_map[p_key] = roster_pct

    return roster_map


# ---------------------------------------------------------------------------
# ENRICH PLAYER RANKS
# ---------------------------------------------------------------------------


def enrich_player_ranks(
    session,
    league_key,
    records,
):

    unique_keys = list(
        {
            r["player_key"]
            for r in records
            if r["player_key"] != "-"
        }
    )

    if not unique_keys:
        return

    rank_map = {}

    BATCH = 25

    for i in range(0, len(unique_keys), BATCH):

        batch = unique_keys[i : i + BATCH]

        keys_str = ",".join(batch)

        url = (
            f"{BASE_URL}/league/{league_key}/players"
            f";player_keys={keys_str};out=ranks"
        )

        data = api_get(session, url)

        time.sleep(0.35)

        try:
            players_block = (
                data.get("fantasy_content", {})
                .get("league", [{}, {}])[1]
                .get("players", {})
            )

        except Exception:
            players_block = {}

        p_count = int(players_block.get("count", 0))

        for j in range(p_count):

            p_entry = players_block.get(str(j), {}).get("player", [])

            if not p_entry:
                continue

            p_key = "-"
            preseason_rank = "-"
            current_rank = "-"

            for item in p_entry:

                # -----------------------------------------------------------
                # METADATA LIST
                # -----------------------------------------------------------

                if isinstance(item, list):

                    for sub in item:

                        if (
                            isinstance(sub, dict)
                            and "player_key" in sub
                        ):
                            p_key = sub["player_key"]

                # -----------------------------------------------------------
                # PLAYER RANKS
                # -----------------------------------------------------------

                elif isinstance(item, dict):

                    if "player_ranks" in item:

                        ranks = item["player_ranks"]

                        # Sometimes Yahoo returns dict instead of list
                        if isinstance(ranks, dict):
                            ranks = [ranks]

                        if isinstance(ranks, list):

                            for r_wrap in ranks:

                                if not isinstance(r_wrap, dict):
                                    continue

                                r = r_wrap.get(
                                    "player_rank",
                                    r_wrap,
                                )

                                rank_type = r.get("rank_type")
                                rank_season = str(
                                    r.get("rank_season", "")
                                )

                                rank_value = r.get(
                                    "rank_value",
                                    "-",
                                )

                                if rank_type == "OR":
                                    preseason_rank = rank_value

                                if (
                                    rank_type == "S"
                                    and rank_season == CURRENT_YEAR
                                ):
                                    current_rank = rank_value

            if p_key != "-":

                rank_map[p_key] = {
                    "current_rank": current_rank,
                    "preseason_rank": preseason_rank,
                }

    # -----------------------------------------------------------------------
    # OWNERSHIP
    # -----------------------------------------------------------------------

    roster_map = get_roster_percent_map(
        session,
        unique_keys,
    )

    # -----------------------------------------------------------------------
    # PATCH RECORDS
    # -----------------------------------------------------------------------

    for rec in records:

        pk = rec["player_key"]

        if pk in rank_map:

            rec["current_rank"] = rank_map[pk]["current_rank"]

            rec["preseason_rank"] = rank_map[pk]["preseason_rank"]

        if pk in roster_map:

            rec["roster_pct"] = roster_map[pk]


# ---------------------------------------------------------------------------
# SAVE OUTPUTS
# ---------------------------------------------------------------------------


def save_outputs(records):

    # -----------------------------------------------------------------------
    # JSON GROUPED BY TRADE
    # -----------------------------------------------------------------------

    grouped = {}

    for rec in records:

        key = (
            rec["season"],
            rec["league_key"],
            rec["trade_id"],
        )

        if key not in grouped:

            grouped[key] = {
                "season": rec["season"],
                "league_key": rec["league_key"],
                "trade_id": rec["trade_id"],
                "timestamp": rec["timestamp"],
                "players": [],
            }

        grouped[key]["players"].append(
            {
                "from_manager": rec["from_manager"],
                "to_manager": rec["to_manager"],
                "player_name": rec["player_name"],
                "player_position": rec["player_position"],
                "player_key": rec["player_key"],
                "current_rank": rec["current_rank"],
                "preseason_rank": rec["preseason_rank"],
                "roster_pct": rec["roster_pct"],
            }
        )

    grouped_list = list(grouped.values())

    with open(
        "trade_ranks.json",
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            grouped_list,
            f,
            indent=2,
            ensure_ascii=False,
        )

    # -----------------------------------------------------------------------
    # CSV
    # -----------------------------------------------------------------------

    csv_fields = [
        "season",
        "league_key",
        "trade_id",
        "timestamp",
        "from_manager",
        "to_manager",
        "player_name",
        "player_position",
        "player_key",
        "current_rank",
        "preseason_rank",
        "roster_pct",
    ]

    with open(
        "trade_ranks.csv",
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=csv_fields,
        )

        writer.writeheader()

        for row in records:
            writer.writerow(row)

    print(
        f"\nSaved {len(records)} player trade rows "
        f"to trade_ranks.json and trade_ranks.csv"
    )


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------


def download_all_trades():

    if not CLIENT_ID or not CLIENT_SECRET:

        print(
            "ERROR: Missing YAHOO_CLIENT_ID "
            "or YAHOO_CLIENT_SECRET"
        )

        return

    session = get_session()

    all_records = []

    # -----------------------------------------------------------------------
    # GET ALL MLB SEASONS
    # -----------------------------------------------------------------------

    print("\nFetching MLB game history...")

    games_data = api_get(
        session,
        f"{BASE_URL}/users;use_login=1/games;game_codes=mlb",
    )

    try:

        user_wrapper = (
            games_data.get("fantasy_content", {})
            .get("users", {})
            .get("0", {})
            .get("user", [])
        )

        games = user_wrapper[1].get("games", {})

    except Exception:
        print("Unable to fetch game history.")
        return

    game_keys = []

    for i in range(int(games.get("count", 0))):

        g = games.get(str(i), {}).get("game", [])

        game_key = None
        season = None

        for item in g:

            if isinstance(item, dict):

                if "game_key" in item:
                    game_key = item["game_key"]

                if "season" in item:
                    season = item["season"]

        if game_key and season:
            game_keys.append((game_key, season))

    print(f"Found {len(game_keys)} MLB seasons")

    # -----------------------------------------------------------------------
    # PROCESS EACH SEASON
    # -----------------------------------------------------------------------

    for game_key, season in game_keys:

        print(f"\n=== {season} ===")

        leagues_url = (
            f"{BASE_URL}/users;use_login=1/games;"
            f"game_keys={game_key}/leagues"
        )

        leagues_data = api_get(session, leagues_url)

        time.sleep(0.4)

        try:

            user_wrapper = (
                leagues_data.get("fantasy_content", {})
                .get("users", {})
                .get("0", {})
                .get("user", [])
            )

            leagues = user_wrapper[1].get("games", {}).get(
                "0",
                {},
            ).get("game", [])[1].get("leagues", {})

        except Exception:
            continue

        league_count = int(leagues.get("count", 0))

        print(f"Leagues: {league_count}")

        for i in range(league_count):

            league_data = (
                leagues.get(str(i), {})
                .get("league", [])
            )

            league_key = None

            for item in league_data:

                if (
                    isinstance(item, dict)
                    and "league_key" in item
                ):
                    league_key = item["league_key"]

            if not league_key:
                continue

            print(f"  League: {league_key}")

            try:

                team_map = build_team_map(
                    session,
                    league_key,
                )

                trade_records = fetch_league_trades(
                    session,
                    league_key,
                    season,
                    team_map,
                )

                if not trade_records:
                    continue

                enrich_player_ranks(
                    session,
                    league_key,
                    trade_records,
                )

                all_records.extend(trade_records)

                print(
                    f"    Trades: {len(trade_records)}"
                )

            except Exception as e:

                print(
                    f"    ERROR processing "
                    f"{league_key}: {e}"
                )

    # -----------------------------------------------------------------------
    # SAVE
    # -----------------------------------------------------------------------

    save_outputs(all_records)


if __name__ == "__main__":
    download_all_trades()

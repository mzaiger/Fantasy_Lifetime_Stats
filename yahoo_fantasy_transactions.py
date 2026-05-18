import csv
import json
import os
import time
import webbrowser
from pathlib import Path
from requests_oauthlib import OAuth2Session

# ---------------------------------------------------------------------------
# CONFIG (Using environment variables from yahoo_fantasy_ranks.py)
# ---------------------------------------------------------------------------
CLIENT_ID     = os.getenv("YAHOO_CLIENT_ID")
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET")
REDIRECT_URI      = "https://localhost"
AUTHORIZATION_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL         = "https://api.login.yahoo.com/oauth2/get_token"
TOKEN_CACHE       = Path("token_cache.json")
BASE_URL          = "https://fantasysports.yahooapis.com/fantasy/v2"

# ---------------------------------------------------------------------------
# OAuth (Adapted from yahoo_fantasy_ranks.py)
# ---------------------------------------------------------------------------
def _save_token(token):
    TOKEN_CACHE.write_text(json.dumps(token, indent=2))

def _load_token():
    # Check for token in environment variables first
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
# Main
# ---------------------------------------------------------------------------
def download_all_transactions():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET environment variables must be set.")
        return

    session = get_session()
    all_transactions = []

    print("\n[1/3] Querying Yahoo profile for all historical MLB seasons...")
    user_games_url = f"{BASE_URL}/users;use_login=1/games;game_codes=mlb"
    games_data = api_get(session, user_games_url)

    try:
        user_wrapper = games_data.get("fantasy_content", {}).get("users", {}).get("0", {}).get("user", [])
        games_dict   = user_wrapper[1].get("games", {})
        game_count   = int(games_dict.get("count", 0))
    except (IndexError, AttributeError, ValueError):
        print("Failed to parse user game profile.")
        return

    mlb_game_keys = []
    for i in range(game_count):
        game_meta = games_dict.get(str(i), {}).get("game", [{}])[0]
        g_key    = game_meta.get("game_key")
        g_season = game_meta.get("season")
        if g_key and g_season:
            mlb_game_keys.append({"key": g_key, "season": g_season})

    mlb_game_keys.sort(key=lambda x: x["season"])
    print(f"Found {len(mlb_game_keys)} MLB seasons: {', '.join(x['season'] for x in mlb_game_keys)}")

    print(f"\n[2/3] Scanning all transactions across every team in every season...")
    for item in mlb_game_keys:
        game_key = item["key"]
        season   = item["season"]

        leagues_url  = f"{BASE_URL}/users;use_login=1/games;game_keys={game_key}/leagues"
        leagues_data = api_get(session, leagues_url)
        time.sleep(0.4)

        try:
            l_user_wrapper  = leagues_data.get("fantasy_content", {}).get("users", {}).get("0", {}).get("user", [])
            l_games_dict    = l_user_wrapper[1].get("games", {})
            l_league_wrapper = l_games_dict.get("0", {}).get("game", [{}, {}])[1].get("leagues", {})
            l_count         = int(l_league_wrapper.get("count", 0))
        except (IndexError, AttributeError, ValueError):
            continue

        for l_idx in range(l_count):
            league_meta = l_league_wrapper.get(str(l_idx), {}).get("league", [{}])[0]
            league_key  = league_meta.get("league_key")
            if not league_key:
                continue

            # Grab all teams in the league
            teams_url  = f"{BASE_URL}/league/{league_key}/teams"
            teams_data = api_get(session, teams_url)
            time.sleep(0.4)

            league_teams = teams_data.get("fantasy_content", {}).get("league", [{}, {}])[1].get("teams", {})
            t_count      = int(league_teams.get("count", 0))

            # Build list of all team keys + names + manager nicknames
            all_teams = []
            for t_idx in range(t_count):
                t_entry    = league_teams.get(str(t_idx), {}).get("team", [[]])[0]
                t_key      = None
                t_name     = ""
                m_nickname = ""
                for prop in t_entry:
                    if not isinstance(prop, dict): continue
                    if "team_key"  in prop: t_key      = prop["team_key"]
                    if "name"      in prop: t_name     = prop["name"]
                    if "managers"  in prop:
                        try:
                            m_nickname = prop["managers"][0]["manager"].get("nickname", "")
                        except (IndexError, KeyError):
                            pass
                if t_key:
                    all_teams.append({"key": t_key, "name": t_name, "manager": m_nickname})

            print(f"  {season} | league {league_key} | {len(all_teams)} teams")

            # Query transactions for EVERY team
            for team in all_teams:
                tx_url  = f"{BASE_URL}/team/{team['key']}/transactions;types=add,drop"
                tx_data = api_get(session, tx_url)
                time.sleep(0.3)

                try:
                    team_wrapper      = tx_data.get("fantasy_content", {}).get("team", [{}, {}])
                    transactions_dict = team_wrapper[1].get("transactions", {})
                except (IndexError, AttributeError):
                    transactions_dict = {}
                if not isinstance(transactions_dict, dict):
                    transactions_dict = {}

                tx_count = int(transactions_dict.get("count", 0))

                for i in range(tx_count):
                    tx_entry = transactions_dict.get(str(i), {}).get("transaction", [])
                    if not tx_entry: continue

                    tx_meta = tx_entry[0] if isinstance(tx_entry[0], dict) else {}
                    tx_id = tx_meta.get("transaction_id", "-")
                    tx_timestamp = tx_meta.get("timestamp", "-")

                    for block in tx_entry[1:]:
                        if not isinstance(block, dict) or "players" not in block: continue
                        players_dict = block["players"]
                        p_count      = int(players_dict.get("count", 0))

                        for j in range(p_count):
                            p_entry = players_dict.get(str(j), {}).get("player", [])
                            if not p_entry or len(p_entry) < 2: continue

                            p_meta = p_entry[0]
                            p_tx   = p_entry[1].get("transaction_data", {})
                            if not isinstance(p_tx, dict): p_tx = {}

                            player_name = ""
                            for it in p_meta:
                                if isinstance(it, dict) and "name" in it:
                                    player_name = it["name"].get("full", "")
                                    break
                            if not player_name:
                                continue

                            raw_action = (p_tx.get("type") or tx_meta.get("type", "")).lower()
                            if not raw_action:
                                continue

                            if raw_action == "add":
                                action = "add"
                            elif raw_action == "drop":
                                action = "drop"
                            elif raw_action == "add/drop":
                                # If it's a drop, there will be a source_team_key present on the player token side
                                if p_tx.get("source_team_key"):
                                    action = "drop"
                                else:
                                    action = "add"
                            else:
                                action = raw_action

                            all_transactions.append({
                                "season": season,
                                "league_key": league_key,
                                "team_key": team["key"],
                                "team_name": team["name"],
                                "manager_nickname": team["manager"],
                                "transaction_id": tx_id,
                                "timestamp": tx_timestamp,
                                "transaction_type": raw_action,
                                "player_name": player_name,
                                "player_action": action
                            })

    # ── [3/3] Exporting Data ───────────────────────────────────────────────
    print(f"\n[3/3] Saving {len(all_transactions)} total player transactions to files...")

    # Save to JSON File
    json_file = Path("all_seasons_transactions.json")
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(all_transactions, f, indent=2, ensure_ascii=False)
    print(f"Saved JSON → {json_file}")

    # Save to CSV File
    csv_file = Path("all_seasons_transactions.csv")
    fieldnames = [
        "season", "league_key", "team_key", "team_name", "manager_nickname",
        "transaction_id", "timestamp", "transaction_type", "player_name", "player_action"
    ]
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_transactions:
            writer.writerow(row)
    print(f"Saved CSV  → {csv_file}")
    print("\nProcessing Complete!")


if __name__ == "__main__":
    download_all_transactions()
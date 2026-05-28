"""
Fetches all TRADE transactions across every historical MLB fantasy season and
enriches each traded player with:
  - preseason_rank (Yahoo overall pre-draft rank, rank_type="OR")
  - roster_pct     (percent_owned in your league)

NOTE: current/in-season rank (rank_type="S") is a live value from Yahoo and
only reflects today — not the state at trade time — so it is intentionally
excluded. preseason_rank ("OR") is stable and meaningful across all seasons.

Output files:
  trade_ranks.json  — grouped by trade (one object per trade, players array)
  trade_ranks.csv   — flat (one row per player per trade)

Patterns borrowed from:
  yahoo_fantasy_transactions.py    → season/league traversal
  Fantasy_Auto_Pilot_Get_Roster.py → rank parsing (OR), percent_owned
"""

import csv
import json
import os
import time
import webbrowser
from pathlib import Path
from requests_oauthlib import OAuth2Session

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
CLIENT_ID         = os.getenv("YAHOO_CLIENT_ID")
CLIENT_SECRET     = os.getenv("YAHOO_CLIENT_SECRET")
REDIRECT_URI      = "https://localhost"
AUTHORIZATION_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL         = "https://api.login.yahoo.com/oauth2/get_token"
TOKEN_CACHE       = Path("token_cache.json")
BASE_URL          = "https://fantasysports.yahooapis.com/fantasy/v2"

# ---------------------------------------------------------------------------
# OAuth helpers  (identical to yahoo_fantasy_ranks.py / transactions.py)
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
        print(f"\nIf browser didn't open:\n  {auth_url}\n")
        redirect = input("Paste the full redirect URL: ").strip()
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
    resp = session.get(url, params={"format": "json"})
    if resp.status_code != 200:
        print(f"  ERROR {resp.status_code}: {resp.text[:300]}")
        return {}
    return resp.json()


# ---------------------------------------------------------------------------
# Build team_key → manager_nickname lookup for a single league
# ---------------------------------------------------------------------------
def build_team_map(session: OAuth2Session, league_key: str) -> dict[str, str]:
    """Returns {team_key: manager_nickname} for every team in the league."""
    url  = f"{BASE_URL}/league/{league_key}/teams"
    data = api_get(session, url)
    time.sleep(0.3)

    team_map: dict[str, str] = {}
    try:
        teams_dict = (
            data.get("fantasy_content", {})
                .get("league", [{}, {}])[1]
                .get("teams", {})
        )
        for i in range(int(teams_dict.get("count", 0))):
            t_entry = teams_dict.get(str(i), {}).get("team", [[]])[0]
            t_key   = None
            m_nick  = "-"
            for prop in t_entry:
                if not isinstance(prop, dict):
                    continue
                if "team_key" in prop:
                    t_key = prop["team_key"]
                if "managers" in prop:
                    try:
                        m_nick = prop["managers"][0]["manager"].get("nickname", "-")
                    except (IndexError, KeyError):
                        pass
            if t_key:
                team_map[t_key] = m_nick
    except (IndexError, KeyError, AttributeError):
        pass

    return team_map


# ---------------------------------------------------------------------------
# Fetch all trade transactions for one league  (league-level endpoint —
# much more efficient than the per-team add/drop approach in transactions.py)
# ---------------------------------------------------------------------------
def fetch_league_trades(
    session: OAuth2Session,
    league_key: str,
    season: str,
    team_map: dict[str, str],
) -> list[dict]:
    """
    Returns flat list — one dict per player per trade — with all metadata
    except ranks/roster_pct (those are filled in by enrich_player_ranks).
    """
    url  = f"{BASE_URL}/league/{league_key}/transactions;types=trade"
    data = api_get(session, url)
    time.sleep(0.4)

    records: list[dict] = []

    try:
        league_list = data.get("fantasy_content", {}).get("league", [])
        tx_dict     = league_list[1].get("transactions", {}) if len(league_list) > 1 else {}
    except (IndexError, KeyError, AttributeError):
        return records

    if not isinstance(tx_dict, dict):
        return records

    tx_count = int(tx_dict.get("count", 0))

    for i in range(tx_count):
        tx_entry = tx_dict.get(str(i), {}).get("transaction", [])
        if not tx_entry:
            continue

        tx_meta  = tx_entry[0] if isinstance(tx_entry[0], dict) else {}
        tx_id    = tx_meta.get("transaction_id", "-")
        tx_ts    = tx_meta.get("timestamp", "-")
        tx_type  = tx_meta.get("type", "").lower()

        # Belt-and-suspenders: skip anything that isn't a trade
        if tx_type != "trade":
            continue

        for block in tx_entry[1:]:
            if not isinstance(block, dict) or "players" not in block:
                continue

            players_dict = block["players"]
            p_count      = int(players_dict.get("count", 0))

            for j in range(p_count):
                p_entry = players_dict.get(str(j), {}).get("player", [])
                if not p_entry or len(p_entry) < 2:
                    continue

                p_meta = p_entry[0]  # list of property dicts
                p_tx   = p_entry[1].get("transaction_data", {})
                if not isinstance(p_tx, dict):
                    p_tx = {}

                # ── parse player metadata ──────────────────────────────────
                player_key  = "-"
                player_name = "-"
                player_pos  = "-"
                for it in p_meta:
                    if not isinstance(it, dict):
                        continue
                    if "player_key"       in it:
                        player_key  = it["player_key"]
                    if "name"             in it:
                        player_name = it["name"].get("full", "-")
                    if "display_position" in it:
                        player_pos  = it.get("display_position", "-")

                # ── resolve manager names from team keys ───────────────────
                source_key   = p_tx.get("source_team_key", "")
                dest_key     = p_tx.get("destination_team_key", "")
                from_manager = team_map.get(source_key, source_key or "-")
                to_manager   = team_map.get(dest_key,   dest_key   or "-")

                records.append({
                    "season":          season,
                    "league_key":      league_key,
                    "trade_id":        tx_id,
                    "timestamp":       tx_ts,
                    "from_manager":    from_manager,
                    "to_manager":      to_manager,
                    "player_key":      player_key,
                    "player_name":     player_name,
                    "player_position": player_pos,
                    # Populated later by enrich_player_ranks()
                    "preseason_rank":  "-",
                    "roster_pct":      "-",
                })

    return records


# ---------------------------------------------------------------------------
# Enrich player records with ranks + roster %
# Rank-parsing logic mirrors Fantasy_Auto_Pilot_Get_Roster.py exactly:
#   rank_type "OR"  → preseason overall rank
#   rank_type "S"   → in-season rank (only meaningful for CURRENT_YEAR)
# ---------------------------------------------------------------------------
def enrich_player_ranks(
    session: OAuth2Session,
    league_key: str,
    records: list[dict],
) -> None:
    """
    Batches unique player keys (25 per call) and calls:
      /league/{lk}/players;player_keys=...;out=ranks,ownership

    Patches each record in `records` in-place with:
      preseason_rank, roster_pct

    Only rank_type "OR" (overall preseason) is captured — it is stable across
    all seasons. The "S" (in-season) rank is live/current-only and excluded.
    """
    unique_keys = list({r["player_key"] for r in records if r["player_key"] != "-"})
    if not unique_keys:
        return

    rank_map:    dict[str, dict] = {}
    percent_map: dict[str, str]  = {}

    BATCH = 25
    for i in range(0, len(unique_keys), BATCH):
        batch    = unique_keys[i : i + BATCH]
        keys_str = ",".join(batch)

        url  = (
            f"{BASE_URL}/league/{league_key}/players"
            f";player_keys={keys_str};out=ranks,ownership"
        )
        data = api_get(session, url)
        time.sleep(0.35)

        try:
            players_block = (
                data.get("fantasy_content", {})
                    .get("league", [{}, {}])[1]
                    .get("players", {})
            )
        except (IndexError, KeyError):
            players_block = {}

        p_count = int(players_block.get("count", 0))
        for j in range(p_count):
            p_entry = players_block.get(str(j), {}).get("player", [])
            if not p_entry:
                continue

            p_key        = "-"
            pre_rank     = "-"
            roster_pct   = "-"

            # player_key lives in the first element (list of property dicts)
            meta_list = p_entry[0] if isinstance(p_entry[0], list) else []
            for it in meta_list:
                if isinstance(it, dict) and "player_key" in it:
                    p_key = it["player_key"]
                    break

            # Additional resource blocks (ranks, ownership)
            for block in p_entry[1:]:
                if not isinstance(block, dict):
                    continue

                # ── ranks — only "OR" (overall preseason) is stable ──────
                if "player_ranks" in block:
                    ranks_list = block["player_ranks"]
                    if isinstance(ranks_list, list):
                        for r_wrap in ranks_list:
                            r = r_wrap.get("player_rank", {})
                            if r.get("rank_type") == "OR":
                                pre_rank = r.get("rank_value", "-")

                # ── ownership → percent_owned ─────────────────────────────
                if "ownership" in block:
                    owned = block["ownership"].get("percent_owned", {})
                    if isinstance(owned, dict):
                        roster_pct = owned.get("value", "-")
                    elif isinstance(owned, (str, int, float)):
                        roster_pct = str(owned)

            if p_key != "-":
                rank_map[p_key]    = {"preseason_rank": pre_rank}
                percent_map[p_key] = roster_pct

    # Patch every record in-place
    for rec in records:
        pk = rec["player_key"]
        if pk in rank_map:
            rec["preseason_rank"] = rank_map[pk]["preseason_rank"]
        if pk in percent_map:
            rec["roster_pct"] = percent_map[pk]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def download_all_trades() -> None:
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: Set YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET environment variables.")
        return

    session     = get_session()
    all_records: list[dict] = []

    # ── [1/4] Discover all historical MLB seasons ──────────────────────────
    print("\n[1/4] Querying Yahoo profile for all historical MLB seasons...")
    games_data = api_get(session, f"{BASE_URL}/users;use_login=1/games;game_codes=mlb")

    try:
        user_wrapper = (
            games_data.get("fantasy_content", {})
                      .get("users", {})
                      .get("0", {})
                      .get("user", [])
        )
        games_dict = user_wrapper[1].get("games", {})
        game_count = int(games_dict.get("count", 0))
    except (IndexError, AttributeError, ValueError):
        print("Failed to parse user game profile.")
        return

    mlb_game_keys: list[dict] = []
    for i in range(game_count):
        gm = games_dict.get(str(i), {}).get("game", [{}])[0]
        if gm.get("game_key") and gm.get("season"):
            mlb_game_keys.append({"key": gm["game_key"], "season": gm["season"]})

    mlb_game_keys.sort(key=lambda x: x["season"])
    seasons_str = ", ".join(x["season"] for x in mlb_game_keys)
    print(f"Found {len(mlb_game_keys)} MLB seasons: {seasons_str}")

    # ── [2/4] Collect trades from every league ─────────────────────────────
    print("\n[2/4] Fetching trades from every league/season...")

    # Group by league_key so we can batch the enrichment calls per league
    records_by_league: dict[str, list[dict]] = {}

    for item in mlb_game_keys:
        game_key = item["key"]
        season   = item["season"]

        leagues_url  = f"{BASE_URL}/users;use_login=1/games;game_keys={game_key}/leagues"
        leagues_data = api_get(session, leagues_url)
        time.sleep(0.4)

        try:
            l_wrapper = (
                leagues_data.get("fantasy_content", {})
                            .get("users", {})
                            .get("0", {})
                            .get("user", [])
            )
            l_games   = l_wrapper[1].get("games", {})
            l_leagues = l_games.get("0", {}).get("game", [{}, {}])[1].get("leagues", {})
            l_count   = int(l_leagues.get("count", 0))
        except (IndexError, AttributeError, ValueError):
            continue

        for l_idx in range(l_count):
            league_meta = l_leagues.get(str(l_idx), {}).get("league", [{}])[0]
            league_key  = league_meta.get("league_key")
            if not league_key:
                continue

            # Build {team_key: manager} map for this league
            team_map = build_team_map(session, league_key)

            # Pull trades for this league
            league_trades = fetch_league_trades(session, league_key, season, team_map)
            unique_trade_ids = len({r["trade_id"] for r in league_trades})
            print(
                f"  {season} | {league_key} | "
                f"{unique_trade_ids} trades | {len(league_trades)} player rows"
            )

            if league_trades:
                records_by_league.setdefault(league_key, []).extend(league_trades)

    # ── [3/4] Enrich with ranks + roster % (batched per league) ───────────
    print("\n[3/4] Enriching players with preseason rank and roster %...")
    for league_key, recs in records_by_league.items():
        season_label = recs[0]["season"] if recs else "?"
        print(
            f"  {league_key} ({season_label}) — "
            f"{len({r['player_key'] for r in recs})} unique players"
        )
        enrich_player_ranks(session, league_key, recs)
        all_records.extend(recs)

    # ── [4/4] Write outputs ────────────────────────────────────────────────
    total_player_rows = len(all_records)
    print(f"\n[4/4] Saving {total_player_rows} trade-player rows to files...")

    # Sort globally by season then trade_id for predictable output ordering
    all_records.sort(key=lambda x: (x["season"], x["trade_id"]))

    # --- JSON: one object per trade, with a nested players list ---
    json_trades: dict[str, dict] = {}   # key = "league_key|trade_id"
    for rec in all_records:
        composite_key = f"{rec['league_key']}|{rec['trade_id']}"
        if composite_key not in json_trades:
            json_trades[composite_key] = {
                "season":     rec["season"],
                "league_key": rec["league_key"],
                "trade_id":   rec["trade_id"],
                "timestamp":  rec["timestamp"],
                "players":    [],
            }
        json_trades[composite_key]["players"].append({
            "from_manager":    rec["from_manager"],
            "to_manager":      rec["to_manager"],
            "player_name":     rec["player_name"],
            "player_position": rec["player_position"],
            "player_key":      rec["player_key"],
            "preseason_rank":  rec["preseason_rank"],
            "roster_pct":      rec["roster_pct"],
        })

    json_out = sorted(json_trades.values(), key=lambda x: (x["season"], x["trade_id"]))

    json_file = Path("trade_ranks.json")
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)
    print(f"Saved JSON → {json_file}  ({len(json_out)} trades)")

    # --- CSV: flat, one row per player per trade ---
    csv_file   = Path("trade_ranks.csv")
    fieldnames = [
        "season", "league_key", "trade_id", "timestamp",
        "from_manager", "to_manager",
        "player_name", "player_position", "player_key",
        "preseason_rank", "roster_pct",
    ]
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in all_records:
            writer.writerow(row)
    print(f"Saved CSV  → {csv_file}  ({total_player_rows} rows)")
    print(
        f"\nDone — {len(json_out)} total trades across "
        f"{len(records_by_league)} leagues."
    )


if __name__ == "__main__":
    download_all_trades()

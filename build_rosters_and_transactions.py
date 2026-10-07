"""
Bird Crime — Rosters & Transactions builder
----------------------------------------------------------------------
Run this from inside this site's folder:

    python3 build_rosters_and_transactions.py

What it does:
  1. Fetches the CURRENT season's rosters (full player lists, not just
     settings) and groups each manager's roster by position, using the
     same cached player database build_matchup_stats.py downloads (run
     that script first if players_cache.json doesn't exist yet).
  2. Fetches traded draft picks (data/current_season's traded_picks
     endpoint) so each manager's profile can show picks they've acquired
     or traded away.
  3. Fetches every transaction across every season on file and keeps only
     completed trades — including 3+ team trades, which Sleeper supports
     natively (a trade just lists 3+ roster_ids with adds/drops mapped
     to whichever roster gained/lost each asset).

Outputs:
  data/current_rosters.json — each manager's roster grouped by position,
                               plus any traded picks affecting them
  data/transactions.json    — full trade history, newest first

No third-party installs needed — only Python's standard library.
"""

import json
import os
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PLAYERS_CACHE = os.path.join(BASE_DIR, "players_cache.json")

SLEEPER_BASE = "https://api.sleeper.app/v1"

CURRENT_SEASON = "2026"
CURRENT_LEAGUE_ID = "1312106085871529984"

# Every league_id in the dynasty chain, oldest first — trades can happen in
# any season (including the current one, before the draft), so unlike the
# matchup-stats script, we check every season here.
ALL_SEASON_LEAGUE_IDS = {
    "2024": "1088228735242891264",
    "2025": "1180623841944899584",
    "2026": CURRENT_LEAGUE_ID,
}

# Rounds/weeks to check for transactions each season. 0 covers preseason/
# offseason trades; most leagues won't have anything past week ~17, but
# checking a couple extra is harmless (empty responses just get skipped).
TRANSACTION_ROUNDS_TO_CHECK = range(0, 19)

POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]

# 2027 season points, for sorting roster players within each position.
# The 2027 league doesn't exist yet (Sleeper creates each new dynasty season's
# league_id around next year's draft), so this starts empty — every player
# sorts at 0 points until then, and the code falls back to alphabetical via
# the tiebreak below. Once Sleeper creates the 2027 league, set its ID and
# a regular-season week count here (same pattern as build_matchup_stats.py's
# SEASONS dict) and re-run this script.
SEASON_POINTS_LEAGUE_ID = None   # e.g. "1234567890123456789" once 2027 exists
SEASON_POINTS_WEEKS = 14

# Future draft years to enumerate picks for (each team owns one pick per round
# per year by default; trades reassign specific ones). Update as years pass.
FUTURE_DRAFT_YEARS = ["2027", "2028", "2029"]
DEFAULT_DRAFT_ROUNDS = 4  # overridden below if the league's draft settings say otherwise


def fetch_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "bird-crime-site/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_local(path):
    with open(path) as f:
        return json.load(f)


def get_players_db():
    if os.path.exists(PLAYERS_CACHE):
        print("Using cached player database (players_cache.json).")
        return load_local(PLAYERS_CACHE)
    print("Downloading Sleeper's full player database (large, one-time)...")
    players = fetch_json(f"{SLEEPER_BASE}/players/nfl", timeout=60)
    with open(PLAYERS_CACHE, "w") as f:
        json.dump(players, f)
    print(f"Cached to {PLAYERS_CACHE} ({len(players)} players).")
    return players


def player_display(pid, players_db):
    info = players_db.get(pid) or {}
    name = info.get("full_name")
    if not name:
        first, last = info.get("first_name"), info.get("last_name")
        name = f"{first or ''} {last or ''}".strip() or pid
    return name, info.get("position") or "UNK"


def build_player_season_points():
    """Sums each player's fantasy points across a season, for sorting roster
    lists. Returns {} (all zero) until SEASON_POINTS_LEAGUE_ID is set above."""
    if not SEASON_POINTS_LEAGUE_ID:
        return {}
    points = {}
    for week in range(1, SEASON_POINTS_WEEKS + 1):
        url = f"{SLEEPER_BASE}/league/{SEASON_POINTS_LEAGUE_ID}/matchups/{week}"
        try:
            week_data = fetch_json(url)
        except urllib.error.URLError:
            continue
        if not week_data:
            continue
        for entry in week_data:
            for pid, pts in (entry.get("players_points") or {}).items():
                points[pid] = points.get(pid, 0) + pts
    return points


def get_draft_rounds():
    try:
        drafts = fetch_json(f"{SLEEPER_BASE}/league/{CURRENT_LEAGUE_ID}/drafts")
        if drafts:
            rounds = drafts[0].get("settings", {}).get("rounds")
            if rounds:
                return rounds
    except urllib.error.URLError:
        pass
    return DEFAULT_DRAFT_ROUNDS


def build_current_rosters(players_db, managers_by_id):
    print(f"Fetching {CURRENT_SEASON} rosters...")
    rosters = fetch_json(f"{SLEEPER_BASE}/league/{CURRENT_LEAGUE_ID}/rosters")
    season_points = build_player_season_points()

    result = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        if not owner_id:
            continue
        by_position = {pos: [] for pos in POSITION_ORDER}
        by_position["OTHER"] = []
        for pid in (r.get("players") or []):
            name, pos = player_display(pid, players_db)
            bucket = pos if pos in by_position else "OTHER"
            by_position[bucket].append({"player_id": pid, "name": name, "_pts": season_points.get(pid, 0)})
        for pos in by_position:
            # Sort by 2027 season points (desc), then name (asc) as tiebreak.
            # Every player is 0 until SEASON_POINTS_LEAGUE_ID is set, so this
            # is effectively alphabetical for now — that's expected.
            by_position[pos].sort(key=lambda p: (-p["_pts"], p["name"]))
            for p in by_position[pos]:
                del p["_pts"]  # internal sort key only, not needed in the output
        result[owner_id] = {"by_position": by_position, "roster_id": r["roster_id"]}

    print(f"Fetching traded picks for {CURRENT_SEASON}...")
    try:
        traded_picks = fetch_json(f"{SLEEPER_BASE}/league/{CURRENT_LEAGUE_ID}/traded_picks")
    except urllib.error.URLError as e:
        print(f"  traded_picks fetch failed ({e}), skipping picks")
        traded_picks = []

    roster_id_to_owner = {r["roster_id"]: r.get("owner_id") for r in rosters}
    rounds = get_draft_rounds()
    print(f"  Enumerating {len(FUTURE_DRAFT_YEARS)} draft years x {rounds} rounds x {len(rosters)} teams...")

    # Full pick universe: every team owns 1 pick per round per future year by
    # default; traded_picks overrides specific (year, round, original_roster) keys.
    pick_owner_roster = {}
    for r in rosters:
        rid = r["roster_id"]
        for year in FUTURE_DRAFT_YEARS:
            for rnd in range(1, rounds + 1):
                pick_owner_roster[(year, rnd, rid)] = rid

    for pick in traded_picks:
        key = (str(pick.get("season")), pick.get("round"), pick.get("roster_id"))
        pick_owner_roster[key] = pick.get("owner_id")  # may add keys outside our enumerated range too

    for owner_id in result:
        result[owner_id]["picks_owned"] = []
        result[owner_id]["picks_traded_away"] = []

    for (year, rnd, original_roster), current_owner_roster in pick_owner_roster.items():
        original_owner_id = roster_id_to_owner.get(original_roster)
        current_owner_id = roster_id_to_owner.get(current_owner_roster)
        if not current_owner_id or current_owner_id not in result:
            continue
        label = f"{year} Round {rnd}"
        is_own = current_owner_id == original_owner_id
        entry = {"label": label, "is_own": is_own}
        if not is_own:
            entry["original_owner"] = managers_by_id.get(original_owner_id, {}).get("display_name", "Unknown")
        result[current_owner_id]["picks_owned"].append(entry)

        if not is_own and original_owner_id and original_owner_id in result:
            new_owner_name = managers_by_id.get(current_owner_id, {}).get("display_name", "Unknown")
            result[original_owner_id]["picks_traded_away"].append({"label": label, "new_owner": new_owner_name})

    for owner_id in result:
        result[owner_id]["picks_owned"].sort(key=lambda p: p["label"])
        result[owner_id]["picks_traded_away"].sort(key=lambda p: p["label"])

    with open(os.path.join(DATA_DIR, "current_rosters.json"), "w") as f:
        json.dump(result, f, indent=2)
    print(f"Wrote {os.path.join(DATA_DIR, 'current_rosters.json')}")



def build_transactions(players_db, managers_by_id):
    all_trades = []

    for season, league_id in ALL_SEASON_LEAGUE_IDS.items():
        print(f"Scanning {season} transactions...")
        try:
            rosters = fetch_json(f"{SLEEPER_BASE}/league/{league_id}/rosters")
        except urllib.error.URLError as e:
            print(f"  {season} rosters fetch failed ({e}), skipping season")
            continue
        roster_id_to_owner = {r["roster_id"]: r.get("owner_id") for r in rosters}

        seen_ids = set()
        for rnd in TRANSACTION_ROUNDS_TO_CHECK:
            url = f"{SLEEPER_BASE}/league/{league_id}/transactions/{rnd}"
            try:
                txns = fetch_json(url)
            except urllib.error.URLError:
                continue
            if not txns:
                continue

            for txn in txns:
                if txn.get("type") != "trade" or txn.get("status") != "complete":
                    continue
                txn_id = txn.get("transaction_id")
                if txn_id in seen_ids:
                    continue
                seen_ids.add(txn_id)

                roster_ids = txn.get("roster_ids") or []
                parties = {rid: {"owner_id": roster_id_to_owner.get(rid), "received": [], "sent": []} for rid in roster_ids}

                adds = txn.get("adds") or {}
                drops = txn.get("drops") or {}
                for pid, to_roster in adds.items():
                    name, pos = player_display(pid, players_db)
                    if to_roster in parties:
                        parties[to_roster]["received"].append(f"{name} ({pos})")
                for pid, from_roster in drops.items():
                    name, pos = player_display(pid, players_db)
                    if from_roster in parties:
                        parties[from_roster]["sent"].append(f"{name} ({pos})")

                for pick in (txn.get("draft_picks") or []):
                    label = f"{pick.get('season')} Round {pick.get('round')}"
                    to_roster = pick.get("owner_id")
                    from_roster = pick.get("previous_owner_id")
                    if to_roster in parties:
                        parties[to_roster]["received"].append(label + " pick")
                    if from_roster in parties:
                        parties[from_roster]["sent"].append(label + " pick")

                for budget in (txn.get("waiver_budget") or []):
                    amt = budget.get("amount")
                    to_roster = budget.get("receiver")
                    from_roster = budget.get("sender")
                    if to_roster in parties:
                        parties[to_roster]["received"].append(f"${amt} FAAB")
                    if from_roster in parties:
                        parties[from_roster]["sent"].append(f"${amt} FAAB")

                party_list = []
                for rid, p in parties.items():
                    owner_id = p["owner_id"]
                    display_name = managers_by_id.get(owner_id, {}).get("display_name", "Unknown")
                    party_list.append({
                        "owner_id": owner_id,
                        "display_name": display_name,
                        "received": p["received"],
                        "sent": p["sent"],
                    })

                all_trades.append({
                    "transaction_id": txn_id,
                    "season": season,
                    "week": txn.get("leg"),
                    "created": txn.get("created"),
                    "num_parties": len(party_list),
                    "parties": party_list,
                })

    all_trades.sort(key=lambda t: t.get("created") or 0, reverse=True)

    with open(os.path.join(DATA_DIR, "transactions.json"), "w") as f:
        json.dump(all_trades, f, indent=2)
    print(f"Wrote {os.path.join(DATA_DIR, 'transactions.json')} ({len(all_trades)} trades found)")


def main():
    players_db = get_players_db()
    managers_list = load_local(os.path.join(DATA_DIR, "managers.json"))
    managers_by_id = {m["user_id"]: m for m in managers_list}

    build_current_rosters(players_db, managers_by_id)
    build_transactions(players_db, managers_by_id)

    print("\nDone! Refresh manager.html and transactions.html to see everything populate.")


if __name__ == "__main__":
    main()

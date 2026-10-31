"""
Bird Crime — Head-to-Head, Position-Aware Lineup Efficiency, Opponents Record,
and Top Player Game builder
----------------------------------------------------------------------
Run this from inside this site's folder:

    python3 build_matchup_stats.py

What it does:
  1. Downloads Sleeper's full NFL player database (~10-15MB, cached locally
     to players_cache.json so repeat runs are fast — Sleeper asks that this
     endpoint not be hit more than once a day).
  2. Fetches every REGULAR SEASON week of matchup data for each completed
     season (playoff weeks are intentionally excluded — see the SEASONS
     comment below for why).
  3. Computes, per manager, per season AND career:
       - An all-time head-to-head win/loss matrix
       - Lineup efficiency = actual starters' points ÷ the best possible
         lineup, respecting real position eligibility
       - "Max PF" (used on Standings) = that same optimal-lineup total
       - Opponents Record = strength of schedule (each week's opponent's
         final season record, summed)
       - Top Player Game = the single best individual scoring performance
         by any player on a manager's roster, any week

Outputs:
  data/h2h.json               — head-to-head matrix
  data/lineup_efficiency.json — per-season and career efficiency + max PF
  data/advanced_stats.json    — opponents record + top player game

No third-party installs needed — only Python's standard library.
"""

import json
import os
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PLAYERS_CACHE = os.path.join(BASE_DIR, "players_cache.json")

# "weeks" is REGULAR SEASON ONLY (up to but not including playoff_week_start=15),
# matching Sleeper's own wins/losses/fpts fields, which never count playoff
# weeks. Playoff results are tracked separately via the bracket-based
# playoff_record stat, so including playoff weeks here would both mismatch
# the "actual PF" comparison basis and double-count playoff games.
SEASONS = {
    "2024": {"league_id": "1088228735242891264", "weeks": 14},
    "2025": {"league_id": "1180623841944899584", "weeks": 14},
    "2026": {"league_id": "1312106085871529984", "weeks": 14},
    # Setting "weeks" to the full 14-week regular season (not just how many
    # have been played) is safe and future-proof: the per-week fetch loop
    # already skips any week that returns no data, so this naturally picks up
    # more real weeks as the season progresses without needing to be updated.
}

SLEEPER_BASE = "https://api.sleeper.app/v1"

# Bird Crime roster slots: QB, RB, RB, WR, WR, TE, FLEX, FLEX, FLEX, SFLX (superflex).
# No kicker or team defense in this league's lineup.
SLOT_CONFIG = [
    {"name": "QB", "count": 1, "eligible": {"QB"}},
    {"name": "RB", "count": 2, "eligible": {"RB"}},
    {"name": "WR", "count": 2, "eligible": {"WR"}},
    {"name": "TE", "count": 1, "eligible": {"TE"}},
    {"name": "FLEX", "count": 3, "eligible": {"RB", "WR", "TE"}},
    {"name": "SFLX", "count": 1, "eligible": {"QB", "RB", "WR", "TE"}},
]
SLOT_ORDER_BY_POSITION = {
    "QB": ["QB", "SFLX"],
    "RB": ["RB", "FLEX", "SFLX"],
    "WR": ["WR", "FLEX", "SFLX"],
    "TE": ["TE", "FLEX", "SFLX"],
}

# ---- DEBUG MODE ----
# To audit a specific manager/season week-by-week, set DEBUG_OWNER_ID below to
# that manager's Sleeper user_id (find it in data/managers.json) and
# DEBUG_SEASON to the year. Leave DEBUG_OWNER_ID as None for normal runs.
DEBUG_OWNER_ID = None   # e.g. "576591884302987264"
DEBUG_SEASON = None     # e.g. "2025"


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


def optimal_lineup_points(players_points, player_positions, debug=False):
    """
    Computes the single highest-scoring LEGAL lineup for one team, one week.

    Step 1: fill dedicated slots (QB, RB, WR, TE) with the top scorer(s) at
    that exact position, independent of other positions.
    Step 2: fill FLEX from whatever's left (RB/WR/TE), by score.
    Step 3: fill SFLX from whatever's left (QB/RB/WR/TE), by score.
    Each player used at most once.
    """
    pool = dict(players_points)
    lineup = []

    for slot in SLOT_CONFIG:
        if slot["name"] in ("FLEX", "SFLX"):
            continue
        eligible_pos = next(iter(slot["eligible"]))
        candidates = sorted(
            [(pid, pts) for pid, pts in pool.items() if player_positions.get(pid) == eligible_pos],
            key=lambda kv: kv[1], reverse=True
        )
        for pid, pts in candidates[:slot["count"]]:
            lineup.append((slot["name"], pid, pts))
            del pool[pid]

    for slot_name in ("FLEX", "SFLX"):
        slot = next(s for s in SLOT_CONFIG if s["name"] == slot_name)
        candidates = sorted(
            [(pid, pts) for pid, pts in pool.items() if player_positions.get(pid) in slot["eligible"]],
            key=lambda kv: kv[1], reverse=True
        )
        for pid, pts in candidates[:slot["count"]]:
            lineup.append((slot_name, pid, pts))
            del pool[pid]

    total = sum(pts for _, _, pts in lineup)

    if debug:
        for slot_name, pid, pts in lineup:
            print(f"      {slot_name:5s} {pid:>10s} ({player_positions.get(pid)})  {pts:.2f}")
        print(f"      {'TOTAL':5s} {'':>10s}      {total:.2f}")

    return total


def main():
    managers = load_local(os.path.join(DATA_DIR, "managers.json"))
    players_db = get_players_db()
    player_positions = {pid: info.get("position") for pid, info in players_db.items()}

    def player_display_name(pid):
        info = players_db.get(pid) or {}
        name = info.get("full_name")
        if name:
            return name
        first, last = info.get("first_name"), info.get("last_name")
        if first or last:
            return f"{first or ''} {last or ''}".strip()
        return pid

    # Build both the roster-id->owner map AND each manager's current win/loss
    # record from ONE live fetch per season, rather than reading the static
    # data/seasons/{year}.json snapshot — that file only gets regenerated when
    # process_data.py is re-run, so for the current in-progress season it can
    # be stale (e.g. still showing 0-0 from before the season started), which
    # was silently producing wrong Opponents Record numbers.
    final_standings_by_season = {}
    roster_owner_by_season = {}
    for season, cfg in SEASONS.items():
        print(f"Fetching {season} rosters...")
        rosters = fetch_json(f"{SLEEPER_BASE}/league/{cfg['league_id']}/rosters")
        roster_owner_by_season[season] = {r["roster_id"]: r["owner_id"] for r in rosters}
        final_standings_by_season[season] = {
            r["owner_id"]: {
                "wins": (r.get("settings") or {}).get("wins", 0),
                "losses": (r.get("settings") or {}).get("losses", 0),
            }
            for r in rosters if r.get("owner_id")
        }

    head_to_head = {}

    def h2h_bucket(a, b):
        head_to_head.setdefault(a, {})
        head_to_head[a].setdefault(b, {"wins": 0, "losses": 0, "ties": 0})
        return head_to_head[a][b]

    efficiency_by_season = {season: {} for season in SEASONS}
    opponents_record = {season: {} for season in SEASONS}
    top_player_game = {}
    highest_weekly_score = []  # list of {owner_id, points, season, week} — supports ties at the max

    def consider_weekly_score(owner_id, season, week, actual_points):
        if not highest_weekly_score or actual_points > highest_weekly_score[0]["points"]:
            highest_weekly_score.clear()
            highest_weekly_score.append({"owner_id": owner_id, "points": round(actual_points, 2), "season": season, "week": week})
        elif actual_points == highest_weekly_score[0]["points"]:
            highest_weekly_score.append({"owner_id": owner_id, "points": round(actual_points, 2), "season": season, "week": week})

    def consider_top_player(owner_id, season, week, players_points):
        for pid, pts in players_points.items():
            best = top_player_game.get(owner_id)
            if best is None or pts > best["points"]:
                top_player_game[owner_id] = {
                    "points": round(pts, 2),
                    "player_name": player_display_name(pid),
                    "season": season,
                    "week": week,
                }

    for season, cfg in SEASONS.items():
        owner_by_roster = roster_owner_by_season[season]
        for week in range(1, cfg["weeks"] + 1):
            url = f"{SLEEPER_BASE}/league/{cfg['league_id']}/matchups/{week}"
            try:
                week_data = fetch_json(url)
            except urllib.error.URLError as e:
                print(f"  {season} week {week}: fetch failed ({e}), skipping")
                continue
            if not week_data:
                continue
            print(f"  {season} week {week}: {len(week_data)} rosters")

            for entry in week_data:
                roster_id = entry.get("roster_id")
                owner_id = owner_by_roster.get(roster_id)
                if not owner_id:
                    continue
                starters_points = entry.get("starters_points") or []
                players_points = entry.get("players_points") or {}
                if not starters_points or not players_points:
                    continue

                actual = sum(starters_points)
                is_debug_target = (DEBUG_OWNER_ID and owner_id == DEBUG_OWNER_ID and season == DEBUG_SEASON)
                if is_debug_target:
                    print(f"\n    -- {season} week {week} (actual starters' total: {actual:.2f}) --")
                optimal = optimal_lineup_points(players_points, player_positions, debug=is_debug_target)
                if optimal <= 0:
                    continue

                bucket = efficiency_by_season[season].setdefault(
                    owner_id, {"actual_sum": 0.0, "optimal_sum": 0.0, "weeks": 0}
                )
                bucket["actual_sum"] += actual
                bucket["optimal_sum"] += optimal
                bucket["weeks"] += 1

                if is_debug_target:
                    print(f"    Week {week} Max PF: {optimal:.2f}  |  Running season Max PF total: {bucket['optimal_sum']:.2f}")

                consider_top_player(owner_id, season, week, players_points)
                consider_weekly_score(owner_id, season, week, actual)

            by_matchup = {}
            for entry in week_data:
                mid = entry.get("matchup_id")
                if mid is None:
                    continue
                by_matchup.setdefault(mid, []).append(entry)

            for mid, pair in by_matchup.items():
                if len(pair) != 2:
                    continue
                a, b = pair
                # Sleeper pre-populates the full season's matchup pairings in advance,
                # so future/unplayed weeks show up here too. Checking for the
                # presence of starters_points isn't reliable — Sleeper fills it with
                # a zero-value array (not empty) even for unplayed weeks, which is
                # still truthy in Python. Checking the actual point totals is the
                # reliable signal: a real played game essentially never ends 0-0.
                pts_a, pts_b = a.get("points", 0), b.get("points", 0)
                if pts_a == 0 and pts_b == 0:
                    continue
                owner_a = owner_by_roster.get(a["roster_id"])
                owner_b = owner_by_roster.get(b["roster_id"])
                if not owner_a or not owner_b:
                    continue
                if pts_a > pts_b:
                    h2h_bucket(owner_a, owner_b)["wins"] += 1
                    h2h_bucket(owner_b, owner_a)["losses"] += 1
                elif pts_b > pts_a:
                    h2h_bucket(owner_b, owner_a)["wins"] += 1
                    h2h_bucket(owner_a, owner_b)["losses"] += 1
                else:
                    h2h_bucket(owner_a, owner_b)["ties"] += 1
                    h2h_bucket(owner_b, owner_a)["ties"] += 1

                final = final_standings_by_season.get(season, {})
                opp_a_final = final.get(owner_b)
                opp_b_final = final.get(owner_a)
                if opp_a_final:
                    bucket_a = opponents_record[season].setdefault(owner_a, {"wins": 0, "losses": 0})
                    bucket_a["wins"] += opp_a_final["wins"]
                    bucket_a["losses"] += opp_a_final["losses"]
                if opp_b_final:
                    bucket_b = opponents_record[season].setdefault(owner_b, {"wins": 0, "losses": 0})
                    bucket_b["wins"] += opp_b_final["wins"]
                    bucket_b["losses"] += opp_b_final["losses"]

    with open(os.path.join(DATA_DIR, "h2h.json"), "w") as f:
        json.dump(head_to_head, f, indent=2)
    print(f"Wrote {os.path.join(DATA_DIR, 'h2h.json')}")

    output = {"by_season": {}, "career": {}}
    career_actual, career_optimal, career_weeks = {}, {}, {}

    for season, owners in efficiency_by_season.items():
        output["by_season"][season] = {}
        for owner_id, b in owners.items():
            if b["optimal_sum"] <= 0:
                continue
            output["by_season"][season][owner_id] = {
                "efficiency_pct": round((b["actual_sum"] / b["optimal_sum"]) * 100, 2),
                "actual_points": round(b["actual_sum"], 2),
                "max_pf": round(b["optimal_sum"], 2),
                "weeks_counted": b["weeks"],
            }
            career_actual[owner_id] = career_actual.get(owner_id, 0.0) + b["actual_sum"]
            career_optimal[owner_id] = career_optimal.get(owner_id, 0.0) + b["optimal_sum"]
            career_weeks[owner_id] = career_weeks.get(owner_id, 0) + b["weeks"]

    for owner_id, optimal_sum in career_optimal.items():
        if optimal_sum <= 0:
            continue
        output["career"][owner_id] = {
            "efficiency_pct": round((career_actual[owner_id] / optimal_sum) * 100, 2),
            "actual_points": round(career_actual[owner_id], 2),
            "max_pf": round(optimal_sum, 2),
            "weeks_counted": career_weeks[owner_id],
        }

    with open(os.path.join(DATA_DIR, "lineup_efficiency.json"), "w") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {os.path.join(DATA_DIR, 'lineup_efficiency.json')}")

    advanced = {
        "opponents_record": opponents_record,
        "top_player_game": top_player_game,
        "highest_weekly_score": highest_weekly_score,
    }
    with open(os.path.join(DATA_DIR, "advanced_stats.json"), "w") as f:
        json.dump(advanced, f, indent=2)
    print(f"Wrote {os.path.join(DATA_DIR, 'advanced_stats.json')}")

    print("\nDone! Refresh standings.html, records.html, and managers.html to see everything populate.")


if __name__ == "__main__":
    main()

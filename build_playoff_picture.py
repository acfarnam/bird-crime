"""
Bird Crime — Playoff Seeding + Top 6 Picks snapshot builder
-----------------------------------------------------------
Run this from inside this site's folder, AFTER the week's games you want to
count are finished:

    python3 build_playoff_picture.py          # counts every completed week
    python3 build_playoff_picture.py 8        # or: count weeks 1-8 only
    python3 build_playoff_picture.py 8 1      # same, but fill in the SECOND-newest
                                              # issue (0 = newest, 1 = the one before...)
                                              # handy for rebuilding an older issue's tables

What it does:
  1. Pulls this season's regular-season matchups from Sleeper (only weeks that
     are completed - a week that is only partly played, like Thursday night of
     the current week, is NOT counted).
  2. Builds the standings and seeds the top 6 using the league rules:
       - Record first (a tie counts as half a win)
       - Tiebreaker 1: head-to-head record among the tied teams
       - Tiebreaker 2: total regular-season points for (PF)
  3. Builds the "top 6 picks if the season ended today" list: the 6 teams that
     would miss the playoffs, ordered by Max PF (best possible lineup points),
     LOWEST Max PF picks first.
  4. Writes data/playoff_picture.json and adds the two tables to the NEWEST
     issue in data/crime_report.json (playoff_seeding, top_picks).

It reuses the lineup math from build_matchup_stats.py, so keep both files in
the same folder (and keep players_cache.json, which that script created).
No third-party installs needed.
"""

import json
import os
import sys
from fractions import Fraction

import build_matchup_stats as bms

SEASON = "2026"
PLAYOFF_TEAMS = 6
REG_SEASON_WEEKS = 14
DATA_DIR = bms.DATA_DIR
SLEEPER_BASE = bms.SLEEPER_BASE

TIEBREAK_LABELS = {"H2H": "Head-to-head", "PF": "Points for"}


def pct(wins, losses, ties):
    games = wins + losses + ties
    return Fraction(2 * wins + ties, 2 * games) if games else Fraction(0)


def break_tie(group, games, points_for):
    """
    Orders a group of teams that have the SAME record.
    Returns a list of (owner_id, tiebreak) where tiebreak is "H2H", "PF" or None.

    Head-to-head is measured only in games played between teams in the group.
    A team that hasn't played anyone in the group counts as .500 there. If H2H
    separates only some of the teams, H2H is re-applied to whatever is still
    tied (using only games among those teams) before falling back to PF.
    """
    if len(group) == 1:
        return [(group[0], None)]

    members = set(group)
    rec = {o: [0, 0, 0] for o in group}  # wins, losses, ties
    for a, b, pts_a, pts_b in games:
        if a in members and b in members:
            if pts_a > pts_b:
                rec[a][0] += 1
                rec[b][1] += 1
            elif pts_b > pts_a:
                rec[b][0] += 1
                rec[a][1] += 1
            else:
                rec[a][2] += 1
                rec[b][2] += 1

    def h2h_score(o):
        w, l, t = rec[o]
        return pct(w, l, t) if (w + l + t) else Fraction(1, 2)

    buckets = {}
    for o in group:
        buckets.setdefault(h2h_score(o), []).append(o)

    if len(buckets) == 1:  # H2H can't separate anyone -> points for
        ordered = sorted(group, key=lambda o: points_for[o], reverse=True)
        return [(o, "PF") for o in ordered]

    result = []
    for score in sorted(buckets, reverse=True):
        bucket = buckets[score]
        if len(bucket) == 1:
            result.append((bucket[0], "H2H"))
        else:
            result.extend(break_tie(bucket, games, points_for))
    return result


def compute_picture(records, points_for, max_pf, games):
    """
    records:    {owner: [wins, losses, ties]}
    points_for: {owner: total regular-season points}
    max_pf:     {owner: total optimal-lineup points}
    games:      [(owner_a, owner_b, points_a, points_b), ...] played games only
    Returns (seeding, picks): seeding is a list of the PLAYOFF_TEAMS seeds,
    picks is the non-playoff teams in draft order (lowest Max PF first).
    """
    by_pct = {}
    for o, (w, l, t) in records.items():
        by_pct.setdefault(pct(w, l, t), []).append(o)

    order = []
    for p in sorted(by_pct, reverse=True):
        order.extend(break_tie(by_pct[p], games, points_for))

    seeding = []
    for i, (o, tb) in enumerate(order[:PLAYOFF_TEAMS], start=1):
        seeding.append({"seed": i, "owner_id": o, "tiebreak": tb})

    out_of_playoffs = [o for o, _ in order[PLAYOFF_TEAMS:]]
    picks = sorted(out_of_playoffs, key=lambda o: max_pf[o])
    return seeding, picks


def fmt_record(w, l, t):
    return f"{w}-{l}-{t}" if t else f"{w}-{l}"


def main():
    league_id = bms.SEASONS[SEASON]["league_id"]

    issue_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    # Which weeks are finished?
    if len(sys.argv) > 1:
        through_week = int(sys.argv[1])
    else:
        state = bms.fetch_json(f"{SLEEPER_BASE}/state/nfl")
        through_week = int(state.get("week", 1)) - 1
    through_week = max(1, min(through_week, REG_SEASON_WEEKS))
    print(f"Counting regular-season weeks 1-{through_week}.")
    print("(If that's wrong, run it like:  python3 build_playoff_picture.py 8)\n")

    users = bms.fetch_json(f"{SLEEPER_BASE}/league/{league_id}/users")
    rosters = bms.fetch_json(f"{SLEEPER_BASE}/league/{league_id}/rosters")
    owner_by_roster = {r["roster_id"]: r["owner_id"] for r in rosters if r.get("owner_id")}
    info = {}
    for u in users:
        meta = u.get("metadata") or {}
        info[u["user_id"]] = {
            "manager": u.get("display_name") or u["user_id"],
            "team": meta.get("team_name") or u.get("display_name") or u["user_id"],
        }

    players_db = bms.get_players_db()
    player_positions = {pid: p.get("position") for pid, p in players_db.items()}

    records = {o: [0, 0, 0] for o in owner_by_roster.values()}
    points_for = {o: 0.0 for o in records}
    max_pf = {o: 0.0 for o in records}
    games = []

    for week in range(1, through_week + 1):
        week_data = bms.fetch_json(f"{SLEEPER_BASE}/league/{league_id}/matchups/{week}")
        if not week_data:
            continue
        by_matchup = {}
        for entry in week_data:
            if entry.get("matchup_id") is not None:
                by_matchup.setdefault(entry["matchup_id"], []).append(entry)
        for pair in by_matchup.values():
            if len(pair) != 2:
                continue
            a, b = pair
            pts_a, pts_b = a.get("points", 0), b.get("points", 0)
            if pts_a == 0 and pts_b == 0:
                continue  # unplayed
            oa, ob = owner_by_roster.get(a["roster_id"]), owner_by_roster.get(b["roster_id"])
            if not oa or not ob:
                continue
            games.append((oa, ob, pts_a, pts_b))
            points_for[oa] += pts_a
            points_for[ob] += pts_b
            if pts_a > pts_b:
                records[oa][0] += 1
                records[ob][1] += 1
            elif pts_b > pts_a:
                records[ob][0] += 1
                records[oa][1] += 1
            else:
                records[oa][2] += 1
                records[ob][2] += 1
            for entry, owner in ((a, oa), (b, ob)):
                pp = entry.get("players_points") or {}
                if pp:
                    max_pf[owner] += bms.optimal_lineup_points(pp, player_positions)

    # Sanity check against Sleeper's own record (only meaningful if you counted
    # every completed week and no mid-week games are included).
    for r in rosters:
        o = r.get("owner_id")
        s = r.get("settings") or {}
        theirs = [s.get("wins", 0), s.get("losses", 0), s.get("ties", 0)]
        if o in records and records[o] != theirs:
            print(f"  NOTE: {info.get(o, {}).get('manager', o)} computed {fmt_record(*records[o])} "
                  f"but Sleeper currently shows {fmt_record(*theirs)} (normal if Sleeper has "
                  f"already counted part of this week).")

    # Show the head-to-head games inside every group of teams with the same
    # record, so the tiebreaker results can be checked by eye.
    by_record = {}
    for o, r in records.items():
        by_record.setdefault(tuple(r), []).append(o)
    for rec_t, grp in sorted(by_record.items(), key=lambda kv: pct(*kv[0]), reverse=True):
        if len(grp) < 2:
            continue
        members = set(grp)
        print(f"\nTIED AT {fmt_record(*rec_t)} ({len(grp)} teams) - games between these teams:")
        vs_group = {o: [0, 0, 0] for o in grp}
        for a, b, pts_a, pts_b in games:
            if a in members and b in members:
                if pts_a == pts_b:
                    print(f"  {info[a]['manager']} tied {info[b]['manager']}  {pts_a:.2f}-{pts_b:.2f}")
                    vs_group[a][2] += 1
                    vs_group[b][2] += 1
                else:
                    w, l, pw, pl = (a, b, pts_a, pts_b) if pts_a > pts_b else (b, a, pts_b, pts_a)
                    print(f"  {info[w]['manager']} beat {info[l]['manager']}  {pw:.2f}-{pl:.2f}")
                    vs_group[w][0] += 1
                    vs_group[l][1] += 1
        print("  Record vs. the group:")
        for o in sorted(grp, key=lambda x: points_for[x], reverse=True):
            print(f"    {info[o]['manager']}: {fmt_record(*vs_group[o])}   (PF {points_for[o]:.2f})")

    seeding, picks = compute_picture(records, points_for, max_pf, games)

    seeding_out = []
    print("\nPLAYOFF SEEDING")
    for s in seeding:
        o = s["owner_id"]
        row = {
            "seed": s["seed"],
            "team": info[o]["team"],
            "manager": info[o]["manager"],
            "record": fmt_record(*records[o]),
            "points_for": round(points_for[o], 2),
            "tiebreak": TIEBREAK_LABELS.get(s["tiebreak"], ""),
        }
        seeding_out.append(row)
        print(f"  {row['seed']}. {row['team']} ({row['manager']})  {row['record']}  "
              f"PF {row['points_for']:.2f}  {row['tiebreak'] or ''}")

    picks_out = []
    print("\nTOP 6 PICKS (non-playoff teams, lowest Max PF first)")
    for i, o in enumerate(picks[:6], start=1):
        row = {
            "pick": i,
            "team": info[o]["team"],
            "manager": info[o]["manager"],
            "max_pf": round(max_pf[o], 2),
        }
        picks_out.append(row)
        print(f"  {i}. {row['team']} ({row['manager']})  {fmt_record(*records[o])}  Max PF {row['max_pf']:.2f}")

    with open(os.path.join(DATA_DIR, "playoff_picture.json"), "w") as f:
        json.dump({"through_week": through_week, "playoff_seeding": seeding_out, "top_picks": picks_out}, f, indent=2)

    report_path = os.path.join(DATA_DIR, "crime_report.json")
    with open(report_path) as f:
        report = json.load(f)
    issue = report["issues"][issue_index]
    issue["snapshot_through_week"] = through_week
    issue["playoff_seeding"] = seeding_out
    issue["top_picks"] = picks_out
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nAdded both tables to the Crime Report issue: \"{issue['headline']}\".")
    print("Refresh crime-report.html to see them.")


if __name__ == "__main__":
    main()

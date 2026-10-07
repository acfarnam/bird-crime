/* ==========================================================================
   BIRD CRIME — data loading
   - Historical seasons (2024, 2025) are read from cached /data JSON files.
   - The current season (2026) is fetched LIVE from the Sleeper API so
     standings/records stay accurate as the season plays out.
   Update CURRENT_LEAGUE_ID / CURRENT_SEASON below if the league renews.
   ========================================================================== */

const CURRENT_LEAGUE_ID = "1312106085871529984";
const CURRENT_SEASON = "2026";
const HISTORICAL_SEASONS = ["2024", "2025"]; // seasons served from cached JSON

const SLEEPER_BASE = "https://api.sleeper.app/v1";

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch ${url}: ${res.status}`);
  return res.json();
}

/** Build a standings array (same shape as our cached season files) from live Sleeper data. */
function buildStandingsFromLive(rosters) {
  const rows = rosters.map(r => {
    const s = r.settings || {};
    const wins = s.wins || 0, losses = s.losses || 0, ties = s.ties || 0;
    const games = wins + losses + ties;
    const pf = (s.fpts || 0) + (s.fpts_decimal || 0) / 100;
    const pa = (s.fpts_against || 0) + (s.fpts_against_decimal || 0) / 100;
    return {
      roster_id: r.roster_id,
      owner_id: r.owner_id,
      wins, losses, ties,
      points_for: Math.round(pf * 100) / 100,
      points_against: Math.round(pa * 100) / 100,
      win_pct: games ? Math.round((wins / games) * 1000) / 1000 : 0,
      ppg: games ? Math.round((pf / games) * 100) / 100 : 0,
      papg: games ? Math.round((pa / games) * 100) / 100 : 0,
      placement: null, // playoffs settle at season end
      playoff_wins: 0,
      playoff_losses: 0,
    };
  });
  rows.sort((a, b) => b.wins - a.wins || b.points_for - a.points_for);
  return rows;
}

/** Fetch the current season live: users + rosters from Sleeper. Falls back to null on failure. */
async function loadCurrentSeasonLive() {
  try {
    const [users, rosters] = await Promise.all([
      fetchJSON(`${SLEEPER_BASE}/league/${CURRENT_LEAGUE_ID}/users`),
      fetchJSON(`${SLEEPER_BASE}/league/${CURRENT_LEAGUE_ID}/rosters`),
    ]);
    const standings = buildStandingsFromLive(rosters);
    return {
      season: CURRENT_SEASON,
      standings,
      champion_roster_id: null,
      runner_up_roster_id: null,
      third_place_roster_id: null,
      live: true,
      liveUsers: users, // team names can shift mid-season; keep alongside
    };
  } catch (err) {
    console.warn("Live Sleeper fetch failed, falling back to cached data:", err);
    return null;
  }
}

/** Load everything the site needs: managers registry, league summary, and all seasons (cached + live). */
async function loadLeagueData() {
  const [managers, summary] = await Promise.all([
    fetchJSON("data/managers.json"),
    fetchJSON("data/league_summary.json"),
  ]);

  const seasons = {};
  await Promise.all(
    HISTORICAL_SEASONS.map(async (s) => {
      seasons[s] = await fetchJSON(`data/seasons/${s}.json`);
    })
  );

  const live = await loadCurrentSeasonLive();
  if (live) {
    seasons[CURRENT_SEASON] = live;
    // Refresh team names for the current season from live data
    live.liveUsers.forEach(u => {
      const m = managers.find(m => m.user_id === u.user_id);
      if (m) {
        m.seasons[CURRENT_SEASON] = { team_name: (u.metadata && u.metadata.team_name) || u.display_name };
      }
    });
  } else {
    // fallback to cached snapshot if present
    try {
      seasons[CURRENT_SEASON] = await fetchJSON(`data/seasons/${CURRENT_SEASON}.json`);
    } catch (e) { /* no cached snapshot available */ }
  }

  return { managers, summary, seasons, allSeasons: [...HISTORICAL_SEASONS, CURRENT_SEASON] };
}

/** Helper: manager lookup by owner/user id */
function managerById(managers, id) {
  return managers.find(m => m.user_id === id);
}

/** Helper: team name for a manager in a given season, with fallback */
function teamNameFor(manager, season) {
  if (!manager) return "Unknown";
  return (manager.seasons[season] && manager.seasons[season].team_name) || manager.display_name;
}

/** Helper: initials for avatar fallback */
function initials(name) {
  return (name || "?").slice(0, 2).toUpperCase();
}

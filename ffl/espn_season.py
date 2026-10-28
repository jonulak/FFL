"""ESPN + Sleeper I/O for the season, flattened to plain dicts for ffl.season.

Everything here touches the network except flatten_box_player / merge_weekly, which are the
pure seams the tests cover.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import requests

from . import calibrate, consensus, db, espn_write, leagues
from .sources import cbs, fleaflicker, scoring_key, sharks
from . import season as S
from .adp import norm_name
from .flight import Flight
from . import config
from .config import SEASON, league, raw_league_get
from .players import fetch_pool
from .settings import load, rescoring
from .sleeper import TEAM_FIX, fetch as fetch_sleeper_season, score

SLEEPER_WEEK = "https://api.sleeper.app/projections/nfl/{season}/{week}"
# the same rows as the projections, actual stats: offensive snaps and the team's, per player
SLEEPER_STATS = "https://api.sleeper.app/stats/nfl/{season}/{week}"
SLEEPER_POS = ["QB", "RB", "WR", "TE", "K", "DEF"]
INJURIES = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
# the feed's rows carry no athlete id, only player-card links with it in the path
INJ_ID_RE = re.compile(r"/id/(\d+)/")
CACHE_S = 1800
# espn_api reads current_week, the teams and their records once, in League.__init__. Under
# launchd this process lives for months, so a League built at boot would freeze the week, the
# standings and journal.sync_results' range forever. Every builder goes through fresh_league(),
# which rebuilds it once an hour; POST /refresh forces it immediately.
LEAGUE_TTL_S = 3600
_LEAGUE_BUILT_AT = 0.0  # the newest League build of any league (the schedule caches no longer key on it)
_BUILT: dict[str, float] = {}  # league key -> when fresh_league last rebuilt that league's League
# week -> (fetched_at, lookup), every week asked for and none dropped: the Matchup board prices on the scoring
# week, the Roster and Players pages on the ROS week (the next one once the week's games are played), and with
# one slot each build evicted the other's 5-25 s lookup. An entry refetches only once it is CACHE_S old.
_SEASON_CACHE: dict[tuple[str | None, int], tuple[float, dict]] = {}  # (league key or None, week)
_SL: dict[int, tuple[float, list]] = {}  # week -> (expires_at, Sleeper's raw weekly rows): one fetch for every league
_SL_SCORED: dict[tuple[int, str], tuple[list, dict]] = {}  # (week, sources.scoring_key) -> (the raw rows it parsed, points)
_SL_FLIGHT = Flight()  # week -> its Sleeper fetch in flight, shared by the builds asking at once
_SNAPS: dict[int, tuple[float, dict]] = {}  # week -> (expires_at, sleeper snaps)
_INJ: list = []  # [expires_at, data]


def refresh_league() -> None:
    """Drop every cached League (every league's) so the next fresh_league() fetches ESPN again."""
    global _LEAGUE_BUILT_AT
    _LEAGUE_BUILT_AT = 0.0
    _BUILT.clear()
    _SCHED[0] = None  # the NFL's schedule too, so POST /refresh re-reads it
    cache_clear = getattr(league, "cache_clear", None)  # None when tests stub `league`
    if cache_clear is not None:
        cache_clear()


def fresh_league(ref: leagues.LeagueRef):
    """config.league(ref), rebuilt when that league's build is older than LEAGUE_TTL_S. Only its
    own League is evicted, so one league's hour running out never costs another its League; _LEAGUE_BUILT_AT
    moves with any rebuild (nothing keys on it now: the pro schedule is NFL-wide, on its own clock)."""
    global _LEAGUE_BUILT_AT
    now = time.time()
    if now - _BUILT.get(ref.key, 0.0) >= LEAGUE_TTL_S:
        evict = getattr(league, "evict", None)  # None when tests stub `league`
        if evict is not None:
            evict(ref)
        elif (cache_clear := getattr(league, "cache_clear", None)) is not None:
            cache_clear()
        _BUILT[ref.key] = now
        _LEAGUE_BUILT_AT = now
    return league(ref)


def flatten_box_player(bp, season_proj: float | None = None) -> dict:
    proj = float(getattr(bp, "projected_points", 0) or 0)
    bye = bool(getattr(bp, "on_bye_week", False))
    # espn-api's BoxPlayer writes the string "None" for pro_opponent when it has no schedule
    # entry, and game_date is a naive datetime in the host's zone (fromtimestamp).
    opp = getattr(bp, "pro_opponent", None)
    gd = getattr(bp, "game_date", None)
    return {"player_id": bp.playerId, "name": bp.name, "pos": bp.position, "team": bp.proTeam,
            # BoxPlayer already defaults slot_position to "FA" (free agents come back as
            # BoxPlayers too); the getattr is belt-and-braces for a plain Player.
            "slot": getattr(bp, "slot_position", "FA"), "bye": bye,
            "injury": bp.injuryStatus or "ACTIVE", "proj_espn": proj, "proj": proj,  # proj_espn: merge_weekly's input, popped there
            "spread": None, "n_src": 1 if proj > 0 else 0, "proj_src": {"espn": proj} if proj > 0 else {}, "actual": float(getattr(bp, "points", 0) or 0),
            "game_played": int(getattr(bp, "game_played", 0) or 0),
            "season_proj": float(season_proj if season_proj is not None else getattr(bp, "projected_total_points", 0) or 0),
            "ros_proj": None, "ros_n": None,
            "ros_spread": None, "return_week": None, "injury_note": None,
            # the league's strategy, "Usage-adjusted ROS" (`uv run python -m ffl.strategy show`): years in the league (apply_exp) and the hurt
            # teammates at his position (apply_injuries) — the two stories the blend cannot price
            "exp": None, "ahead": [],
            "opponent": None if bye or opp in (None, "None") else opp,
            "kickoff": gd.astimezone().isoformat() if gd is not None and not bye else None,
            # BoxPlayer carries the opponent but not the venue; apply_home fills this from the
            # pro schedule. None means unknown (no game, or the schedule view failed).
            "home": None}


def stat_row(bp, settings: dict) -> dict[str, float] | None:
    """A BoxPlayer's week as the raw stat row the live feed carries (stat id -> value), for the
    stat line and the scoring breakdown once the feed is gone (after Monday, or an API restart).

    espn-api renames ESPN's stat ids (`breakdown`, PLAYER_STATS_MAP) and the map is not one to
    one: a few names stand for two ids that carry the same value (3 and 22, passing yards). The
    name goes back to the id the league scores; failing that, the lowest. An id the map does not
    know keeps ESPN's own key ("214") and passes through. None before the game (no breakdown)."""
    from espn_api.football.constant import PLAYER_STATS_MAP
    bd = getattr(bp, "breakdown", None) or {}
    if not bd:
        return None
    scored = {int(x["stat_id"]) for x in settings.get("scoring", [])}
    ids: dict[str, int] = {}
    for sid, name in sorted(PLAYER_STATS_MAP.items()):
        if name not in ids or (sid in scored and ids[name] not in scored):
            ids[name] = sid
    out = {}
    for k, v in bd.items():
        sid = int(k) if str(k).isdigit() else ids.get(k)
        if sid is not None and v is not None:
            out[str(sid)] = float(v)
    return out


_HOME: dict[tuple[int, int], tuple[dict, dict[str, bool]]] = {}  # (week, id(doc)) -> (doc, team -> hosts)


def parse_home_by_team(data: dict, week: int) -> dict[str, bool]:
    """Which pro teams host their game in `week`, from ESPN's proTeamSchedules_wl view —
    the same document espn-api reads for the opponent, which it keeps and the venue it drops."""
    from espn_api.football.constant import PRO_TEAM_MAP
    out: dict[str, bool] = {}
    for t in data.get("settings", {}).get("proTeams", []):
        games = (t.get("proGamesByScoringPeriod") or {}).get(str(week)) or []
        abbr = PRO_TEAM_MAP.get(t.get("id"))
        if games and abbr and abbr != "None":
            out[abbr] = games[0].get("homeProTeamId") == t["id"]
    return out


_SCHED: list = [None]  # [(expires_at, doc) or None]: the proTeamSchedules_wl document, the NFL's whichever League asked
# The parses below key on the document itself (the entry holds it, so a freed document's id is never reused for a hit)
_BYE: dict[int, tuple[dict, dict[str, int]]] = {}  # id(doc) -> (doc, team -> bye week)


def _schedule_doc(lg) -> dict:
    """ESPN's proTeamSchedules_wl view (venues, bye weeks and kickoffs all read it), fetched once per LEAGUE_TTL_S
    whichever League asks; refresh_league() drops it. A stubbed league without a request object reads as an empty
    document, uncached; a failed fetch is not cached."""
    req = getattr(lg, "espn_request", None)  # tests stub the league without one
    if req is None:
        return {}
    held = _SCHED[0]  # one read: refresh_league() may set None at any moment
    if held is None or time.time() >= held[0]:
        held = (time.time() + LEAGUE_TTL_S, req.get_pro_schedule())
        _SCHED[0] = held  # one assignment
    return held[1]


_PARSE_LOCK = threading.Lock()


def _parsed(cache: dict, doc: dict, key, parse):
    """`parse(doc)` kept in `cache` under `key` + the document's identity; a new document drops the old parses."""
    with _PARSE_LOCK:  # builds run on threads; the loop below must not race another's insert or delete
        hit = cache.get((key, id(doc)))
        if hit is None or hit[0] is not doc:
            for k in [k for k, v in cache.items() if v[0] is not doc]:
                del cache[k]
            hit = cache[(key, id(doc))] = (doc, parse(doc))
        return hit[1]


def home_by_team(lg, week: int) -> dict[str, bool]:
    """parse_home_by_team over the live view, cached for the life of the League object. A failed
    fetch returns {} so every card shows the opponent without a venue rather than no page."""
    try:
        data = _schedule_doc(lg)
    except Exception as e:  # noqa: BLE001 — venue is decoration, the call is not
        print(f"pro schedule unavailable, venues unknown: {e}")
        return {}  # not cached: the next request retries
    return _parsed(_HOME, data, week, lambda d: parse_home_by_team(d, week))


def parse_bye_by_team(data: dict) -> dict[str, int]:
    """Each pro team's bye week from the same document, keyed by the abbreviation espn-api gives a
    player's proTeam (PRO_TEAM_MAP by id, as parse_home_by_team); the placeholder team has none."""
    from espn_api.football.constant import PRO_TEAM_MAP
    out: dict[str, int] = {}
    for t in data.get("settings", {}).get("proTeams", []):
        abbr = PRO_TEAM_MAP.get(t.get("id")) or t.get("abbrev")
        if t.get("id") and abbr and t.get("byeWeek"):
            out[abbr] = int(t["byeWeek"])
    return out


def bye_by_team(lg) -> dict[str, int]:
    """parse_bye_by_team over the live view, cached like home_by_team. Without it, season.games_left
    counts a bye still ahead as a game, so pace_gap runs one game high for that player."""
    try:
        data = _schedule_doc(lg)
    except Exception as e:  # noqa: BLE001
        print(f"pro schedule unavailable, byes unknown: {e}")
        return {}
    return _parsed(_BYE, data, None, parse_bye_by_team)


def parse_kickoffs_by_week(data: dict) -> dict[int, list[datetime]]:
    """Every pro game's kickoff, by scoring period, from the same document: an aware datetime off
    ESPN's millisecond epoch. Both clubs list the game, so it is taken once by its id."""
    seen: dict[int, set] = {}
    out: dict[int, list[datetime]] = {}
    for t in data.get("settings", {}).get("proTeams", []):
        for wk, games in (t.get("proGamesByScoringPeriod") or {}).items():
            for g in games or []:
                gid, ms = g.get("id"), g.get("date")
                if ms is None or gid in seen.setdefault(int(wk), set()):
                    continue
                seen[int(wk)].add(gid)
                out.setdefault(int(wk), []).append(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))
    return out


_KICKS: dict[int, tuple[dict, dict[int, list[datetime]]]] = {}  # id(doc) -> (doc, week -> kickoffs)


def kickoffs_by_week(lg) -> dict[int, list[datetime]]:
    """parse_kickoffs_by_week over the live view, cached like bye_by_team; {} on a failed fetch,
    which leaves the ROS week at ESPN's."""
    try:
        data = _schedule_doc(lg)
    except Exception as e:  # noqa: BLE001
        print(f"pro schedule unavailable, ROS week is ESPN's: {e}")
        return {}
    return _parsed(_KICKS, data, None, parse_kickoffs_by_week)


def ros_week_for(lg, week: int) -> int:
    """season.ros_week off the schedule: `week + 1` once every game in `week` is played."""
    return S.ros_week(week, kickoffs_by_week(lg).get(week, []))


def apply_home(players: list[dict], home: dict[str, bool]) -> list[dict]:
    """Set `home` for every player with a game this week; players without one stay None."""
    for p in players:
        p["home"] = home.get(p["team"]) if p.get("opponent") else None
    return players


# --- recent form (the league's strategy, "Recent form" (`uv run python -m ffl.strategy show`)) ---------------------------------------------------

OPPORTUNITY = {"K": ("attemptedFieldGoals", "attemptedExtraPoints"),
               "QB": ("passingAttempts", "rushingAttempts", "receivingTargets")}
TOUCHES = ("rushingAttempts", "receivingTargets")


def game_from_stats(week: int, st: dict, pos: str, final: bool) -> dict:
    """One week of an espn-api player's `stats` as the game row season.recent_form reads.

    `breakdown` is the raw stat line (ESPN's names where espn-api knows them); a row before
    kickoff carries the projection and no breakdown. A defence is active in any week it has a
    line; everyone else needs an opportunity. Snaps are Sleeper's, joined later by apply_form.
    """
    bd = st.get("breakdown") or {}
    g = lambda k: float(bd.get(k) or 0)  # noqa: E731
    if pos == "D/ST":
        opp, active = None, bool(bd)
    else:
        opp = sum(g(k) for k in OPPORTUNITY.get(pos, TOUCHES))
        active = opp > 0
    return {"week": week, "pts": float(st.get("points") or 0), "proj": float(st.get("projected_points") or 0),
            "opp": opp, "tgt": g("receivingTargets"), "rec_yd": g("receivingYards"),
            "car": g("rushingAttempts"), "rec": g("receivingReceptions"), "pass_att": g("passingAttempts"),
            "snaps": None, "team_snaps": None, "final": final, "active": active}


def player_games(obj, weeks: list[int], week: int) -> list[dict]:
    """The weeks asked for, off an espn-api Player/BoxPlayer's `stats` (a Player from the team
    roster carries every week; a BoxPlayer or free agent only the week it was fetched for).
    Earlier weeks are final; this week's game is final when espn-api says it has been played."""
    stats = getattr(obj, "stats", None) or {}
    out = []
    for w in weeks:
        st = stats.get(w)
        if st:
            final = w < week or int(getattr(obj, "game_played", 100) or 0) >= 100
            out.append(game_from_stats(w, st, obj.position, final))
    return out


def form_weeks(week: int) -> list[int]:
    """The weeks the window is looked for in: the last FORM_WEEKS, ending this week."""
    return list(range(max(1, week - S.FORM_WEEKS + 1), week + 1))


def parse_sleeper_snaps(rows: list[dict]) -> dict[tuple[str, str], tuple[float, float]]:
    """(offensive snaps, team offensive snaps) keyed like parse_sleeper_rows. A row without snaps
    (they land a day or so after the game) is left out, so the game shows no snap share yet."""
    out: dict[tuple[str, str], tuple[float, float]] = {}
    for p in rows:
        pl, st = p.get("player", {}), p.get("stats", {})
        pos = pl.get("position")
        if pos not in S.SNAP_POSITIONS or not st.get("off_snp") or not st.get("tm_off_snp"):
            continue
        out[(norm_name(f"{pl.get('first_name','')} {pl.get('last_name','')}"), pos)] = (
            float(st["off_snp"]), float(st["tm_off_snp"]))
    return out


def parse_sleeper_usage(rows: list[dict]) -> dict[tuple[str, str], dict[str, float | None]]:
    """Air yards and red-zone looks keyed like parse_sleeper_snaps, off the same rows. Sleeper's air
    yards are *completed* air yards (the yards a caught ball travelled past the line; never more than
    the receiving yards, a catch behind the line negative), not intended ones: his (`air_yd`, with
    his catches), his club's that week (`team_air`, its passers' pass_air_yd summed, which is its
    receivers' rec_air_yd summed; None when nobody completed one), red-zone targets, rushes and pass
    attempts, and a passer's own air yards with his completions. A stat Sleeper leaves off a row is a
    zero; a row without snaps is left out, as there (the game has not landed)."""
    team_air: dict[str, float] = {}
    for p in rows:
        team_air[p.get("team")] = team_air.get(p.get("team"), 0.0) + float((p.get("stats") or {}).get("pass_air_yd") or 0)
    out: dict[tuple[str, str], dict[str, float | None]] = {}
    for p in rows:
        pl, st = p.get("player", {}), p.get("stats", {})
        pos = pl.get("position")
        if pos not in S.SNAP_POSITIONS or not st.get("off_snp"):
            continue
        g = lambda k: float(st.get(k) or 0)  # noqa: E731
        out[(norm_name(f"{pl.get('first_name','')} {pl.get('last_name','')}"), pos)] = {
            "air_yd": g("rec_air_yd"), "catches": g("rec"), "team_air": team_air.get(p.get("team")) or None,
            "rz_tgt": g("rec_rz_tgt"), "rz_att": g("rush_rz_att"), "rz_pass": g("pass_rz_att"),
            "pass_air": g("pass_air_yd"), "cmp": g("pass_cmp")}
    return out


def parse_sleeper_exp(rows: list[dict]) -> dict[tuple[str, str], int]:
    """Years in the league (0 for a rookie) off the same rows, keyed like parse_sleeper_snaps;
    a row without the field is left out."""
    out: dict[tuple[str, str], int] = {}
    for p in rows:
        pl = p.get("player", {})
        if pl.get("years_exp") is None or not pl.get("position"):
            continue
        out[(norm_name(f"{pl.get('first_name','')} {pl.get('last_name','')}"), pl["position"])] = int(pl["years_exp"])
    return out


def fetch_sleeper_stats(week: int) -> list[dict]:
    """Sleeper's raw stat rows for the week (snaps and the player record both come off them)."""
    r = requests.get(SLEEPER_STATS.format(season=SEASON, week=week),
                     params=[("season_type", "regular"), ("order_by", "pts_ppr")] + [("position[]", p) for p in sorted(S.SNAP_POSITIONS)],
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_sleeper_snaps(week: int) -> dict[tuple[str, str], tuple[float, float]]:
    return parse_sleeper_snaps(fetch_sleeper_stats(week))


def _stats_rows(week: int) -> list[dict]:
    """One week's raw Sleeper stat rows, cached for CACHE_S; [] for this build when the fetch
    fails (no snap share for that game, the points columns unaffected), and not cached."""
    hit = _SNAPS.get(week)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        data = fetch_sleeper_stats(week)
    except requests.RequestException as e:
        print(f"sleeper stats unavailable for week {week}: {e}")
        return []
    _SNAPS[week] = (time.time() + CACHE_S, data)
    return data


def _snaps_for(weeks: list[int], snaps: dict | None) -> dict[int, dict]:
    """Sleeper's snaps for each week (parse_sleeper_snaps over _stats_rows), all weeks in flight
    together; an injected `snaps` (tests) is taken as is."""
    if snaps is not None:
        return snaps
    with ThreadPoolExecutor(max(len(weeks), 1)) as pool:
        return dict(zip(weeks, pool.map(lambda w: parse_sleeper_snaps(_stats_rows(w)), weeks)))


def _exp_for(weeks: list[int], snaps: dict | None) -> dict[tuple[str, str], int]:
    """Years in the league off the same cached rows (the latest week wins); {} when the snaps
    were injected, since then nothing was fetched."""
    if snaps is not None:
        return {}
    out: dict[tuple[str, str], int] = {}
    for w in sorted(weeks):
        out.update(parse_sleeper_exp(_stats_rows(w)))
    return out


def _usage_for(weeks: list[int], snaps: dict | None) -> dict[int, dict]:
    """parse_sleeper_usage for each week off the same cached rows; {} when the snaps were
    injected, since then nothing was fetched."""
    if snaps is not None:
        return {}
    return {w: parse_sleeper_usage(_stats_rows(w)) for w in weeks}


def apply_exp(players: list[dict], exp: dict[tuple[str, str], int]) -> list[dict]:
    """`exp` on every player Sleeper has a record for; None otherwise."""
    return [{**p, "exp": exp.get(_form_key(p))} for p in players]


def _form_key(p: dict) -> tuple[str, str]:
    return (norm_name(p["name"]), p["pos"])


def apply_form(players: list[dict], games_by_id: dict[int, list[dict]], snaps_by_week: dict[int, dict],
               week: int, byes: dict[str, int], refs: dict[int, dict[str, float]] | None = None,
               usage_by_week: dict[int, dict] | None = None) -> list[dict]:
    """`form` on every player: season.recent_form over his game rows with Sleeper's snaps and air
    yards / red-zone looks (`usage_by_week`, parse_sleeper_usage) joined on (name, position) per
    week and that week's position reference (`refs`, season.snap_reference; without one every game
    weighs one), paced against his ROS (season.ros_proj) over the games he has left from `week`
    (the ROS week). None when he has no game in the window."""
    out = []
    for p in players:
        q = dict(p)
        games = []
        for g in games_by_id.get(p["player_id"], []):
            snap = snaps_by_week.get(g["week"], {}).get(_form_key(p)) if p["pos"] in S.SNAP_POSITIONS else None
            ref = (refs or {}).get(g["week"], {}).get(p["pos"])
            use = (usage_by_week or {}).get(g["week"], {}).get(_form_key(p)) if p["pos"] in S.SNAP_POSITIONS else None
            games.append({**g, **(use or {}), "snaps": snap[0], "team_snaps": snap[1], "ref": ref} if snap else {**g, **(use or {}), "ref": ref})
        q["form"] = S.recent_form(games, p["pos"], S.ros_proj(p, week), S.games_left(week, byes.get(p["team"])))
        out.append(q)
    return out


def snap_refs(snaps_by_week: dict[int, dict]) -> dict[int, dict[str, float]]:
    """season.snap_reference for each week that has snaps."""
    return {w: S.snap_reference(s) for w, s in snaps_by_week.items() if s}


def fetch_sleeper_rows(week: int) -> list[dict]:
    """Sleeper's raw weekly projection rows (player + stat line); ffl.props reads the stat lines."""
    r = requests.get(SLEEPER_WEEK.format(season=SEASON, week=week),
                     params=[("season_type", "regular"), ("order_by", "pts_ppr")] + [("position[]", p) for p in SLEEPER_POS],
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    return r.json()


def parse_sleeper_rows(rows: list[dict], ks: dict) -> dict[tuple[str, str], float]:
    """Sleeper's weekly rows keyed the way merge_weekly looks them up.

    Rows without `gp` are inactive players, whose stats are all zero — averaging that against
    ESPN would halve a real projection, so they are skipped and left out of the consensus.
    """
    out: dict[tuple[str, str], float] = {}
    for p in rows:
        pl, st = p.get("player", {}), p.get("stats", {})
        pos = pl.get("position")
        if pos not in SLEEPER_POS or not st or not st.get("gp"):
            continue
        team = TEAM_FIX.get(pl.get("team") or "", pl.get("team") or "")
        if pos == "DEF":
            out[(team, "D/ST")] = float(st.get("pts_std") or 0)
        elif pos == "K":
            out[(norm_name(f"{pl.get('first_name','')} {pl.get('last_name','')}"), "K")] = float(st.get("pts_std") or 0)
        else:
            out[(norm_name(f"{pl.get('first_name','')} {pl.get('last_name','')}"), pos)] = round(score(st, ks[pos]), 1)
    return out


# A source's name for a player -> ESPN's, when norm_name alone does not match them (add as the
# unmatched log names them)
ALIASES: dict[tuple[str, str], tuple[str, str]] = {
    ("chigoziem okonkwo", "TE"): ("chig okonkwo", "TE"),     # CBS, FantasySharks
    ("josh palmer", "WR"): ("joshua palmer", "WR"),           # CBS
    ("kenneth gainwell", "RB"): ("kenny gainwell", "RB"),     # FantasySharks
}
_POOL_KEYS: list = []   # [the ESPN pool's source keys], set by season_lookup for the weekly unmatched log


def canon(d: dict[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    return {ALIASES.get(k, k): v for k, v in d.items()} if ALIASES else d


def unmatched(src: dict[tuple[str, str], float], pool: set[tuple[str, str]], top: int = 100) -> list[str]:
    """The source's top-`top` rows by points that match no ESPN player: a name to add to ALIASES."""
    best = sorted(src.items(), key=lambda kv: -kv[1])[:top]
    return [f"{k[0]} {k[1]}" for k, _ in best if k not in pool]


WEEKLY_SOURCES = {"cbs": cbs.fetch_week, "sharks": sharks.fetch_week, "fleaflicker": fleaflicker.fetch_week}


def _on(registry: dict) -> dict:
    """The registry's sources this host asks: one in FFL_SOURCES_OFF is left out, absent from the consensus rather than failing."""
    return {name: fn for name, fn in registry.items() if not config.source_off(name)}


def weekly_sources(week: int, settings: dict) -> dict[str, dict[tuple[str, str], float]]:
    """Every weekly source's projections for the week, fetched together (each cached; a failed one is {})."""
    ks = rescoring(settings)
    weekly = _on(WEEKLY_SOURCES)
    with ThreadPoolExecutor(len(weekly) + 1) as pool:
        sl = pool.submit(_sleeper_for, week, settings)
        rest = {name: pool.submit(fn, week, ks) for name, fn in weekly.items()}
        out = {name: f.result() for name, f in rest.items()}
        try:
            out = {"sleeper": sl.result(), **out}
        except requests.RequestException as e:
            print(f"sleeper week {week} unavailable: {e}")
            out = {"sleeper": {}, **out}
    out = {name: canon(d) for name, d in out.items()}
    if _POOL_KEYS:
        for name, d in out.items():
            if miss := unmatched(d, _POOL_KEYS[0]):
                print(f"week {week} source {name}: no ESPN player for {', '.join(miss[:10])}")
    return out


def source_key(p: dict) -> tuple[str, str]:
    # ESPN's abbreviations differ from Sleeper's (WSH/WAS, JAC/JAX): normalise before matching.
    return ((TEAM_FIX.get(p["team"], p["team"]), "D/ST") if p["pos"] == "D/ST"
            else (norm_name(p["name"]), p["pos"]))


def source_values(p: dict, sources: dict[str, dict]) -> dict[str, float]:
    """His number from each source that has him; ESPN's 0 is a placeholder, not a projection."""
    key = source_key(p)
    vals = {name: float(d[key]) for name, d in sources.items() if d.get(key) is not None}
    if (p.get("proj_espn") or 0) > 0:
        vals["espn"] = float(p["proj_espn"])
    return vals


def merge_weekly(players: list[dict], sources: dict[str, dict]) -> list[dict]:
    """One weekly projection per player (the league's strategy, "Lineup" (`uv run python -m ffl.strategy show`)): the consensus of every source that has
    him (consensus.combine). ESPN's own number is an input only and leaves the dict here; each source's
    number rides along in proj_src (highest first) for the projection dialog."""
    out = []
    for p in players:
        q = dict(p)
        espn = float(q.pop("proj_espn", 0) or 0)
        vals = source_values(p, sources)
        proj, spread, n = consensus.combine(vals, consensus.weights_for("week", p["pos"]))
        q["proj"] = proj if proj is not None else espn
        q["spread"], q["n_src"] = spread, n
        q["proj_src"] = {s: round(v, 1) for s, v in sorted(vals.items(), key=lambda kv: -kv[1])}
        out.append(q)
    return out


def projection_rows_for(players: list[dict], sources: dict, lookup: dict[int, dict], now: datetime,
                        ros_ahead: bool) -> tuple[list[dict], list[dict]]:
    """Each source's number per player for projection_rows: weekly rows while his game is ahead; ROS
    rows likewise, or all of them while the ROS week is ahead of ESPN's (the league's strategy, "Waivers" (`uv run python -m ffl.strategy show`))."""
    def ahead(p):
        return p.get("kickoff") is not None and datetime.fromisoformat(p["kickoff"]) > now
    week = [{"player_id": p["player_id"], "source": s, "points": v}
            for p in players if ahead(p) for s, v in source_values(p, sources).items()]
    ros = [{"player_id": p["player_id"], "source": s, "points": v}
           for p in players if ros_ahead or ahead(p) or p.get("bye")
           for s, v in ((lookup.get(p["player_id"]) or {}).get("ros_src") or {}).items()]
    return week, ros


def record_projections(*, ref: leagues.LeagueRef) -> dict:
    """The notify tick's step (every 15 min): every rostered player's per-source numbers into
    projection_rows, so `ffl.calibrate sources` can fit weights from what each source said.
    `ref` is the league whose rosters and rows these are."""
    lg = fresh_league(ref)
    settings = load(ref)
    week = lg.current_week
    rw = ros_week_for(lg, week)
    players = [flatten_box_player(bp) for lineup in _boxes_by_team(lg, week, ref=ref).values() for bp in lineup]
    wk, ros = projection_rows_for(players, weekly_sources(week, settings), season_lookup(settings, rw, ref=ref),
                                  datetime.now().astimezone(), ros_ahead=rw > week)
    return {"week": db.put_projection_rows(week, "week", wk, ref=ref), "ros": db.put_projection_rows(rw, "ros", ros, ref=ref)}


def my_team_id(lg, ref: leagues.LeagueRef) -> int:
    """leagues.my_team_id of `ref`'s settings, but a failure is a RuntimeError: the API's `except Exception` paths must catch it (SystemExit would escape them)."""
    try:
        return leagues.my_team_id({}, ref)  # the membership's: no settings read (ffl-soc.6.2.3)
    except SystemExit as e:
        raise RuntimeError(str(e)) from e


def owner_name(team) -> str:
    """The manager's real name ("Henry Kubacki"); a co-managed team's owners joined with " & ".
    First and last as ESPN holds them, trimmed and capitalised (one is stored lower case); the
    display name is an ESPN handle ("ESPNFAN7221…") and never shown. "" when ESPN lists no owner."""
    names = (" ".join(w[:1].upper() + w[1:] for w in f"{o.get('firstName') or ''} {o.get('lastName') or ''}".split())
             for o in getattr(team, "owners", None) or [])
    return " & ".join(n for n in names if n)


def build_side(team, players: list[dict], settings: dict) -> dict:
    """One matchup side from already-merged player dicts."""
    order = {s: i for i, s in enumerate(S.SLOT_ORDER)}
    lineup = sorted((p for p in players if p["slot"] not in S.BENCH_SLOTS), key=lambda p: order.get(p["slot"], 99))
    bench = [p for p in players if p["slot"] == "BE"]
    ir = [p for p in players if p["slot"] == "IR"]
    cur, opt = S.current_lineup(players), S.optimal_lineup(players, settings)
    # abbrev and logo as the League page carries them, so the scorebug can show the club's mark
    # and the record (today's, whichever week the board shows), so the bug reads like the League's
    return {"team_id": team.team_id, "team": team.team_name, "owner": owner_name(team),
            "wins": getattr(team, "wins", 0), "losses": getattr(team, "losses", 0), "ties": getattr(team, "ties", 0),
            "abbrev": getattr(team, "team_abbrev", "") or "",
            "logo": getattr(team, "logo_url", "") or "", "lineup": lineup, "bench": bench, "ir": ir,
            "optimal": opt, "optimal_swaps": S.lineup_diff(cur, opt),
            "proj_total": S.lineup_total(cur), "optimal_total": S.lineup_total(opt),
            "actual_total": round(sum(p["actual"] for p in lineup), 2)}


def sleeper_ros(weeks: list[dict[tuple[str, str], float]]) -> dict[tuple[str, str], float]:
    """Sleeper's weekly projections summed per player over the given weeks. A bye, or a week an
    injured player is out, is simply a week with no row for him."""
    out: dict[tuple[str, str], float] = {}
    for wk in weeks:
        for key, pts in wk.items():
            out[key] = round(out.get(key, 0.0) + float(pts), 1)
    return out


ROS_SOURCES = {"cbs": cbs.fetch_ros, "sharks": sharks.fetch_ros}


def merge_season(espn_rows: list[dict], sleeper, ros: dict[str, dict] | None = None) -> dict[int, dict]:
    """Season and rest-of-season projections by ESPN player id.

    Season: ESPN and Sleeper averaged like board.merge_sleeper. ROS (the league's strategy, "Waivers" (`uv run python -m ffl.strategy show`)): the
    consensus (consensus.combine) of every ROS source that has him — Sleeper's remaining weeks summed,
    CBS's and FantasySharks' rest-of-season pages — None when none does (season.ros_proj then scales
    the season number). `ros_src` keeps each source's number for the recorder; apply_season never
    copies it onto a player. Non-D/ST match on (normalised name, position); D/ST on team, with ESPN's
    abbreviation normalised through TEAM_FIX. An ESPN 0 is a placeholder row, not a projection.
    """
    rows = sleeper.to_dict("records") if hasattr(sleeper, "to_dict") else list(sleeper)
    by_name = {(r["key"], r["pos"]): r["proj_sleeper"] for r in rows if r["pos"] != "D/ST"}
    by_team = {r["team"]: r["proj_sleeper"] for r in rows if r["pos"] == "D/ST"}
    out: dict[int, dict] = {}
    for row in espn_rows:
        team = TEAM_FIX.get(row["team"], row["team"])
        key = (team, "D/ST") if row["pos"] == "D/ST" else (norm_name(row["name"]), row["pos"])
        sl = by_team.get(team) if row["pos"] == "D/ST" else by_name.get(key)
        espn = row["proj"] if (row.get("proj") or 0) > 0 else None
        have = [float(x) for x in (espn, sl) if x is not None]
        vals = {name: float(d[key]) for name, d in (ros or {}).items() if d.get(key) is not None}
        proj, spread, n = consensus.combine(vals, consensus.weights_for("ros", row["pos"]))
        out[row["player_id"]] = {"season_proj": round(sum(have) / len(have), 1) if have else 0.0,
                                 "ros_proj": proj, "ros_spread": spread, "ros_n": n, "ros_src": vals}
    return out


def apply_season(players: list[dict], lookup: dict[int, dict]) -> list[dict]:
    """Season projections from the full-pool lookup, where it has the player.

    box_scores comes from ESPN's mMatchupScore view, which carries only weekly stats — every
    BoxPlayer's projected_total_points is 0 there — so the flattened value is only a fallback.
    """
    out = []
    for p in players:
        q = dict(p)
        got = lookup.get(p["player_id"])
        if got:
            for k in ("season_proj", "ros_proj", "ros_spread", "ros_n"):
                q[k] = got.get(k)
        out.append(q)
    return out


def _remaining_sleeper_weeks(week: int, settings: dict) -> list[dict] | None:
    """Sleeper's projections for weeks week..18, fetched together; None when any week failed,
    because a sum missing a week would undercount every player rather than a few."""
    weeks = list(range(week, S.NFL_WEEKS + 1))
    try:
        with ThreadPoolExecutor(max(len(weeks), 1)) as pool:
            return list(pool.map(lambda w: _sleeper_for(w, settings), weeks))
    except requests.RequestException as e:
        print(f"sleeper weekly projections unavailable, ROS rests on the other sources: {e}")
        return None


def season_lookup(settings: dict, week: int, ref) -> dict[int, dict]:
    """The whole player pool's season and rest-of-season projections, cached for CACHE_S per week
    (one ESPN call, Sleeper's season and every remaining week, CBS's and FantasySharks' pages).
    `ref` is the league whose ESPN scoring prices the pool."""
    now = time.time()
    cache_key = (ref.key, week)
    hit = _SEASON_CACHE.get(cache_key)
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    # four independent sources of one to four seconds each, in flight together: in sequence they
    # were most of what the Refresh button waited on
    ks = rescoring(settings)
    ros_sources = _on(ROS_SOURCES)
    with ThreadPoolExecutor(3 + len(ros_sources)) as pool:
        espn_f = pool.submit(fetch_pool, ref=ref)
        sleeper_f = pool.submit(fetch_sleeper_season, settings)
        weeks_f = pool.submit(_remaining_sleeper_weeks, week, settings)
        ros_f = {name: pool.submit(fn, ks, week) for name, fn in ros_sources.items()}
        weeks = weeks_f.result()
        ros = {name: f.result() for name, f in ros_f.items()}
        if weeks is not None:
            ros = {"sleeper": sleeper_ros(weeks), **ros}
        ros = {name: canon(d) for name, d in ros.items()}
        espn_rows = espn_f.result()
        lookup = merge_season(espn_rows, sleeper_f.result(), ros)
    pool_keys = {((TEAM_FIX.get(r["team"], r["team"]), "D/ST") if r["pos"] == "D/ST"
                  else (norm_name(r["name"]), r["pos"])) for r in espn_rows}
    _POOL_KEYS[:] = [pool_keys]
    for name, d in ros.items():
        if miss := unmatched(d, pool_keys):
            print(f"ROS source {name}: no ESPN player for {', '.join(miss[:10])}")
    _SEASON_CACHE[cache_key] = (now, lookup)
    return lookup


def _return_date(raw) -> date | None:
    """One returnDate, or None when ESPN writes something that is not a date ("TBD", "").

    Eight hundred rows go through here on every fetch: one unparseable one must cost its own
    return date, not the whole /week and /waivers build.
    """
    try:
        return date.fromisoformat(raw) if raw else None
    except (TypeError, ValueError):
        return None


def parse_injuries(payload: dict) -> dict[int, dict]:
    """ESPN's public injuries feed keyed by ESPN player id (= BoxPlayer.playerId).

    A row without a player-card link cannot be matched to a roster, so it is dropped. `Active`
    rows are kept: their comment is real news (a depth-chart move, a return to practice) even
    though they carry no `details` and so no return date. A team lists a player's rows
    newest-first, so the first row for an id wins.
    """
    from espn_api.football.constant import PRO_TEAM_MAP
    out: dict[int, dict] = {}
    for team in payload.get("injuries") or []:
        try:
            abbr = PRO_TEAM_MAP.get(int(team.get("id")))  # the feed writes the id as a string
        except (TypeError, ValueError):
            abbr = None
        for row in team.get("injuries") or []:
            ath = row.get("athlete") or {}
            hrefs = (link.get("href") or "" for link in ath.get("links", []))
            pid = next((int(m.group(1)) for h in hrefs if (m := INJ_ID_RE.search(h))), None)
            if pid is None or pid in out:
                continue
            det = row.get("details") or {}
            note = (row.get("shortComment") or "").strip() or (row.get("longComment") or "").strip()
            out[pid] = {"return_date": _return_date(det.get("returnDate")),
                        "note": note or None, "body_part": det.get("type"),
                        # who he is, for `ahead`: the club (ESPN's team id), position and status
                        "team": abbr if abbr and abbr != "None" else None,
                        "pos": (ath.get("position") or {}).get("abbreviation"),
                        "status": row.get("status"), "name": ath.get("displayName")}
    return out


def fetch_injuries() -> dict[int, dict]:
    """Every team's injury rows, cached for CACHE_S; an outage serves the last good data.

    Nothing is logged on failure: season.injury_terms falls back to its per-status constants, so
    a missing return week is degraded advice, not an error.
    """
    if _INJ and _INJ[0] > time.time():
        return _INJ[1]
    try:
        # site.api.espn.com answers 403 to anything browser-shaped in User-Agent (Mozilla/…, or a
        # descriptive custom string) and 200 to requests' default — so no header here.
        r = requests.get(INJURIES, timeout=30)
        r.raise_for_status()
        data = parse_injuries(r.json())
    except requests.RequestException:
        return _INJ[1] if _INJ else {}
    _INJ[:] = [time.time() + CACHE_S, data]
    return data


def apply_injuries(players: list[dict], injuries: dict[int, dict], week1_start: date | None) -> list[dict]:
    """The return week and injury note from the feed, where it has the player.

    Without week1_start — settings stored before that key existed — a return date cannot
    be turned into a week, so only the note is applied and season.games_out stays None.
    """
    def ret_week(row: dict) -> int | None:
        rd = row.get("return_date")
        return S.week_of(rd, week1_start) if rd and week1_start else None

    # the report by club and position, for `ahead`: the hurt (not "Active") teammates at his spot
    hurt: dict[tuple[str, str], list[tuple[int, dict]]] = {}
    for pid, row in injuries.items():
        if row.get("team") and row.get("pos") and (row.get("status") or "Active") != "Active":
            hurt.setdefault((row["team"], row["pos"]), []).append((pid, row))
    out = []
    for p in players:
        q = dict(p)
        got = injuries.get(p["player_id"])
        if got:
            q["injury_note"] = got["note"]
            q["return_week"] = ret_week(got)
        q["ahead"] = [{"name": row.get("name"), "status": (row.get("status") or "").upper(), "return_week": ret_week(row)}
                      for pid, row in hurt.get((p["team"], p["pos"]), []) if pid != p["player_id"]]
        out.append(q)
    return out


def _sleeper_for(week: int, settings: dict) -> dict:
    """Sleeper's weekly projections rescored to `settings`. The raw rows are fetched once for every league and kept
    CACHE_S per week (POST /refresh clears _SL); each scoring's parse of them is kept until the rows are refetched."""
    ks = rescoring(settings)
    rows = _sleeper_rows(week)
    key = (week, scoring_key(ks))
    hit = _SL_SCORED.get(key)
    if hit and hit[0] is rows:
        return hit[1]
    data = parse_sleeper_rows(rows, ks)
    _SL_SCORED[key] = (rows, data)
    return data


def _sleeper_rows(week: int) -> list:
    hit = _SL.get(week)
    if hit and hit[0] > time.time():
        return hit[1]

    def fetch():
        hit = _SL.get(week)
        if hit and hit[0] > time.time():  # the flight before this one landed it
            return hit[1]
        rows = fetch_sleeper_rows(week)
        _SL[week] = (time.time() + CACHE_S, rows)
        return rows

    return _SL_FLIGHT.do(week, fetch)


def _season_for(settings: dict, season: dict | None, week: int, *, ref: leagues.LeagueRef) -> dict:
    return season if season is not None else season_lookup(settings, week, ref=ref)


def ros_source(lookup: dict[int, dict]) -> str:
    """"sources" when the lookup priced anyone off Sleeper's weeks or CBS, else "season": the page
    says so, because a whole pool on the frozen season totals is the bug this replaced."""
    return "sources" if any(v.get("ros_proj") is not None for v in lookup.values()) else "season"


def _injuries_for(injuries: dict | None) -> dict:
    return injuries if injuries is not None else fetch_injuries()


def _week1_start(settings: dict) -> date | None:
    """The stored settings' week 1 Tuesday, absent from settings stored before ffl.settings wrote it."""
    d = settings.get("week1_start")
    return date.fromisoformat(d) if d else None


BOXES_TTL_S = 60
_BOXES: dict[tuple[str, int], tuple[float, object, list]] = {}  # (league key, week) -> (expires_at, the League fetched from, box scores)


_BOXES_FLIGHT = Flight()  # (league key, week, League) -> its box-score fetch in flight, shared by the builders asking at once
_BOXES_EPOCH = [0]  # bumped by forget_current_boxes: a fetch that began before the bump does not keep its boxes


def forget_current_boxes(*, ref: leagues.LeagueRef) -> None:
    """Drop the current week's box scores of `ref`'s league (a league move changed its rosters); finished weeks never change.
    The epoch stays global: a fetch of another league that began before the bump just does not keep its boxes (over-invalidating is safe).
    The non-final stored rows are expired too (fetched_at 0), so a pre-move read in the table never counts as a
    60 s hit; a final row's fetched_at is the correction pass's throttle and stays."""
    _BOXES_EPOCH[0] += 1
    key = ref.key
    for k in [k for k, hit in list(_BOXES.items()) if k[0] == key and hit[0] != math.inf]:  # a builder thread may be adding
        _BOXES.pop(k, None)
    try:
        db.expire_live_boxes(ref=ref)
    except db.DBError as e:
        logging.getLogger(__name__).warning("stored box scores not expired: %s", e)


def _fetch_box_doc(lg, week: int) -> dict:
    """One week's box scores as ESPN answers them, in JSON that stores: what espn-api's League.box_scores reads
    (mMatchupScore + mScoreboard for the week's matchup period, that week's pro games, the positional ratings),
    asked the same way. _parse_box_doc turns it back into the same BoxScores (tests/test_box_doc.py pins it)."""
    period, scoring = lg.currentMatchupPeriod, lg.current_week
    if week:  # a week after ESPN's (a league before its first week) is asked as itself too: the current period's sides carry no rosters
        scoring = week
        period = next((mp for mp, weeks in lg.settings.matchup_periods.items() if week in weeks), period)
    filters = {"schedule": {"filterMatchupPeriodIds": {"value": [period]}}}
    data = lg.espn_request.league_get(params={"view": ["mMatchupScore", "mScoreboard"], "scoringPeriodId": scoring},
                                      headers={"x-fantasy-filter": json.dumps(filters)})
    return {"scoring_period": scoring, "matchups": data["schedule"],
            "pro_schedule": {str(t): list(g) for t, g in lg._get_pro_schedule(scoring).items()},
            "ratings": lg._get_positional_ratings(scoring)}


def _parse_box_doc(lg, doc: dict) -> list:
    """_fetch_box_doc's answer as espn-api's BoxScores, each side's team id swapped for lg's Team as it does."""
    from espn_api.football.box_score import BoxScore
    # JSON made the pro team ids strings; BoxPlayer looks its club up by int, and a miss reads as a bye
    sched = {int(t): tuple(g) for t, g in doc["pro_schedule"].items()}
    sp = doc["scoring_period"]
    boxes = [BoxScore(m, sched, doc["ratings"], sp, lg.year) for m in doc["matchups"]]
    teams = {t.team_id: t for t in lg.teams}
    for b in boxes:
        b.home_team = teams.get(b.home_team, b.home_team)
        b.away_team = teams.get(b.away_team, b.away_team)
    return boxes


def box_hash(doc: dict) -> str:
    """sha256 of a box doc in canonical JSON, so equal documents hash equal whatever their key order."""
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _stored_boxes(lg, week: int, ref: leagues.LeagueRef) -> tuple[list | None, float, bool]:
    """The stored row parsed, when it was read (0 when none) and whether that read was final (made after the
    week finished). A DB error or a doc espn-api can no longer parse counts as no row: logged, and the week is
    read from ESPN."""
    try:
        row = db.box_doc(week, ref=ref)
    except db.DBError as e:
        logging.getLogger(__name__).warning("stored box scores for week %s unreadable: %s", week, e)
        return None, 0.0, False
    if not row:
        return None, 0.0, False
    try:
        return _parse_box_doc(lg, row["doc"]), row["fetched_at"], bool(row["final"])
    except Exception as e:  # noqa: BLE001 — an espn-api upgrade changed BoxScore: read ESPN again
        logging.getLogger(__name__).warning("stored box scores for week %s unparseable: %s", week, e)
        return None, 0.0, False


def _read_boxes(lg, week: int, finished: bool, epoch: int, ref: leagues.LeagueRef) -> tuple[list, bool, bool]:
    """(box scores, read from ESPN just now, final: a finished week's read made after it finished).
    A finished week is a hit only on a final row: the current week's last read (maybe mid-game) is not its
    result, so the first ask after ESPN rolls the week reads it again and stores that read final."""
    if not hasattr(lg, "espn_request"):  # tests stub League with box_scores alone: nothing to store or read back
        return lg.box_scores(week), True, finished
    stored, at, final = _stored_boxes(lg, week, ref)
    if stored is not None and ((finished and final) or (not finished and time.time() - at < BOXES_TTL_S)):
        return stored, False, finished and final
    try:
        doc = _fetch_box_doc(lg, week)
    except Exception as e:  # noqa: BLE001
        if stored is None:
            raise
        logging.getLogger(__name__).warning("week %s box scores from the stored read (ESPN failed: %s)", week, e)
        return stored, False, finished and final
    boxes = _parse_box_doc(lg, doc)  # before the store: a read that does not parse is never kept for the next one to trust
    if not finished and _BOXES_EPOCH[0] != epoch:  # a move landed during the fetch: a pre-move read must not be stored as fresh
        return boxes, False, False
    try:
        db.put_box_doc(week, doc, box_hash(doc), time.time(), final=finished, ref=ref)
    except db.DBError as e:
        logging.getLogger(__name__).warning("week %s box scores not stored: %s", week, e)
    return boxes, True, finished


def _first_week(r: leagues.LeagueRef) -> int:
    """The league's first scoring week (1 unless it was drafted mid-season). Settings that cannot be read
    count as 1, so a league without them reads as it did before the guard."""
    try:
        return load(ref=r).get("first_week") or 1
    except (Exception, SystemExit) as e:  # noqa: BLE001 — the guard must not add a failure path (load exits on missing settings)
        logging.getLogger(__name__).warning("first_week of %s not read, taken as 1: %s", r.key, e)
        return 1


def league_week(lg, *, ref: leagues.LeagueRef) -> int:
    """The week `ref`'s pages default to: ESPN's current week, or the league's first week while ESPN is still before
    it (a league drafted mid-season, between its draft and its first week). Home starts at 1: ESPN's week."""
    return max(lg.current_week, _first_week(ref))


def league_period(period: int, *, ref: leagues.LeagueRef) -> int:
    """ESPN's scoring period (a pending read's) as the league's week, to compare with a page's: league_week's rule."""
    return max(period, _first_week(ref))


def box_scores(lg, week: int, *, ref: leagues.LeagueRef) -> list:
    """lg.box_scores(week), shared by every builder for BOXES_TTL_S.

    The four page builders each need the week's box scores, and the API cache rebuilds them
    within the same minute or two; this is one 0.7 s ESPN call instead of four. Keyed on the
    League object itself, not its build time: tests stub a fresh League per case, and a rebuilt
    League (fresh_league after an hour, or POST /refresh) is a new source, fetched again.
    Weeks before the league's first_week (drafted mid-season) have no box scores: []. A finished week (before the League's current one: the form window's earlier weeks) never
    changes, so it is kept for the life of the process, whichever League asks (ffl-qf7), and is
    read from the box_scores table (ESPN only when it has no final row); finished weeks change only through
    the correction pass (ffl-7sq). Every cache is per league (`ref`): two leagues' week 5 never answer each other. The current week is a hit for BOXES_TTL_S off its row's fetched_at
    (a cold process right after a deploy), and when ESPN fails the stored row is served whatever its age
    (a finished week's non-final row so served is kept BOXES_TTL_S only, never for the process).
    """
    if week < _first_week(ref):  # the league had no matchups yet: nothing to read, store or cache
        return []
    hit = _BOXES.get((ref.key, week))
    if hit and (hit[0] == math.inf or (hit[0] > time.time() and hit[1] is lg)):
        return hit[2]
    # keyed on the League too: a caller holding another League fetches from its own, never waits for this one's
    return _BOXES_FLIGHT.do((ref.key, week, id(lg)), lambda: _fetch_boxes(lg, week, ref))


def _fetch_boxes(lg, week: int, ref: leagues.LeagueRef) -> list:
    """box_scores' fetch, once for every caller waiting on it: the read, the install and the log's reconcile."""
    key = ref.key
    hit = _BOXES.get((key, week))
    if hit and (hit[0] == math.inf or (hit[0] > time.time() and hit[1] is lg)):  # the flight before this one
        return hit[2]
    epoch = _BOXES_EPOCH[0]
    finished = week < getattr(lg, "current_week", week)
    boxes, fresh, final = _read_boxes(lg, week, finished, epoch, ref)
    if finished or _BOXES_EPOCH[0] == epoch:  # a move during the fetch: these may be from before it
        _BOXES[(key, week)] = (math.inf if final else time.time() + BOXES_TTL_S, lg, boxes)
    if fresh and week == getattr(lg, "current_week", None):
        # every page's rosters come through here: a rostered man the record never saw taken (or a
        # drop it missed), seen by any page, syncs the transaction log
        try:
            reconcile_log(lg, {pid: c["team_id"] for pid, c in owners_in(boxes).items()}, ref=ref)
        except Exception as e:  # noqa: BLE001 — the record catching up is a side effect; the page stands without it
            print(f"transaction reconcile failed: {e}")
    return boxes


CORRECTION_EVERY_S = 3600  # a finished, unsettled week is re-read at most this often


def _points_by_player(boxes) -> dict[int, dict]:
    """player id -> {team_id, points (2 dp), starter, name, pos, team} over every lineup of the boxes."""
    out = {}
    for b in boxes:
        for side, lineup in ((b.home_team, b.home_lineup), (b.away_team, b.away_lineup)):
            tid = getattr(side, "team_id", side)
            for p in lineup:
                out[p.playerId] = {"team_id": tid if isinstance(tid, int) else None, "points": round(p.points, 2),
                                   "starter": p.slot_position not in S.BENCH_SLOTS, "name": p.name,
                                   "pos": p.position, "team": p.proTeam}
    return out


def _correct_week(lg, w: int, now: datetime, row: dict | None, kicks: list, plays: list, *, ref: leagues.LeagueRef) -> dict:
    """One finished week's pass: raises on any ESPN or DB failure, leaving the stored row as it was.
    Only a final row (read after the week finished) is a baseline: a non-final one is the current week's last
    read, maybe mid-game, so the week is backfilled from this read instead. A short answer against a final row
    (fewer matchups, or a stored player missing) is no result: the stored doc stays, unsettled, nothing logged."""
    log = logging.getLogger(__name__)
    at = now.isoformat(timespec="seconds")
    doc = _fetch_box_doc(lg, w)
    corrections, week_plays = [], []
    baseline, old_boxes = None, []
    if row and row.get("final"):
        try:
            old_boxes = _parse_box_doc(lg, row["doc"])
            baseline = _points_by_player(old_boxes)
        except Exception as e:  # noqa: BLE001 — an unparseable stored doc is no baseline
            log.warning("week %s stored box scores unparseable, no corrections compared: %s", w, e)
    if baseline is not None:
        new_boxes = _parse_box_doc(lg, doc)
        points = _points_by_player(new_boxes)
        missing = set(baseline) - set(points)
        if len(new_boxes) < len(old_boxes) or missing:
            log.warning("week %s: ESPN answered short (%s of %s matchups, %s stored players missing); "
                        "stored read kept, left unsettled", w, len(new_boxes), len(old_boxes), len(missing))
            db.put_box_doc(w, row["doc"], row["hash"], now.timestamp(), settled=False, final=True, ref=ref)
            return {"changed": 0, "settled": False}
        for pid, new in points.items():
            old = baseline.get(pid)
            if old is None or old["points"] == new["points"]:
                continue
            log.warning("stat correction week %s: %s %s -> %s", w, new["name"], old["points"], new["points"])
            corrections.append({"week": w, "player_id": pid, "team_id": new["team_id"], "old": old["points"],
                                "new": new["points"], "at": at})
            if new["starter"]:
                week_plays.append({"at": at, "week": w, "player_id": pid, "team_id": new["team_id"],
                                   "name": new["name"], "pos": new["pos"], "team": new["team"],
                                   "delta": round(new["points"] - old["points"], 2), "total": new["points"],
                                   "text": f"Stat correction: {old['points']:g} → {new['points']:g}",
                                   "play_id": None, "quarter": None, "clock": None, "game_id": None,
                                   "match": "correction"})
    gap_new, gap_corr = {}, []
    ids = db.gap_ids(w, ref=ref)
    if ids:
        gap_new = _fetch_gap_rows(w, ids, lg.current_week, ref=ref)
        old_rows = db.gap_rows(w, ids, ref=ref)
        # a stored player answered with no rows is a short answer, not a correction: kept out (week unsettled)
        gap_new = {pid: rows for pid, rows in gap_new.items() if rows or not old_rows.get(pid)}
        for pid, rows in gap_new.items():
            old, new = round(sum(r["pts"] for r in old_rows.get(pid, [])), 2), round(sum(r["pts"] for r in rows), 2)
            if pid in old_rows and old != new:
                log.warning("stat correction week %s: gap player %s %s -> %s", w, pid, old, new)
                gap_corr.append({"week": w, "player_id": pid, "team_id": None, "old": old, "new": new, "at": at})
    settled = False
    if kicks:
        settled = now >= S.settle_at(max(kicks))
    else:
        log.warning("week %s: no kickoffs (schedule read failed), left unsettled", w)
    if ids and set(gap_new) != set(ids):  # _fetch_gap_rows swallows its errors: a missing id is a failed read
        log.warning("week %s: gap rows missing for %s, left unsettled", w, sorted(set(ids) - set(gap_new)))
        settled = False
    if gap_new:  # before the box row: a failure here leaves the week unsettled and retried
        db.add_box_corrections(gap_corr, ref=ref)
        db.put_gap_rows(w, gap_new, now.timestamp(), ref=ref)
        for pid, rows in gap_new.items():
            _GAP_GAMES[(ref.key, pid, w)] = rows
    db.put_box_doc(w, doc, box_hash(doc), now.timestamp(), settled=settled, corrections=corrections, final=True, ref=ref)
    plays.extend(week_plays)
    return {"changed": len(corrections) + len(gap_corr), "settled": settled}


def correct_boxes(lg, now: datetime | None = None, *, ref: leagues.LeagueRef) -> dict:
    """The stat-correction pass (ffl-7sq): every finished week (first_week..current-1) without a settled row is read from
    ESPN again (never through box_scores(), which answers a finished week from memory or the table), at most
    hourly, and each starter or bench total that moved is logged to box_score_corrections; a starter's also
    becomes a play row (match "correction") in the result for the caller to write, since the live runner owns the
    plays store. The week settles when this read is past season.settle_at of its last kickoff, and a week with no
    kickoffs (the schedule read failed) never settles. A week with no row yet, or only a non-final one (read
    while it was the current week), is backfilled with no baseline, so nothing is logged. A week that fails is {"error"} and its row is untouched (retried next tick).
    Returns {"weeks": {w: {changed, settled} | {error}}, "changed": [weeks with a change], "plays": [rows]}."""
    now = now or datetime.now(timezone.utc)
    log = logging.getLogger(__name__)
    out = {"weeks": {}, "changed": [], "plays": []}
    try:
        first = _first_week(ref)  # a league drafted mid-season had no matchups before it
        stored = db.box_weeks(ref=ref)
        kicks = kickoffs_by_week(lg)
    except Exception as e:  # noqa: BLE001
        log.warning("correction pass not run: %s", e)
        out["error"] = str(e)
        return out
    for w in range(first, lg.current_week):
        have = stored.get(w)
        # a non-final row (the current week's last read, maybe mid-game) is due now, unthrottled
        if have and have["final"] and (have["settled"] or now.timestamp() - have["fetched_at"] < CORRECTION_EVERY_S):
            continue
        try:
            row = db.box_doc(w, ref=ref) if have else None
            res = _correct_week(lg, w, now, row, kicks.get(w) or [], out["plays"], ref=ref)
        except Exception as e:  # noqa: BLE001 — one week's ESPN or DB failure must not stop the others
            log.warning("correction pass: week %s failed: %s", w, e)
            out["weeks"][w] = {"error": str(e)}
            continue
        out["weeks"][w] = res
        if res["changed"] or not (have and have["final"]):  # a backfill replaced a non-final read too
            _BOXES.pop((ref.key, w), None)
        if res["changed"]:
            out["changed"].append(w)
    return out


PRO_SCHEDULE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
_PAST_SCHED: dict[int, dict] = {}  # season -> its proTeamSchedules_wl document; a past season never changes


def past_schedule(season: int) -> dict:
    """An earlier season's pro schedule (opponents and byes for last season's game log)."""
    if season not in _PAST_SCHED:
        r = requests.get(PRO_SCHEDULE.format(season=season), params={"view": "proTeamSchedules_wl"},
                         timeout=20)  # public: no league's cookies
        r.raise_for_status()
        _PAST_SCHED[season] = r.json()
    return _PAST_SCHED[season]


# ESPN's per-season player reads 404 before 2018, and 2018's weekly rows carry no games played (stat 210),
# so the log reads none of them (checked 2026-10-22)
FIRST_SEASON = 2019
HISTORY = SEASON - FIRST_SEASON  # past seasons in the season modal: every one ESPN has
_RANKS: dict[str, tuple[float, dict]] = {}  # league key -> (expires_at, ranks); kona_player_info is league-scored


def opponent_ranks(*, ref: leagues.LeagueRef) -> dict[str, dict[str, dict]]:
    """ESPN's points-allowed rank of every defence against each position (player_card.parse_ranks),
    off the league's kona_player_info view, which carries it beside any player list (one player is
    asked for). Cached per league for CACHE_S; a failed read serves the last good ranks, or none."""
    key = ref.key
    hit = _RANKS.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    from . import player_card as card_log  # player_card imports this module

    filt = {"players": {"limit": 1, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    try:
        ranks = card_log.parse_ranks(raw_league_get("kona_player_info", headers={"x-fantasy-filter": json.dumps(filt)}, ref=ref))
    except Exception as e:  # noqa: BLE001 — the ranks are context; the log stands without them
        print(f"opponent ranks unavailable: {e}")
        return hit[1] if hit else {}
    _RANKS[key] = (time.time() + CACHE_S, ranks)
    return ranks


POS_SLOT = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "D/ST": 16, "K": 17}
_POS_RANKS: dict[tuple[str, str], tuple[float, dict]] = {}  # (league key, pos) -> (expires_at, player_card.parse_pos_ranks)


def position_ranks(pos: str, *, ref: leagues.LeagueRef) -> dict:
    """Every player's season and weekly rank at `pos` (player_card.parse_pos_ranks), off one
    kona_player_info list of the position's top 400 by season points with their weekly rows
    (about 1.5 s and 3 MB for WR). Cached per league for CACHE_S; a failed read serves the last good, or none."""
    hit = _POS_RANKS.get((ref.key, pos))
    if hit and hit[0] > time.time():
        return hit[1]
    from . import player_card as card_log  # player_card imports this module

    weeks = [f"01{ref.season}{w}" for w in range(1, S.NFL_WEEKS + 1)]
    filt = {"players": {"filterSlotIds": {"value": [POS_SLOT[pos]]}, "limit": 400,
                        "filterStatsForTopScoringPeriodIds": {"value": 25, "additionalValue": weeks},
                        "sortAppliedStatTotal": {"sortAsc": False, "sortPriority": 1, "value": f"00{ref.season}"}}}
    try:
        doc = raw_league_get("kona_player_info", headers={"x-fantasy-filter": json.dumps(filt)}, ref=ref)
        ranks = card_log.parse_pos_ranks(doc, pos, ref.season)
    except Exception as e:  # noqa: BLE001 — the ranks are context; the log stands without them
        print(f"{pos} position ranks unavailable: {e}")
        return hit[1] if hit else {}
    _POS_RANKS[(ref.key, pos)] = (time.time() + CACHE_S, ranks)
    return ranks
_PAST_STATS: dict[tuple[int, int], list[dict]] = {}  # (season, player) -> his stat rows; a past season never changes


def past_stats(season: int, player_id: int) -> list[dict]:
    """One player's stat rows for a season before last, off that season's default league (the
    league call carries only this season and last). Same row shape, same stat ids."""
    key = (season, player_id)
    if key not in _PAST_STATS:
        filt = {"players": {"filterIds": {"value": [player_id]},
                            "filterStatsForTopScoringPeriodIds": {"value": 25, "additionalValue": [f"00{season}"]}}}
        r = requests.get(f"{PRO_SCHEDULE.format(season=season)}/segments/0/leaguedefaults/3",
                         params={"view": "kona_playercard"}, headers={"x-fantasy-filter": json.dumps(filt)},
                         timeout=20)  # public: no league's cookies
        r.raise_for_status()
        players = r.json().get("players") or []
        _PAST_STATS[key] = players[0]["player"].get("stats") or [] if players else []
    return _PAST_STATS[key]


def player_card(player_id: int, *, ref: leagues.LeagueRef) -> dict:
    """One player's game log, this season and every one ESPN has before it (player_card.player_log): the
    league call returns every week of this season and last, each earlier season is one more call,
    made side by side, with the opponent ranks and Sleeper's projection for every week left. A
    failed read drops what it carried (that season, its opponents, the ranks, a week's Sleeper
    projection) rather than failing the log. The league call, its ranks and its settings are `ref`'s."""
    from . import player_card as card_log  # player_card imports this module

    season = ref.season
    lg = fresh_league(ref)
    filt = {"players": {"filterIds": {"value": [player_id]},
                        "filterStatsForTopScoringPeriodIds": {"value": 25, "additionalValue": [f"00{season}", f"10{season}", f"11{season}{lg.current_week}"]}}}
    card = raw_league_get("kona_playercard", headers={"x-fantasy-filter": json.dumps(filt)}, ref=ref)
    if not card.get("players"):
        raise LookupError(f"ESPN has no player {player_id}")
    earlier = range(season - 2, season - HISTORY - 1, -1)
    p = card["players"][0]["player"]
    pos = card_log.POS.get(p.get("defaultPositionId"))
    reads = {("sched", season): lambda: _schedule_doc(lg), ("ranks", season): lambda: opponent_ranks(ref=ref)}
    if pos in POS_SLOT:
        reads[("pos_ranks", season)] = lambda: position_ranks(pos, ref=ref)
    reads |= {("sched", yr): (lambda yr=yr: past_schedule(yr)) for yr in range(season - 1, season - HISTORY - 1, -1)}
    reads |= {("stats", yr): (lambda yr=yr: past_stats(yr, player_id)) for yr in earlier}
    settings = load(ref)
    reads[("sources", lg.current_week)] = lambda: weekly_sources(lg.current_week, settings)
    reads |= {("sleeper", wk): (lambda wk=wk: _sleeper_for(wk, settings)) for wk in range(lg.current_week, S.NFL_WEEKS + 1)}
    if pos in S.SNAP_POSITIONS:  # his snaps each week this season, off the cached Sleeper stat rows
        reads |= {("snaps", wk): (lambda wk=wk: parse_sleeper_snaps(_stats_rows(wk))) for wk in range(1, lg.current_week + 1)}
    with ThreadPoolExecutor(max_workers=len(reads)) as pool:
        futures = {k: pool.submit(fn) for k, fn in reads.items()}
    schedules, rows, ranks, sleeper, pos_ranks, snaps, current = {}, [], {}, {}, {}, {}, {}
    key = (norm_name(p.get("fullName") or ""), pos)
    for (kind, yr), f in futures.items():
        try:
            if kind == "sched":
                schedules[yr] = f.result()
            elif kind == "ranks":
                ranks = f.result()
            elif kind == "pos_ranks":
                pos_ranks = f.result()
            elif kind == "sources":
                current = f.result()
            elif kind == "sleeper":
                sleeper[yr] = f.result()  # yr is the week here
            elif kind == "snaps":
                if key in (week := f.result()):
                    snaps[yr] = week[key]
            else:
                rows += f.result()
        except Exception as e:  # noqa: BLE001 — an earlier season or the opponents are not worth the log
            print(f"{kind} {yr} unavailable for player {player_id}: {e}")
    card = {"players": [{"player": {**p, "stats": (p.get("stats") or []) + rows}}]}
    from .live import box_items
    log = card_log.player_log(card, box_items(settings), schedules, lg.current_week, season, HISTORY, ranks,
                              sleeper=sleeper, pos_ranks=pos_ranks, snaps=snaps, current=current)
    return {**log, "playoff_weeks": settings.get("playoff_weeks", [])}  # this season's: the log flags its weeks


def _boxes_by_team(lg, week: int, *, ref: leagues.LeagueRef) -> dict[int, list]:
    """Every team's roster for the week, from a single box_scores call."""
    out: dict[int, list] = {}
    for m in box_scores(lg, week, ref=ref):
        for t, lineup in ((m.home_team, m.home_lineup), (m.away_team, m.away_lineup)):
            if hasattr(t, "team_id"):
                out[t.team_id] = lineup
    return out


def _owners(lg, week: int, *, ref: leagues.LeagueRef) -> dict[int, dict]:
    """player_id -> the fantasy team rostering him this week, off the same box_scores fetch:
    the Players page's owner chip."""
    return owners_in(box_scores(lg, week, ref=ref))


def owners_in(boxes: list) -> dict[int, dict]:
    """player_id -> the fantasy team holding him in these box scores. A team without a name or
    abbreviation leaves it None."""
    out: dict[int, dict] = {}
    for m in boxes:
        for t, lineup in ((m.home_team, m.home_lineup), (m.away_team, m.away_lineup)):
            if hasattr(t, "team_id"):
                club = {"team_id": t.team_id, "name": getattr(t, "team_name", None) or None,
                        "abbrev": getattr(t, "team_abbrev", None) or None}
                for bp in lineup:
                    out[bp.playerId] = club
    return out


# projection_rows kinds for a finished week's board: KICKOFF is the notify tick's record, each source's number
# while the game was ahead (calibration fits on it); LATE is what the sources answered when a week with no record
# (before 2026's week 5) was first opened after its games, kept so it is asked once, apart from calibration's rows.
KICKOFF, LATE = "week", "week_late"


def _box_players(boxes: list) -> list[dict]:
    return [flatten_box_player(bp) for m in boxes for lineup in (m.away_lineup, m.home_lineup) for bp in lineup or []]


def recorded_sources(week: int, boxes: list, *, ref: leagues.LeagueRef) -> dict[str, dict[tuple[str, str], float]] | None:
    """A finished week's projections from projection_rows (the kickoff record, else the saved late fetch), keyed
    like weekly_sources for the week's box players. None when neither is there or the DB cannot answer."""
    try:
        rows = db.projection_rows(KICKOFF, week=week, ref=ref) or db.projection_rows(LATE, week=week, ref=ref)
    except db.DBError as e:
        print(f"recorded projections for week {week} unreadable, fetching the sources: {e}")
        return None
    if not rows:
        return None
    keys = {p["player_id"]: source_key(p) for p in _box_players(boxes)}
    out: dict[str, dict[tuple[str, str], float]] = {}
    for r in rows:
        if (key := keys.get(r["player_id"])) is not None:
            out.setdefault(r["source"], {})[key] = r["points"]
    return out


# The weekly sources that answer for a week already played (checked 2026-10-13): CBS's week page serves the
# current week whatever week is asked, and FleaFlicker has no rows for a past week.
PLAYED_WEEK_SOURCES = ("sleeper", "sharks")


def _fetch_and_keep(week: int, settings: dict, boxes: list, *, ref: leagues.LeagueRef) -> dict[str, dict[tuple[str, str], float]]:
    """A finished week with nothing recorded: ask the sources that serve a played week, and keep what they said
    (kind LATE) for every box player of the week, so no later build asks again. Kept only when each answered: a
    source down today is asked again next time rather than missing from the week for good."""
    played = [name for name in PLAYED_WEEK_SOURCES if not config.source_off(name)]  # one switched off is not waited for
    src = {name: d for name, d in weekly_sources(week, settings).items() if name in played}
    if all(src.get(name) for name in played):
        rows = [{"player_id": p["player_id"], "source": s, "points": v}
                for p in _box_players(boxes) for s, v in source_values(p, src).items()]
        try:
            db.put_projection_rows(week, LATE, rows, ref=ref)
        except db.DBError as e:
            print(f"week {week}'s fetched projections not saved: {e}")
    return src


def week_sources(lg, week: int, settings: dict, boxes: list, *, ref: leagues.LeagueRef) -> dict[str, dict[tuple[str, str], float]]:
    """The week's source projections as the Matchup board and the League scoreboard both price it. A finished
    week is priced as it stood at kickoff where the notify tick recorded it (else the late fetch, made once):
    refetching every source made a past board 9-15 s cold and a past scoreboard 7-9 s, and a source may
    rewrite a week once it is played."""
    if week < lg.current_week:
        return recorded_sources(week, boxes, ref=ref) or _fetch_and_keep(week, settings, boxes, ref=ref)
    return weekly_sources(week, settings)


def week_state(week: int | None = None, sources: dict | None = None, season: dict | None = None,
               injuries: dict | None = None, team: int | None = None, *, ref: leagues.LeagueRef) -> dict:
    """The matchup board for `team`'s game (mine by default). `me` is the left side and `opp`
    the right: I stand on the left of my own game; any other game reads away then home, as the
    League scoreboard stacks it. `mine` says which kind of board it is. `ref` is the league."""
    lg = fresh_league(ref)
    settings = load(ref)
    week = week or league_week(lg, ref=ref)
    me = my_team_id(lg, ref)
    team = team or me
    boxes = box_scores(lg, week, ref=ref)
    # A finished week's ROS is today's (the lookup the current board already holds): building that week's ROS
    # was most of a cold past board
    finished = week < lg.current_week
    src = sources if sources is not None else week_sources(lg, week, settings, boxes, ref=ref)
    seas = _season_for(settings, season, lg.current_week if finished else week, ref=ref)
    inj, w1 = _injuries_for(injuries), _week1_start(settings)
    home = home_by_team(lg, week)
    for m in boxes:
        sides = [(m.away_team, m.away_lineup), (m.home_team, m.home_lineup)]
        ids = [getattr(t, "team_id", None) for t, _ in sides]
        if team not in ids:
            continue
        mine = me in ids
        if mine and ids[0] != me:
            sides.reverse()
        # a bye or placeholder side has no team to build; the real side takes the left
        sides = [s for s in sides if hasattr(s[0], "team_id")]
        # my players sat by hand this week stay out of my side's by-projection lineup, as on the Roster page
        sat = db.sat_ids(week, ref=ref) if mine else set()
        built = [build_side(t, with_sits(_side_players(lineup, settings, src, seas, inj, w1, home),
                                         sat if t.team_id == me else set()), settings)
                 for t, lineup in sides]
        # the left side's odds of winning (season.win_prob); the live overlay recomputes them
        odds = None
        if len(built) > 1:
            sigma, source = calibrate.load_sigma()
            p = S.win_prob(built[0]["lineup"], built[1]["lineup"], sigma)
            odds = {"me": round(p, 3), "opp": round(1 - p, 3), "sigma_source": source}
        return {"week": week, "live": S.is_live(), "mine": mine, "slots": S.starter_slots(settings), "me": built[0],
                "opp": built[1] if len(built) > 1 else None, "win_prob": odds}
    if team != me:
        raise LookupError(f"no matchup for team {team} in week {week}")
    raise RuntimeError(f"no matchup for team {me} in week {week}")


def league_starters(week: int | None = None, *, ref: leagues.LeagueRef) -> dict:
    """Every game's starters, the play feed's roster: {week, games: [[side, side], ...]}, each side
    {team_id, lineup: [{player_id, name, pos, team}]}. Bench and IR are left out; a bye slot too."""
    lg = fresh_league(ref)
    week = week or lg.current_week
    games = []
    for m in box_scores(lg, week, ref=ref):
        games.append([{"team_id": t.team_id,
                       "lineup": [{"player_id": bp.playerId, "name": bp.name, "pos": bp.position, "team": bp.proTeam}
                                  for bp in lineup or [] if getattr(bp, "slot_position", "BE") not in S.BENCH_SLOTS]}
                      for t, lineup in ((m.away_team, m.away_lineup), (m.home_team, m.home_lineup))
                      if hasattr(t, "team_id")])
    return {"week": week, "games": games}


def _side_players(lineup: list, settings: dict, src: dict, seas: dict, inj: dict, w1, home: dict) -> list[dict]:
    """One side's box players as the board shows them: both sources' projections, the season
    lookup, injury return weeks and the venue."""
    return apply_home(apply_injuries(
        apply_season(merge_weekly([{**flatten_box_player(bp), "stat_row": stat_row(bp, settings)}
                                   for bp in lineup], src), seas), inj, w1), home)


def team_state(team_id: int, week: int | None = None, sources: dict | None = None, season: dict | None = None,
               injuries: dict | None = None, *, ref: leagues.LeagueRef) -> dict:
    """Any team's roster for the week (the team page): its side of the box scores, grouped
    starters / bench / IR as the board groups them, under the club as the League page shows it
    plus its standings rank. A team with no game this week (a playoff bye) is listed from its
    roster, without points. Raises LookupError on an unknown team id. `ref` is the league."""
    lg = fresh_league(ref)
    t = next((t for t in lg.teams if t.team_id == team_id), None)
    if t is None:
        raise LookupError(f"no team {team_id}")
    settings = load(ref)
    week = week or league_week(lg, ref=ref)  # a league before its first week pages that week, as the board does
    src = sources if sources is not None else weekly_sources(week, settings)
    seas = _season_for(settings, season, week, ref=ref)
    players = _boxes_by_team(lg, week, ref=ref).get(team_id)
    built = _side_players(players or getattr(t, "roster", []), settings, src, seas,
                          _injuries_for(injuries), _week1_start(settings), home_by_team(lg, week))
    if players is None:  # roster Players carry lineupSlot, not the box score's slot_position
        slots = {rp.playerId: getattr(rp, "lineupSlot", "BE") for rp in t.roster}
        built = [{**p, "slot": slots.get(p["player_id"], "BE")} for p in built]
    rank = next((i for i, s in enumerate(lg.standings(), 1) if s.team_id == team_id), None)
    return {"week": week, "live": S.is_live(), "mine": team_id == my_team_id(lg, ref),
            "club": {**_club(t), "rank": rank}, "side": build_side(t, built, settings)}


def parse_pool_status(doc: dict) -> dict[int, dict]:
    """kona_player_info players -> {id: {status, waiver_date}}. status is FREEAGENT (added instantly),
    WAIVERS (a claim) or ONTEAM; waiver_date is the ms epoch his waiver period ends, when ESPN says."""
    return {int(p["id"]): {"status": p.get("status"), "waiver_date": p.get("waiverProcessDate")}
            for p in doc.get("players", [])}


_SLOT_NAME = {i: n for n, i in espn_write.SLOT_ID.items()}


def parse_roster_flags(doc: dict, team_id: int) -> dict[int, dict]:
    """mRoster -> {id: {droppable, locked, slot, order}} for one team (slot = ESPN's lineup slot name, left out when unknown;
    order = his place in ESPN's entries, the order its Matchup tab lists a slot's players in). ESPN marks an undroppable
    player (droppable false) and one whose game has started (lineupLocked); anything unsaid is droppable/unlocked.
    injury (ESPN's injuryStatus) and name (fullName) ride along when the document carries them."""
    team = next((t for t in doc.get("teams", []) if t["id"] == team_id), None)
    out = {}
    for i, e in enumerate((team or {}).get("roster", {}).get("entries", [])):
        pe = e.get("playerPoolEntry") or {}
        player = pe.get("player") or {}
        out[int(e["playerId"])] = {"droppable": player.get("droppable") is not False,
                                   "locked": bool(pe.get("lineupLocked")), "order": i}
        slot = _SLOT_NAME.get(e.get("lineupSlotId"))
        if slot:
            out[int(e["playerId"])]["slot"] = slot
        for key, src in (("injury", "injuryStatus"), ("name", "fullName")):
            if player.get(src):
                out[int(e["playerId"])][key] = player[src]
    return out


STATUS_LIMIT = 1000


def pool_status(*, ref: leagues.LeagueRef) -> dict[int, dict]:
    # filterStatus, not filterIds: ESPN 400s on "limit" beside filterIds (tx_player_info), and D/ST ids are negative.
    # This is the query the 2026-10-06 probe ran (limit 400 answered); it covers every pool row (60 per position).
    flt = {"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS"]}, "limit": STATUS_LIMIT,
                       "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    return parse_pool_status(raw_league_get("kona_player_info", headers={"x-fantasy-filter": json.dumps(flt)}, ref=ref))


def roster_flags(lg, *, ref: leagues.LeagueRef) -> dict[int, dict]:
    return parse_roster_flags(raw_league_get("mRoster", ref=ref), my_team_id(lg, ref))


# How deep the Players page's free-agent half goes at each position, by ownership. Measured
# 2026-10-06: the build took 3.1 s at 60, 2.9 s at 100, 2.7 s at 150 (the calls run in parallel and
# the payload is not the cost). ESPN runs out first at QB (100), D/ST (19) and K (48).
POOL_SIZE = 150


def free_agent_pool(week: int | None = None, size: int = POOL_SIZE, sources: dict | None = None,
                    season: dict | None = None, injuries: dict | None = None,
                    snaps: dict | None = None, *, ref: leagues.LeagueRef) -> dict:
    """The Players page's pool: `positions` holds the free agents ranked for a claim (claims, the
    waiver outlook and the calibration snapshot read it, so it never carries a rostered man);
    `rostered` holds every rostered player priced the same way, tagged with his owner. `ref` is the league."""
    lg = fresh_league(ref)
    settings = load(ref)
    week = week or league_week(lg, ref=ref)
    # the league's strategy, "Waivers" (`uv run python -m ffl.strategy show`): ROS starts from the first week whose games are not all played
    rw = ros_week_for(lg, week)
    week_over = rw > week
    me = my_team_id(lg, ref)
    src = sources if sources is not None else weekly_sources(week, settings)
    seas = _season_for(settings, season, rw, ref=ref)
    inj, w1 = _injuries_for(injuries), _week1_start(settings)
    boxes = _boxes_by_team(lg, week, ref=ref)
    if me not in boxes:
        raise RuntimeError(f"no box score for team {me} in week {week}")
    home = home_by_team(lg, week)  # so the Waivers page can say "at" when a free agent travels
    byes, weeks = bye_by_team(lg), form_weeks(week)
    snap_weeks = _snaps_for(weeks, snaps)
    refs, exp, usage = snap_refs(snap_weeks), _exp_for(weeks, snaps), _usage_for(weeks, snaps)
    roster_games = _roster_games(lg, boxes, week, ref=ref)

    def enrich(players: list[dict], games: dict[int, list[dict]]) -> list[dict]:
        return apply_form(apply_exp(apply_home(apply_injuries(apply_season(merge_weekly(players, src), seas), inj, w1), home), exp),
                          games, snap_weeks, rw, byes, refs, usage)
    # my players carry the same enrichment as a free agent: the page compares him against them
    my_players = enrich([flatten_box_player(bp) for bp in boxes[me]], roster_games)
    # every rostered player carries his form too: the replacement line runs on the adjusted ROS
    rostered = enrich([flatten_box_player(bp) for lineup in boxes.values() for bp in lineup], roster_games)
    out, frees_by_pos = {}, {}
    positions = ["QB", "RB", "WR", "TE", "D/ST", "K"]
    # one ESPN call of ~0.6 s per position and form week (the pool is the same set each week,
    # sorted by ownership; only the stats change): all in flight together, not one after another
    asks = [(pos, w) for pos in positions for w in weeks]
    with ThreadPoolExecutor(len(asks)) as pool:
        fetched = dict(zip(asks, pool.map(lambda a: lg.free_agents(week=a[1], size=size, position=a[0]), asks)))
    for pos in positions:
        games: dict[int, list[dict]] = {}
        for w in weeks:
            for obj in fetched[(pos, w)]:
                games.setdefault(obj.playerId, []).extend(player_games(obj, [w], week))
        frees = enrich([flatten_box_player(bp) for bp in fetched[(pos, week)]], games)
        for f in frees:
            f["slot"] = "FA"
        out[pos] = S.waiver_deltas(frees, my_players, rostered + frees, settings, rw, week_over=week_over)
        for r in out[pos]:
            r["owner"], r["mine"] = None, False
        frees_by_pos[pos] = frees
    # what the blend read this ROS week, kept for ffl.calibrate; never a reason for the page to fail
    try:
        calibrate.write_snapshot(rw, calibrate.snapshot_rows(out, rw), ref=ref)
    except db.DBError as e:
        print(f"calibration snapshot not written: {e}")
    roster = S.roster_rows(my_players, rw)
    # claim facts (ffl/claims.py): a failed read leaves the defaults, so nothing is ever added instantly
    # by mistake (unknown status counts as WAIVERS) and ESPN is left to refuse an undroppable cut
    try:
        status = pool_status(ref=ref)
    except Exception as e:
        print(f"waiver status not read: {e}")
        status = {}
    try:
        flags = roster_flags(lg, ref=ref)
    except Exception as e:
        print(f"roster flags not read: {e}")
        flags = {}
    for rows in out.values():
        for r in rows:
            s = status.get(r["player_id"]) or {}
            r["status"], r["waiver_date"] = s.get("status"), s.get("waiver_date")
    for r in roster:
        f = flags.get(r["player_id"]) or {}
        r["droppable"], r["locked"] = f.get("droppable", True), f.get("locked", False)
    # the Players page: every rostered player, priced as a free agent would be (worth to me);
    # my own carry no worth, measuring a man against my weakest being circular
    owners = _owners(lg, week, ref=ref)
    rostered_out: dict[str, list[dict]] = {}
    for pos in positions:
        rows = S.waiver_deltas([p for p in rostered if p["pos"] == pos], my_players,
                               rostered + frees_by_pos[pos], settings, rw, week_over=week_over)
        for r in rows:
            r["owner"] = owners.get(r["player_id"])
            r["mine"] = bool(r["owner"]) and r["owner"]["team_id"] == me
            if r["mine"]:
                r.update(add=None, delta_roster=None, delta_pool=None, week_delta=None, would_start=None)
        rostered_out[pos] = rows
    # the Watch page: every watched man priced as a Players row, a free agent deeper than the lists by id;
    # never added to `positions`, and a failed read costs the watch, not the page
    try:
        watching = db.watched_ids(ref=ref)
    except db.DBError as e:
        print(f"watchlist not read: {e}")
        watching = []
    found = {r["player_id"]: r for rows in (*out.values(), *rostered_out.values()) for r in rows}
    deep, games = _watched_rows(lg, [pid for pid in watching if pid not in found and pid > 0], week, ref=ref)
    deep = enrich(deep, games)
    for pos in positions:
        at = [{**p, "slot": "FA"} for p in deep if p["pos"] == pos]
        # replacement level off the same pool a free agent's row reads: the deep man does not move it
        for r in S.waiver_deltas(at, my_players, rostered + frees_by_pos[pos], settings, rw, week_over=week_over):
            s = status.get(r["player_id"]) or {}
            found[r["player_id"]] = {**r, "status": s.get("status"), "waiver_date": s.get("waiver_date"),
                                     "owner": None, "mine": False}
    watched = [dict(found[pid]) for pid in watching if pid in found]
    return {"week": week, "ros_week": rw, "week_over": week_over, "positions": out, "rostered": rostered_out,
            "roster": roster, "watched": watched, "ros_source": ros_source(seas)}


def _roster_games(lg, boxes: dict[int, list], week: int, *, ref: leagues.LeagueRef) -> dict[int, list[dict]]:
    """Game rows for every rostered player: this week's off the box score (fresh every minute), the
    earlier form weeks off those weeks' box scores (one cached call a week). A Player on lg.teams
    rosters carries only the current scoring period's stats, so it cannot supply them (ffl-rns). A
    week he sat on no fantasy roster (a pickup's free-agent weeks, a drop he was re-added after) comes
    off that week's player info (_gap_games, ffl-iig), so a recent pickup's window is as full as a free
    agent's; a week whose box scores failed stays empty."""
    games = {bp.playerId: player_games(bp, [week], week) for lineup in boxes.values() for bp in lineup}
    earlier: dict[int, list[dict]] = {pid: [] for pid in games}
    gaps: dict[int, list[int]] = {}
    for w in form_weeks(week)[:-1]:
        try:
            by_team = _boxes_by_team(lg, w, ref=ref)
        except Exception as e:  # noqa: BLE001 — one week's history is not worth the page (ffl-qf7)
            logging.getLogger(__name__).warning("week %s box scores not read; no form rows for it: %s", w, e)
            continue
        seen = set()
        for lineup in by_team.values():
            for bp in lineup:
                if bp.playerId in earlier and bp.playerId not in seen:
                    seen.add(bp.playerId)
                    earlier[bp.playerId] += player_games(bp, [w], week)
        for pid in earlier.keys() - seen:  # in any box score that week he was rostered (a bye there is no gap)
            gaps.setdefault(pid, []).append(w)
    for pid, rows in _gap_games(gaps, week, ref=ref).items():
        earlier[pid] = sorted(earlier[pid] + rows, key=lambda g: g["week"])
    return {pid: earlier[pid] + g for pid, g in games.items()}


GAP_BATCH = 40  # ids per kona_player_info call, as tx_player_info asks (no `limit`: ESPN 400s on it)
# (player, finished week) -> his rows. Kept in memory and in the gap_games table; the correction pass
# re-reads an unsettled week's rows (ffl-7sq).
_GAP_GAMES: dict[tuple[str, int, int], list[dict]] = {}  # (league key, player, week): ESPN's player info is the league's own


def _fetch_gap_rows(w: int, ids: list[int], week: int, *, ref: leagues.LeagueRef) -> dict[int, list[dict]]:
    """One kona_player_info read of `ids` for week w, with that week's scoringPeriodId, the same call
    free_agents(week=w) makes: player -> player_games(pl, [w], week). A failed call is logged and leaves
    its players out of the answer, as does a player ESPN answers without or oddly. Pure ESPN: neither the
    process memory nor the table is consulted."""
    from espn_api.football.player import Player

    log = logging.getLogger(__name__)
    filt = {"players": {"filterIds": {"value": ids}}}
    try:
        doc = raw_league_get("kona_player_info", headers={"x-fantasy-filter": json.dumps(filt)}, scoringPeriodId=w, ref=ref)
    except Exception as e:  # noqa: BLE001 — a pickup's free-agent weeks are not worth the page
        log.warning("week %s player info not read; no free-agent rows for %s: %s", w, ids, e)
        return {}
    out: dict[int, list[dict]] = {}
    for p in doc.get("players") or []:
        try:  # one player ESPN answers oddly costs his own rows, not the batch's
            pl = Player(p, ref.season)
        except Exception as e:  # noqa: BLE001
            log.warning("week %s player info unreadable for %s: %s", w, p.get("id"), e)
            continue
        if pl.playerId in ids:  # ESPN answering without one leaves him to be asked again next build
            out[pl.playerId] = player_games(pl, [w], week)
    return out


def _gap_games(gaps: dict[int, list[int]], week: int, *, ref: leagues.LeagueRef) -> dict[int, list[dict]]:
    """Game rows for the earlier weeks a rostered player sat on no fantasy roster (player -> weeks): one
    kona_player_info call per such week (GAP_BATCH ids each) for the players missing from the process and
    the gap_games table, so the row (points and that week's projection) is the one a free agent's window
    reads. The free-agent lists themselves cannot supply him: ESPN filters them by today's status, so a man
    rostered now is in none of them; and his player card has no past week's projection. Each (player, week)
    is kept in memory and in the table once read (a finished week), so a build after the first asks for
    nothing, even after a restart; the correction pass re-reads an unsettled week's rows (ffl-7sq). A failed
    call leaves that week without rows, as a failed week's box scores does; a table error is logged and
    ESPN is asked instead. Memory, table and ESPN are all `ref`'s."""
    log = logging.getLogger(__name__)
    lk = ref.key
    asks: list[tuple[int, list[int]]] = []
    for w in sorted({w for ws in gaps.values() for w in ws}):
        ids = sorted(pid for pid, ws in gaps.items() if w in ws and (lk, pid, w) not in _GAP_GAMES)
        if not ids:
            continue
        try:
            stored = db.gap_rows(w, ids, ref=ref)
        except db.DBError as e:
            log.warning("gap_games not read for week %s: %s", w, e)
            stored = {}
        for pid, rows in stored.items():
            _GAP_GAMES[(lk, pid, w)] = rows
        ids = [pid for pid in ids if pid not in stored]
        asks += [(w, ids[i:i + GAP_BATCH]) for i in range(0, len(ids), GAP_BATCH)]

    def read(ask: tuple[int, list[int]]) -> tuple[int, dict[int, list[dict]]]:
        return ask[0], _fetch_gap_rows(ask[0], ask[1], week, ref=ref)
    if asks:
        with ThreadPoolExecutor(len(asks)) as pool:  # all in flight together, like the free-agent asks
            answers = list(pool.map(read, asks))
        by_week: dict[int, dict[int, list[dict]]] = {}
        for w, got in answers:
            by_week.setdefault(w, {}).update(got)
        for w, got in by_week.items():
            for pid, rows in got.items():
                _GAP_GAMES[(lk, pid, w)] = rows
            if got:
                try:
                    db.put_gap_rows(w, got, time.time(), ref=ref)
                except db.DBError as e:
                    log.warning("gap_games not saved for week %s: %s", w, e)
    out: dict[int, list[dict]] = {}
    for pid, ws in gaps.items():
        if rows := [g for w in ws for g in _GAP_GAMES.get((lk, pid, w), [])]:
            out[pid] = rows
    return out


def _watched_rows(lg, ids: list[int], week: int, *, ref: leagues.LeagueRef) -> tuple[list[dict], dict[int, list[dict]]]:
    """Watched free agents deeper than the pool's lists, flattened as a free agent is, with their game rows:
    one kona_player_info read by ids for this week (what free_agents(week) asks, filterIds in place of the
    ownership sort; no `limit`, ESPN 400s on it beside filterIds) wrapped in BoxPlayers the same way, and the
    earlier form weeks through _gap_games. A failed read is logged and leaves them out; so is a man ESPN
    answers without or oddly."""
    from espn_api.football.box_player import BoxPlayer

    log = logging.getLogger(__name__)
    if not ids:
        return [], {}
    filt = {"players": {"filterIds": {"value": ids}}}
    try:
        doc = raw_league_get("kona_player_info", headers={"x-fantasy-filter": json.dumps(filt)}, scoringPeriodId=week, ref=ref)
        sched, ranks = lg._get_pro_schedule(week), lg._get_positional_ratings(week)
    except Exception as e:  # noqa: BLE001 — a watched man is not worth the Players page
        log.warning("watched players %s not read: %s", ids, e)
        return [], {}
    boxes = []
    for p in doc.get("players") or []:
        try:
            b = BoxPlayer(p, sched, ranks, week, ref.season)
        except Exception as e:  # noqa: BLE001
            log.warning("watched player info unreadable for %s: %s", p.get("id"), e)
            continue
        if b.playerId in ids:
            boxes.append(b)
    if missing := sorted(set(ids) - {b.playerId for b in boxes}):
        log.warning("watched players %s not in ESPN's answer", missing)
    earlier = _gap_games({b.playerId: form_weeks(week)[:-1] for b in boxes}, week, ref=ref)
    games = {b.playerId: earlier.get(b.playerId, []) + player_games(b, [week], week) for b in boxes}
    return [flatten_box_player(b) for b in boxes], games


def with_espn_slots(rows: list[dict], flags: dict[int, dict], fake: dict[int, str],
                    seats: dict[int, int] | None = None) -> list[dict]:
    """Each row's slot and lock from the fresh mRoster read (`flags`), never from the cached box score; the
    fake store's slot (FFL_WRITES=0) wins over both, and a row ESPN's read lacks keeps the slot it has, unlocked.
    `espn_order` is his seat's place in that read (season.reseat: the player whose place he took, else his own;
    after every player it has, in box order, when it lacks him)."""
    seats = seats or {}

    def order(pid: int, i: int) -> int:
        seat = seats.get(pid, pid)
        return flags.get(seat if seat in flags else pid, {}).get("order", 1000 + i)
    return [{**r, "slot": fake.get(r["player_id"], flags.get(r["player_id"], {}).get("slot", r["slot"])),
             "locked": flags.get(r["player_id"], {}).get("locked", False),
             "espn_order": order(r["player_id"], i)} for i, r in enumerate(rows)]


def with_sits(rows: list[dict], sat: set[int]) -> list[dict]:
    """Each row marked `sat` when he is sat by hand this week (the `sits` table; season.roster_cards)."""
    return [{**r, "sat": r["player_id"] in sat} for r in rows]


def roster_state(week: int | None = None, sources: dict | None = None, season: dict | None = None,
                 injuries: dict | None = None, snaps: dict | None = None, flags: dict | None = None,
                 *, ref: leagues.LeagueRef) -> dict:
    """My roster as cards (season.roster_cards) for the Roster page. Same enrichment as week_state,
    plus `form` (the league's strategy, "Recent form" (`uv run python -m ffl.strategy show`)) on every card. `flags` is an mRoster read the caller
    already has (POST /lineup's, with the moves applied); without it the read is made here. `ref` is the league."""
    lg = fresh_league(ref)
    settings = load(ref)
    week = week or league_week(lg, ref=ref)
    rw = ros_week_for(lg, week)
    me = my_team_id(lg, ref)
    src = sources if sources is not None else weekly_sources(week, settings)
    seas = _season_for(settings, season, rw, ref=ref)
    inj, w1 = _injuries_for(injuries), _week1_start(settings)
    boxes = _boxes_by_team(lg, week, ref=ref)
    if me not in boxes:
        raise RuntimeError(f"no box score for team {me} in week {week}")
    weeks = form_weeks(week)
    snap_weeks = _snaps_for(weeks, snaps)
    players = apply_form(apply_exp(apply_home(apply_injuries(
        apply_season(merge_weekly([flatten_box_player(bp) for bp in boxes[me]], src), seas), inj, w1),
        home_by_team(lg, week)), _exp_for(weeks, snaps)),
        _roster_games(lg, {me: boxes[me]}, week, ref=ref), snap_weeks, rw, bye_by_team(lg), snap_refs(snap_weeks),
        _usage_for(weeks, snaps))
    fake = espn_write.fake_slots(ref=ref)
    if flags is None:
        try:
            flags = roster_flags(lg, ref=ref)
        except Exception as e:  # noqa: BLE001 — a failed read shows every row unlocked; POST /lineup re-reads before writing
            logging.getLogger(__name__).warning("roster_flags read failed; every row shows unlocked: %s", e)
            flags = {}
    players = with_sits(with_espn_slots(players, flags, fake, db.seat_map(ref=ref)), db.sat_ids(week, ref=ref))
    return {"week": week, "live": S.is_live(), "cards": S.roster_cards(players, settings, week),
            "empty_slots": S.empty_slots(players, settings), "position_caps": S.position_caps(players, settings.get("position_limits", {})),
            "writes": espn_write.writes()}


def _club(t) -> dict:
    """A fantasy team as the League page shows it: name, its manager's name, ESPN abbreviation, the
    owner's logo (empty when ESPN has none; the UI falls back to the abbreviation) and the record."""
    return {"team_id": t.team_id, "team": t.team_name, "owner": owner_name(t), "abbrev": getattr(t, "team_abbrev", "") or "",
            "logo": getattr(t, "logo_url", "") or "", "wins": t.wins, "losses": t.losses,
            "ties": getattr(t, "ties", 0)}


def team_logo(team_id: int, *, ref: leagues.LeagueRef) -> tuple[bytes, str]:
    """The bytes and content type of a fantasy team's logo. ESPN serves an owner's own upload
    (mystique-api.fantasy.espn.com) only with the league cookies, so the browser cannot load it
    directly; the API fetches it on the UI's behalf. Raises on an unknown team or a bad fetch. `ref` is the league."""
    lg = fresh_league(ref)
    t = next((t for t in lg.teams if t.team_id == team_id), None)
    url = getattr(t, "logo_url", "") if t else ""
    if not url:
        raise LookupError(f"no logo for team {team_id}")
    from . import espn_auth
    c = espn_auth.for_read(ref)
    r = requests.get(url, cookies=c.jar() if c else None, timeout=20)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "image/png").split(";")[0]


def _streak(t) -> str:
    n = getattr(t, "streak_length", 0) or 0
    return f"{(getattr(t, 'streak_type', '') or '')[:1]}{n}" if n else ""


# What a League tile keeps of each starter: enough for live.overlay_league to re-price the odds and
# for the tile to sum its estimated final (web/lib/game.ts sideTotals, which reads the kickoff too).
SCORE_STARTER = ("player_id", "pos", "team", "proj", "actual", "game_played", "bye", "injury", "kickoff")


def league_state(week: int | None = None, *, ref: leagues.LeagueRef) -> dict:
    """The League page: standings and the week's scoreboard of `ref`'s league; `first_week` is the
    week its fantasy season starts (a mid-season league: later than 1), for the week picker."""
    lg = fresh_league(ref)
    settings = load(ref)
    me = my_team_id(lg, ref)
    current = lg.current_week
    week = week or current
    standings = [{"rank": i, **_club(t), "points_for": round(t.points_for, 2),
                  "points_against": round(t.points_against, 2), "streak": _streak(t), "me": t.team_id == me}
                 for i, t in enumerate(lg.standings(), 1)]
    # Box scores rather than the lighter mMatchupScore view: they carry each side's lineup, whose
    # starters the tile's projection and odds are built from. espn_api clamps box_scores to the
    # current week, so a future week takes its pairings from the schedule view, projection 0.
    board = []
    src: dict | None = None  # the week's source projections, read once the first lineup needs them
    sigma, _ = calibrate.load_sigma()
    boxes = box_scores(lg, week, ref=ref) if week <= current else []
    for m in (boxes if week <= current else lg.scoreboard(week)):
        if not (hasattr(m.home_team, "team_id") and hasattr(m.away_team, "team_id")):
            continue  # a bye slot in a playoff round
        # starter_ids lets the live overlay re-sum a side from the feed's per-player points,
        # starter_teams mark the tile live only while one of those starters' games is on, and
        # pending_teams are the clubs whose game ESPN does not yet call played (game_played, three
        # hours after kickoff; a bye counts as played), which the overlay clears at the feed's
        # whistle. `starters` are the same players as the board merges them (ESPN+Sleeper), so the
        # side's projection is the board's and the overlay can re-price the odds as games run; a
        # box score without lineups keeps ESPN's side projection. A schedule-view pairing (future
        # week) carries no lineup, so all are empty there.
        row = {}
        for side, t, score in (("home", m.home_team, m.home_score), ("away", m.away_team, m.away_score)):
            starters = [bp for bp in (getattr(m, f"{side}_lineup", None) or [])
                        if getattr(bp, "slot_position", "BE") not in S.BENCH_SLOTS]
            if starters and src is None:
                # the scoreboard stood without the sources before the odds: an outage leaves ESPN's numbers
                try:
                    src = week_sources(lg, week, settings, boxes, ref=ref)
                except Exception:  # noqa: BLE001
                    src = {}
            players = merge_weekly([flatten_box_player(bp) for bp in starters], src) if starters else []
            row[side] = {**_club(t), "score": round(score, 2),
                         "proj": (round(sum(S.playable_proj(p) for p in players), 2) if players
                                  else round(float(getattr(m, f"{side}_projected", 0) or 0), 2)),
                         "me": t.team_id == me,
                         "starter_ids": [bp.playerId for bp in starters],
                         "starter_teams": sorted({bp.proTeam for bp in starters if getattr(bp, "proTeam", None)}),
                         "pending_teams": sorted({bp.proTeam for bp in starters if getattr(bp, "proTeam", None)
                                                  and int(getattr(bp, "game_played", 100) or 0) < 100}),
                         "starters": [{k: p[k] for k in SCORE_STARTER} for p in players]}
        odds = None
        if row["away"]["starters"] and row["home"]["starters"]:
            p = S.win_prob(row["away"]["starters"], row["home"]["starters"], sigma)
            odds = {"away": round(p, 3), "home": round(1 - p, 3)}
        board.append({**row, "live": False, "final": S.matchup_final(row), "win_prob": odds})
    # the picker runs on through the playoffs, whose weeks have no pairings until the regular season ends
    playoffs = settings.get("playoff_weeks", [])
    return {"week": week, "current_week": current, "first_week": settings.get("first_week", 1),
            "weeks": max(playoffs, default=settings["reg_season_weeks"]),
            "playoff_teams": settings["playoff_teams"], "playoff_weeks": playoffs, "live": S.is_live() and week == current,
            "standings": standings, "scoreboard": board}


def journal_state(*, ref) -> dict:
    return {"transactions": db.transactions(ref=ref)}


def parse_tx_players(payload: dict) -> dict[int, dict]:
    """A kona_player_info answer as player id -> position and NFL team (None once he is a free
    agent of the NFL)."""
    from espn_api.football.constant import PRO_TEAM_MAP

    from .player_card import POS
    out = {}
    for e in payload.get("players") or []:
        p = e.get("player") or {}
        if p.get("id") is None:
            continue
        team = PRO_TEAM_MAP.get(p.get("proTeamId"))
        out[int(p["id"])] = {"pos": POS.get(p.get("defaultPositionId")), "team": team if team and team != "None" else None}
    return out


def tx_player_info(ids: set[int], *, ref) -> dict[int, dict]:
    """Position and NFL team of every player in the transaction log, in one call. A D/ST's id is
    -16000 less its club's id, so it needs no lookup; a failed read leaves the rest without (the
    page shows the name alone) rather than failing the page."""
    from espn_api.football.constant import PRO_TEAM_MAP
    info = {pid: {"pos": "D/ST", "team": PRO_TEAM_MAP.get(-16000 - pid)} for pid in ids if pid < 0}
    people = sorted(pid for pid in ids if pid > 0)
    # No "limit" in the filter: ESPN answers 400 to one beside filterIds. Asked in chunks so a
    # season's worth of ids never meets whatever page size it applies instead.
    for i in range(0, len(people), TX_CHUNK):
        filt = {"players": {"filterIds": {"value": people[i:i + TX_CHUNK]}}}
        try:
            info |= parse_tx_players(raw_league_get("kona_player_info", headers={"x-fantasy-filter": json.dumps(filt)}, ref=ref))
        except Exception as e:  # noqa: BLE001 — positions and clubs are dressing; the moves stand without them
            print(f"transaction player info unavailable: {e}")
    return info


TX_CHUNK = 40


_RECONCILED: dict[str, frozenset] = {}  # league key -> the disagreement last synced for, so one the feed cannot close syncs once


def reconcile_log(lg, owners: dict[int, int], *, ref) -> bool:
    """Sync the transaction log when ESPN's rosters (player_id -> team id) disagree with what the
    draft and the log say, so a move shows in the record as soon as any page sees its result. True
    when it synced. A disagreement the feed cannot close (a commissioner move it never reports)
    syncs once, not on every rebuild."""
    from . import journal
    drift = frozenset(S.ownership_drift(owners, S.log_owners(draft_picks(ref=ref), journal_state(ref=ref)["transactions"])))
    key = ref.key
    if not drift or drift == _RECONCILED.get(key):
        return False
    _RECONCILED[key] = drift
    journal.sync_transactions(lg, ref=ref)
    return True


def transactions_state(*, ref) -> dict:
    """The Transactions page: the journal's log grouped into moves (season.group_transactions), each
    side dressed with its fantasy team as the League page shows it (current name: teams get
    renamed, and the log keeps the name at the time). The log is synced from ESPN's activity feed
    first: it otherwise grows only on Refresh, and a move made since would be missing here and from
    every player's record. A failed sync serves the log as it stands."""
    from . import journal
    lg = fresh_league(ref)
    try:
        journal.sync_transactions(lg, ref=ref)
    except Exception as e:  # noqa: BLE001 — the log on disk is still the record; ESPN down is not a blank page
        print(f"transaction sync failed, serving the log as it stands: {e}")
    rows = journal_state(ref=ref)["transactions"]
    clubs = {t.team_id: _club(t) for t in lg.teams}
    ids = {p[3] for r in rows if (p := S.parse_tx_id(r.get("id", "")))}
    moves = S.group_transactions(rows, tx_player_info(ids, ref=ref), date.fromisoformat(load(ref)["week1_start"]))
    for m in moves:
        m["sides"] = [{**s, **({k: v for k, v in clubs[s["team_id"]].items() if k in ("team", "abbrev", "logo")}
                               if s["team_id"] in clubs else {"abbrev": "", "logo": ""})} for s in m["sides"]]
    return {"me": my_team_id(lg, ref), "current_week": lg.current_week, "moves": moves,
            "teams": sorted(clubs.values(), key=lambda c: c["team"].lower())}


def draft_picks(*, ref) -> list[dict]:
    """Every pick of the draft: overall, round, pick in the round, team id and name, player id,
    and when (the log's stamp). The DB's draft_picks is the record; when it is empty, ESPN's own
    draft (League.draft, complete once the draft is done) stands in, in pick order, with no time
    (ESPN keeps none)."""
    rows = db.picks(ref=ref)
    if rows:
        return [{"overall": r["overall"], "round": r["round"], "pick": r["pick"], "team_id": r["team_id"],
                 "team": r["team"], "player_id": r["player_id"], "at": r["ts"]} for r in rows]
    return [{"overall": i, "round": p.round_num, "pick": p.round_pick, "team_id": p.team.team_id,
             "team": p.team.team_name, "player_id": p.playerId, "at": None}
            for i, p in enumerate(fresh_league(ref).draft, 1)]



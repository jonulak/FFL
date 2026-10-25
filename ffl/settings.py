"""A league's settings from ESPN into the DB (ffl/formats.py), and back out as one dict.

    uv run python -m ffl.settings [league key]    # read ESPN, store, print the summary (default: the home league)
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone

from espn_api.football.constant import POSITION_MAP, SETTINGS_SCORING_FORMAT_MAP

from . import db, formats, leagues
from .config import raw_league_get

NON_STARTER_SLOTS = {20, 21}  # bench, IR


def nfl_week1_start(season: int) -> date:
    """The Tuesday week 1 starts on: kickoff is the Thursday after the first Monday of September."""
    sep1 = date(season, 9, 1)
    first_monday = sep1 + timedelta(days=(0 - sep1.weekday()) % 7)
    return first_monday + timedelta(days=1)  # kickoff Thursday minus two days


def starters_by_position(slot_counts: dict[int, int]) -> dict[str, int]:
    return {POSITION_MAP.get(s, str(s)): n for s, n in slot_counts.items() if n and s not in NON_STARTER_SLOTS}


# rosterSettings.positionLimits is keyed by a player's defaultPositionId, not by the lineup slot ids above.
LIMIT_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}


def position_limits(limits: dict) -> dict[str, int]:
    """ESPN's cap on how many of a position one team may roster; -1 (no cap) is left out."""
    return {LIMIT_POS[int(k)]: v for k, v in limits.items() if int(k) in LIMIT_POS and v >= 0}


def playoff_weeks(schedule: dict) -> list[int]:
    """The NFL weeks of the fantasy playoffs: the scoring periods of every matchup period after the regular season's."""
    reg = schedule["matchupPeriodCount"]
    return sorted(w for mp, weeks in schedule.get("matchupPeriods", {}).items() if int(mp) > reg for w in weeks)


def first_week(status: dict, draft_ms: int | None, week1_start: date) -> int:
    """The league's first scoring week. ESPN reports 1 until a league has drafted, so a league joined once the
    season is under way takes the week after its draft (the 2026-10-25 public draft, in week 7: ESPN said 8
    once it was over); a preseason league, or one that has drafted, keeps ESPN's."""
    fw = status.get("firstScoringPeriod") or 1
    under_way = max(status.get("currentMatchupPeriod") or 0, status.get("latestScoringPeriod") or 0) > 1
    if fw > 1 or not under_way or not draft_ms:
        return fw
    from .season import week_of  # season imports board, which imports this module
    drafted = datetime.fromtimestamp(draft_ms / 1000, tz=timezone.utc).astimezone().date()
    return max(1, week_of(drafted, week1_start) + 1)


def fetch(ref) -> dict:
    """ESPN's settings for the league: today's settings.json dict plus `first_week` (the first scoring
    week, `first_week()`) and `scoring_items` (the raw items, every position's pointsOverrides kept, for the formats)."""
    raw = raw_league_get(["mSettings", "mTeam"], ref=ref)
    status = raw.get("status") or raw_league_get(["mStatus"], ref=ref).get("status") or {}
    s = raw["settings"]
    slot_counts = {int(k): v for k, v in s["rosterSettings"]["lineupSlotCounts"].items()}
    draft = s["draftSettings"]
    scoring = []
    for item in s["scoringSettings"].get("scoringItems", []):
        meta = SETTINGS_SCORING_FORMAT_MAP.get(item["statId"], {"abbr": f"stat{item['statId']}", "label": "Unknown"})
        # the flat list keeps what it always held (D/ST's override, else the base: live scoring reads it);
        # every position's own points are in the formats, built from scoring_items
        pts = item.get("pointsOverrides", {}).get("16") or item.get("points", 0)
        if pts:
            scoring.append({"stat_id": item["statId"], "abbr": meta["abbr"], "label": meta["label"], "points": pts})

    members = {m["id"]: m for m in raw.get("members", [])}
    teams = []
    for t in raw["teams"]:
        owners = [members.get(o, {}) for o in t.get("owners", [])]
        teams.append({
            "id": t["id"],
            "abbrev": t.get("abbrev"),
            "name": t.get("name") or f"{t.get('location','')} {t.get('nickname','')}".strip(),
            "owners": [f"{o.get('firstName','')} {o.get('lastName','')}".strip() for o in owners],
            "owner_swids": t.get("owners", []),
        })

    season = ref.season
    return {
        "league_id": int(ref.provider_id),
        "season": season,
        "week1_start": nfl_week1_start(season).isoformat(),
        "name": s["name"],
        "team_count": s["size"],
        "scoring_type": s["scoringSettings"].get("scoringType"),
        "reg_season_weeks": s["scheduleSettings"]["matchupPeriodCount"],
        "playoff_teams": s["scheduleSettings"]["playoffTeamCount"],
        "playoff_weeks": playoff_weeks(s["scheduleSettings"]),
        "roster_slots": {POSITION_MAP.get(k, str(k)): v for k, v in slot_counts.items() if v},
        "roster_slot_ids": slot_counts,
        "starters": starters_by_position(slot_counts),
        "roster_size": sum(v for k, v in slot_counts.items() if k != 21),
        "bench_size": slot_counts.get(20, 0),
        "ir_size": slot_counts.get(21, 0),
        "position_limits": position_limits(s["rosterSettings"].get("positionLimits", {})),
        "draft": {
            "type": draft.get("type"),
            "order_type": draft.get("orderType"),
            "date_ms": draft.get("date"),
            "pick_order": draft.get("pickOrder"),
            "keeper_count": draft.get("keeperCount"),
            "time_per_pick_s": draft.get("timePerSelection"),
            "auction_budget": draft.get("auctionBudget"),
        },
        "faab": s["acquisitionSettings"].get("isUsingAcquisitionBudget"),
        "faab_budget": s["acquisitionSettings"].get("acquisitionBudget"),
        "waiver_hours": s["acquisitionSettings"].get("waiverHours"),
        "trade_deadline_ms": s.get("tradeSettings", {}).get("deadlineDate"),
        "scoring": scoring,
        "teams": teams,
        "first_week": first_week(status, draft.get("date"), nfl_week1_start(season)),
        "scoring_items": s["scoringSettings"].get("scoringItems", []),
    }


def collect(ref=None) -> dict:
    """Read the league's settings from ESPN and store them; returns what load() now answers."""
    ref = ref or leagues.current()
    ref = formats.store(ref, fetch(ref))
    return load(ref)


def load(ref=None) -> dict:
    """The league's settings (None: the current league, the home one unless FFL_LEAGUE names another): the
    dict settings.json used to hold, plus `first_week`, `league_key`, `scoring_tables` and `scoring_items` (formats.assemble).
    A fresh dict on every call."""
    ref = ref or leagues.current()
    stored = db.league_settings_doc(ref.key, ref.season)
    if stored is None:
        raise SystemExit(f"no settings stored for {ref.key} {ref.season}: run `uv run python -m ffl.settings {ref.key}`")
    return formats.assemble(ref.key, stored)


RESCORED_POS = ("QB", "RB", "WR", "TE")   # K and D/ST take a source's own points


def rescoring(settings: dict) -> dict[str, dict[str, float]]:
    """{pos: {stat: points}} over formats.RESCORED, signed as the league scores them, for each position a
    source is rescored at (sleeper.score takes one): the league's per-position tables (`scoring_tables`,
    a TE premium only on TE's), else, for a dict without them, the flat `scoring` list at every position."""
    tables = settings.get("scoring_tables") or formats.scoring_from_espn(formats.items_from_list(settings["scoring"]))[0]
    return {p: {k: float(v) for k, v in tables[p].items() if k in formats.RESCORED} for p in RESCORED_POS}


def key_scoring(scoring: list[dict]) -> dict:
    """The headline points off the flat list (board header, the 1QB ADP feed's PPR, ffl.settings' summary);
    rescoring reads `rescoring`. Signed as the league scores them; `int` is the interception thrown
    (INTT), not the D/ST interception (INT), which only matched it while both were worth 2 (ffl-twi)."""
    by = {x["abbr"]: x["points"] for x in scoring}
    return {k: by.get(a, 0) for k, a in {
        "ppr": "REC", "pass_td": "PTD", "pass_yd": "PY", "int": "INTT",
        "rush_yd": "RY", "rush_td": "RTD", "rec_yd": "REY", "rec_td": "RETD", "fumble": "FUML",
    }.items()}


def to_markdown(d: dict) -> str:
    dr = d["draft"]
    when = (datetime.fromtimestamp(dr["date_ms"] / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
            if dr.get("date_ms") else "unset")
    L = [f"# {d['name']} ({d['season']})", "",
         f"- League ID: {d['league_id']}",
         f"- Teams: {d['team_count']}  |  Reg season: {d['reg_season_weeks']} wks  |  Playoff teams: {d['playoff_teams']}"
         f"  |  Playoff weeks: {', '.join(map(str, d.get('playoff_weeks', [])))}",
         f"- Draft: {dr['type']} / order={dr['order_type']} / {when} / {dr['time_per_pick_s']}s per pick / keepers={dr['keeper_count']}",
         f"- FAAB: {d['faab']} (budget {d['faab_budget']})  |  waiver hours: {d['waiver_hours']}",
         f"- Week 1 starts: {d['week1_start']}",
         "", "## Roster", "| Slot | Count |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in d["roster_slots"].items()]
    L += ["", f"Starters: {d['starters']}  |  Bench: {d['bench_size']}  |  IR: {d['ir_size']}",
          f"Position limits: {d.get('position_limits') or 'none'}", "",
          "## Scoring (non-zero)", "| Stat | Points |", "|---|---|"]
    L += [f"| {x['label']} ({x['abbr']}) | {x['points']} |" for x in d["scoring"]]
    order = dr.get("pick_order") or []
    pos = {tid: i + 1 for i, tid in enumerate(order)}
    L += ["", "## Teams", "| Pick | Team ID | Abbrev | Name | Owner |", "|---|---|---|---|---|"]
    L += [f"| {pos.get(t['id'], '-')} | {t['id']} | {t['abbrev']} | {t['name']} | {', '.join(t['owners'])} |"
          for t in sorted(d["teams"], key=lambda t: pos.get(t["id"], 99))]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None):
    argv = sys.argv[1:] if argv is None else argv
    d = collect(leagues.get(argv[0]) if argv else None)
    print(to_markdown(d))
    print("Key scoring:", key_scoring(d["scoring"]))


if __name__ == "__main__":
    main()

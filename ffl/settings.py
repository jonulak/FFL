"""Dump league settings to data/settings.json and data/settings.md."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from espn_api.football.constant import POSITION_MAP, SETTINGS_SCORING_FORMAT_MAP

from .config import DATA, league, raw_league_get

NON_STARTER_SLOTS = {20, 21}  # bench, IR


def starters_by_position(slot_counts: dict[int, int]) -> dict[str, int]:
    return {POSITION_MAP.get(s, str(s)): n for s, n in slot_counts.items() if n and s not in NON_STARTER_SLOTS}


def collect() -> dict:
    lg = league()
    raw = raw_league_get(["mSettings", "mTeam"])
    s = raw["settings"]
    slot_counts = {int(k): v for k, v in s["rosterSettings"]["lineupSlotCounts"].items()}
    draft = s["draftSettings"]
    scoring = []
    for item in s["scoringSettings"].get("scoringItems", []):
        meta = SETTINGS_SCORING_FORMAT_MAP.get(item["statId"], {"abbr": f"stat{item['statId']}", "label": "Unknown"})
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

    return {
        "league_id": lg.league_id,
        "season": lg.year,
        "name": s["name"],
        "team_count": s["size"],
        "scoring_type": s["scoringSettings"].get("scoringType"),
        "reg_season_weeks": s["scheduleSettings"]["matchupPeriodCount"],
        "playoff_teams": s["scheduleSettings"]["playoffTeamCount"],
        "roster_slots": {POSITION_MAP.get(k, str(k)): v for k, v in slot_counts.items() if v},
        "roster_slot_ids": slot_counts,
        "starters": starters_by_position(slot_counts),
        "roster_size": sum(v for k, v in slot_counts.items() if k != 21),
        "bench_size": slot_counts.get(20, 0),
        "ir_size": slot_counts.get(21, 0),
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
    }


def load() -> dict:
    p = DATA / "settings.json"
    if not p.exists():
        raise SystemExit("data/settings.json missing: run `uv run python -m ffl.settings` first")
    return json.loads(p.read_text())


def key_scoring(scoring: list[dict]) -> dict:
    by = {x["abbr"]: x["points"] for x in scoring}
    return {k: by.get(a, 0) for k, a in {
        "ppr": "REC", "pass_td": "PTD", "pass_yd": "PY", "int": "INT",
        "rush_yd": "RY", "rush_td": "RTD", "rec_yd": "REY", "rec_td": "RETD", "fumble": "FUML",
    }.items()}


def to_markdown(d: dict) -> str:
    dr = d["draft"]
    when = (datetime.fromtimestamp(dr["date_ms"] / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
            if dr.get("date_ms") else "unset")
    L = [f"# {d['name']} ({d['season']})", "",
         f"- League ID: {d['league_id']}",
         f"- Teams: {d['team_count']}  |  Reg season: {d['reg_season_weeks']} wks  |  Playoff teams: {d['playoff_teams']}",
         f"- Draft: {dr['type']} / order={dr['order_type']} / {when} / {dr['time_per_pick_s']}s per pick / keepers={dr['keeper_count']}",
         f"- FAAB: {d['faab']} (budget {d['faab_budget']})  |  waiver hours: {d['waiver_hours']}",
         "", "## Roster", "| Slot | Count |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in d["roster_slots"].items()]
    L += ["", f"Starters: {d['starters']}  |  Bench: {d['bench_size']}  |  IR: {d['ir_size']}", "",
          "## Scoring (non-zero)", "| Stat | Points |", "|---|---|"]
    L += [f"| {x['label']} ({x['abbr']}) | {x['points']} |" for x in d["scoring"]]
    order = dr.get("pick_order") or []
    pos = {tid: i + 1 for i, tid in enumerate(order)}
    L += ["", "## Teams", "| Pick | Team ID | Abbrev | Name | Owner |", "|---|---|---|---|---|"]
    L += [f"| {pos.get(t['id'], '-')} | {t['id']} | {t['abbrev']} | {t['name']} | {', '.join(t['owners'])} |"
          for t in sorted(d["teams"], key=lambda t: pos.get(t["id"], 99))]
    return "\n".join(L) + "\n"


def main():
    d = collect()
    (DATA / "settings.json").write_text(json.dumps(d, indent=2))
    md = to_markdown(d)
    (DATA / "settings.md").write_text(md)
    print(md)
    print("Key scoring:", key_scoring(d["scoring"]))


if __name__ == "__main__":
    main()

"""Scoring and lineup formats, shared by every league that plays them, and a league's settings in the DB.

A league's scoring is six per-position tables (neutral stat name -> points), each stored once in
`scoring_formats` under a content key and linked per position in `league_scoring`: two leagues on the
same PPR scoring share six rows, and a TE premium shares all but TE's. `rescore_key` hashes only the
stats a projection source can be rescored on (RESCORED), so a league that differs only in, say, the
fumble-recovery TD prices players off the same rescored projections. A lineup format (starters, bench,
roster size) is one `lineup_formats` row, linked by `leagues.lineup_format`. Everything else ESPN's
settings say (name, teams, draft, waivers, the flat `scoring` list) is the league's `league_settings`
row; its provider position limits are `league_caps.provider_limit`. `settings.load` assembles them back
into the dict `settings.json` used to be.

Pure except store / league_table; `rows_for` is what both `store` and migration 0015 write.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime

from espn_api.football.constant import SETTINGS_SCORING_FORMAT_MAP

POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST")
ESPN_SLOT_POS = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 17: "K", 16: "D/ST"}   # pointsOverrides keys
LINEUP_KEYS = ("team_count", "starters", "roster_slots", "bench_size", "ir_size", "roster_size")

RESCORED = ("pass_yd", "pass_td", "pass_int", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td", "fum_lost",
            "pass_2pt", "rush_2pt", "rec_2pt")

_EVERY = (5, 10, 20, 25, 50)
OFFENSE = {
    "PA": "pass_att", "PC": "pass_cmp", "INC": "pass_inc", "PY": "pass_yd", "PTD": "pass_td", "INTT": "pass_int",
    "2PC": "pass_2pt", "PY100": "pass_yd_100", "PC5": "pass_cmp_5", "PC10": "pass_cmp_10",
    "IP5": "pass_inc_5", "IP10": "pass_inc_10", "PTD40": "pass_td_40", "PTD50": "pass_td_50",
    "P300": "pass_300", "P400": "pass_400", "CPCT": "pass_cmp_pct", "PYPG": "pass_yd_pg",
    "RA": "rush_att", "RY": "rush_yd", "RTD": "rush_td", "2PR": "rush_2pt", "R100": "rush_yd_100",
    "RA5": "rush_att_5", "RA10": "rush_att_10", "RTD40": "rush_td_40", "RTD50": "rush_td_50",
    "RY100": "rush_100", "RY200": "rush_200", "RYPA": "rush_ypa", "RYPG": "rush_yd_pg",
    "REC": "rec", "REY": "rec_yd", "RETD": "rec_td", "2PRE": "rec_2pt", "RE100": "rec_yd_100",
    "REC5": "rec_5", "REC10": "rec_10", "RETD40": "rec_td_40", "RETD50": "rec_td_50",
    "REY100": "rec_100", "REY200": "rec_200", "RET": "rec_tgt", "YAC": "rec_yac", "YPC": "rec_ypc",
    "REYPG": "rec_yd_pg", "PTL": "two_pt",
    "FTD": "fum_rec_td", "SKD": "sacked", "PFUM": "pass_fum", "RFUM": "rush_fum", "REFUM": "rec_fum",
    "FUM": "fum", "PFUML": "pass_fum_lost", "RFUML": "rush_fum_lost", "REFUML": "rec_fum_lost",
    "FUML": "fum_lost", "TT": "turnovers", "PFD": "pass_fd", "RFD": "rush_fd", "REFD": "rec_fd",
    "O2PRET": "off_2pt_ret", "O1PSF": "off_1pt_safety",
    **{f"PY{n}": f"pass_yd_{n}" for n in _EVERY}, **{f"RY{n}": f"rush_yd_{n}" for n in _EVERY},
    **{f"REY{n}": f"rec_yd_{n}" for n in _EVERY},
    **{f"PTD{n}": f"pass_td_{n}" for n in (0, 10, 20, 30)}, **{f"RTD{n}": f"rush_td_{n}" for n in (0, 10, 20, 30)},
    **{f"RETD{n}": f"rec_td_{n}" for n in (0, 10, 20, 30)},
}
RETURNS = {   # scored by a returner and by a D/ST alike
    "KRTD": "kr_td", "PRTD": "pr_td", "KR": "kr_yd", "PR": "pr_yd", "KR10": "kr_yd_10", "KR25": "kr_yd_25",
    "PR10": "pr_yd_10", "PR25": "pr_yd_25", "2PRET": "two_pt_ret", "1PSF": "one_pt_safety",
}
KICKING = {
    "FG0": "fg_0_39", "FGA0": "fga_0_39", "FGM0": "fg_miss_0_39", "FG40": "fg_40_49", "FGA40": "fga_40_49",
    "FGM40": "fg_miss_40_49", "FG50": "fg_50_59", "FGA50": "fga_50_59", "FGM50": "fg_miss_50_59",
    "FG60": "fg_60", "FGA60": "fga_60", "FGM60": "fg_miss_60", "FG50P": "fg_50p", "FGA50P": "fga_50p",
    "FGM50P": "fg_miss_50p", "FG": "fg", "FGA": "fga", "FGM": "fg_miss", "PAT": "pat", "PATA": "pat_att",
    "PATM": "pat_miss", "FGY": "fg_yd", "FGMY": "fg_miss_yd", "FGAY": "fga_yd",
    **{f"FGY{n}": f"fg_yd_{n}" for n in (*_EVERY, 100)}, **{f"FGMY{n}": f"fg_miss_yd_{n}" for n in (*_EVERY, 100)},
    **{f"FGAY{n}": f"fga_yd_{n}" for n in (*_EVERY, 100)},
}
DEFENSE = {
    "PA0": "pa_0", "PA1": "pa_1_6", "PA7": "pa_7_13", "PA14": "pa_14_17", "PA18": "pa_18_21", "PA22": "pa_22_27",
    "PA28": "pa_28_34", "PA35": "pa_35_45", "PA46": "pa_46", "PTSA": "pts_allowed", "PAPG": "pts_allowed_pg",
    "YA": "yds_allowed", "YA100": "ya_0_99", "YA199": "ya_100_199", "YA299": "ya_200_299", "YA349": "ya_300_349",
    "YA399": "ya_350_399", "YA449": "ya_400_449", "YA499": "ya_450_499", "YA549": "ya_500_549", "YA550": "ya_550",
    "YAPG": "yds_allowed_pg",
    "INT": "def_int", "FR": "def_fum_rec", "BLKK": "blk_kick", "SF": "safety", "SK": "sack", "HALFSK": "half_sack",
    "BLKKRTD": "blk_kick_td", "DEFRETTD": "def_ret_td", "INTTD": "int_td", "FRTD": "fum_ret_td", "TRTD": "ret_td",
    "FF": "fum_forced", "TKA": "tkl_ast", "TKS": "tkl_solo", "TK": "tkl", "TK3": "tkl_3", "TK5": "tkl_5",
    "STF": "stuff", "PD": "pass_def", "D2PRET": "def_2pt_ret", "D1PSF": "def_1pt_safety",
    "DPTSA": "dst_pts_allowed", "DPA0": "dst_pa_0", "DPA1": "dst_pa_1_6", "DPA7": "dst_pa_7_13",
    "DPA14": "dst_pa_14_17", "DPA18": "dst_pa_18_21", "DPA22": "dst_pa_22_27", "DPA28": "dst_pa_28_34",
    "DPA35": "dst_pa_35_45", "DPA46": "dst_pa_46", "DPAPG": "dst_pts_allowed_pg",
}
NEUTRAL = {**OFFENSE, **RETURNS, **KICKING, **DEFENSE}   # ESPN abbr -> neutral stat name
_OFF = frozenset(OFFENSE.values()) | frozenset(RETURNS.values())
POSITION_STATS = {"QB": _OFF, "RB": _OFF, "WR": _OFF, "TE": _OFF, "K": frozenset(KICKING.values()),
                  "D/ST": frozenset(DEFENSE.values()) | frozenset(RETURNS.values())}
_SLOT = {pos: str(slot) for slot, pos in ESPN_SLOT_POS.items()}


def _abbr(stat_id: int) -> str:
    return SETTINGS_SCORING_FORMAT_MAP.get(stat_id, {}).get("abbr") or f"stat{stat_id}"


def items_from_list(scoring: list[dict]) -> list[dict]:
    """The flat `scoring` list (settings.json's, no overrides) as raw ESPN scoringItems."""
    return [{"statId": x["stat_id"], "points": x["points"]} for x in scoring]


def scoring_from_espn(items: list[dict]) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Raw ESPN scoringItems -> ({pos: neutral table}, unmapped abbrs). Each position takes the item's
    override for its own lineup slot (an override of 0 is a 0), else the base points, over its own stats
    only; a stat worth 0 is left out, so a table names only what scores."""
    tables: dict[str, dict[str, float]] = {pos: {} for pos in POSITIONS}
    unmapped = set()
    for it in items:
        abbr = _abbr(it["statId"])
        base = float(it.get("points") or 0)
        over = {str(k): float(v) for k, v in (it.get("pointsOverrides") or {}).items()}
        name = NEUTRAL.get(abbr)
        if name is None:
            if base or any(over.values()):
                unmapped.add(abbr)
            continue
        for pos in POSITIONS:
            if name in POSITION_STATS[pos]:
                pts = over.get(_SLOT[pos], base)
                if pts:
                    tables[pos][name] = pts
    return tables, sorted(unmapped)


def _digest(doc) -> str:
    return hashlib.sha1(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


def scoring_key(pos: str, table: dict[str, float]) -> str:
    return f"sc-{pos}-" + _digest(table)


def rescore_key(pos: str, table: dict[str, float]) -> str:
    """The key over the stats projections are rescored on: leagues that share it share rescored projections."""
    return scoring_key(pos, {k: v for k, v in table.items() if k in RESCORED})


def lineup_from_settings(s: dict) -> dict:
    return {k: s.get(k) for k in LINEUP_KEYS}


def lineup_key(lineup: dict) -> str:
    return "ln-" + _digest(lineup)


def rows_for(league: str, season: int, doc: dict, now: str | None = None) -> dict:
    """Every row a league's settings document becomes, by table (plus `lineup_key` and `name`). `doc` is
    ffl.settings.fetch's dict, or settings.json's (no raw `scoring_items`: the flat list, no overrides;
    no `first_week`: 1). Caps rows carry the provider limit only; my caps are never written here."""
    now = now or datetime.now().astimezone().isoformat(timespec="seconds")
    doc = dict(doc)
    items = doc.pop("scoring_items", None)
    first_week = doc.pop("first_week", None) or 1
    tables, unmapped = scoring_from_espn(items if items is not None else items_from_list(doc.get("scoring", [])))
    lineup = lineup_from_settings(doc)
    lkey = lineup_key(lineup)
    limits = doc.get("position_limits") or {}
    rest = {k: v for k, v in doc.items() if k not in LINEUP_KEYS and k != "position_limits"}
    return {
        "scoring_formats": [{"key": scoring_key(p, t), "pos": p, "rescore_key": rescore_key(p, t), "table": t,
                             "added_at": now} for p, t in tables.items()],
        "league_scoring": [{"league": league, "pos": p, "scoring_format": scoring_key(p, t), "unmapped": unmapped}
                           for p, t in tables.items()],
        "lineup_formats": [{"key": lkey, "doc": lineup, "added_at": now}],
        "league_settings": [{"league": league, "season": season, "fetched_at": now,
                             "doc": {**rest, "_keys": list(doc), "first_week": first_week, "scoring_items": items}}],
        "league_caps": [{"league": league, "pos": p, "provider_limit": limits.get(p), "my_cap": None}
                        for p in POSITIONS],
        "lineup_key": lkey,
        "name": (doc.get("name") or "").strip() or None,
    }


def assemble(ref_key: str, stored: dict) -> dict:
    """db.league_settings_doc's rows -> the dict settings.json used to be, plus first_week and league_key."""
    doc, lineup = stored["doc"], stored["lineup"]
    if lineup is None:   # the registry row links no lineup format, or links one that is not stored
        raise SystemExit(f"no lineup format linked to {ref_key}: run `uv run python -m ffl.settings {ref_key}`")
    limits = {p: c["provider_limit"] for p, c in stored["caps"].items() if c["provider_limit"] is not None}
    out = {}
    for k in doc["_keys"]:
        if k in LINEUP_KEYS:
            out[k] = lineup.get(k)
        elif k == "position_limits":
            out[k] = {p: limits[p] for p in POSITIONS if p in limits}
        else:
            out[k] = doc[k]
    out["first_week"] = doc.get("first_week") or 1
    out["league_key"] = ref_key
    return out


def store(ref, settings_doc: dict):
    """Upsert the league's formats, settings row and provider caps (my caps kept), and link the registry row,
    in one transaction."""
    from . import db, leagues
    rows = rows_for(ref.key, ref.season, settings_doc)
    cur = next((r for r in leagues.all_leagues() if r.key == ref.key), ref)
    cur = replace(cur, lineup_format=rows["lineup_key"], name=rows["name"] or cur.name)
    db.put_league_settings(rows, {k: getattr(cur, k) for k in leagues._FIELDS})
    return leagues.get(ref.key)


def position_formats(ref) -> dict[str, str]:
    """{pos: scoring_formats key} linked to the league, every position (ESPN's projections are scored on the
    whole table, so a pool is keyed by this, not by rescore_key)."""
    from . import db
    keys = db.league_scoring_keys(ref.key)
    if set(keys) != set(POSITIONS):
        raise SystemExit(f"no scoring formats linked to {ref.key}: run `uv run python -m ffl.settings {ref.key}`")
    return keys


def league_table(ref) -> dict[str, dict[str, float]]:
    """The six tables linked to the league, for live scoring and box scores."""
    from . import db
    return db.league_scoring_tables(ref.key)

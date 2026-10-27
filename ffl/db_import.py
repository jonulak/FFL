"""One-time import of the old flat files into the DB (`uv run python -m ffl.db import`).

Insert-if-absent everywhere, never upsert: the old Mac deploy ran this on every deploy while the files existed,
and a stale file must not overwrite rows the API has written since. Each reader returns
(source_count, rows); a missing file is skipped (source 0).
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from pathlib import Path

from . import db


def _mtime(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")


def _csv(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with p.open(newline="") as f:
        return list(csv.DictReader(f))


def _num(v, cast):
    return cast(v) if v not in (None, "") else None


def _transactions(journal: Path, season: int):
    rows = [{"season": season, "id": r["id"], "date": r["date"], "type": r["type"], "team": r["team"],
             "player": r["player"], "player_id": db._tx_player_id(r["id"]), "action": r["action"],
             "bid": int(r.get("bid") or 0), "reasoning": r.get("reasoning") or ""}
            for r in _csv(journal / "transactions.csv")]
    return len(rows), rows


def _picks(journal: Path, season: int):
    ints, floats = ("overall", "round", "pick", "team_id", "player_id"), ("adp_2qb", "board_rank_at_pick", "proj")
    from . import leagues
    rows, path = [], journal / "draft" / "picks.csv"
    league = leagues.home().key if path.exists() else None  # the old log is the home league's draft
    for r in _csv(path):
        row = {"league": league, "season": season, **{k: (v if v != "" else None) for k, v in r.items()}}
        row.update({k: _num(r[k], int) for k in ints})
        row.update({k: _num(r[k], float) for k in floats})
        row["team"], row["player"] = row["team"] or "", row["player"] or ""  # NOT NULL, as put_picks coerces
        rows.append(row)
    return len(rows), rows


def _results(journal: Path, season: int):
    rows = [{"season": season, "week": int(r["week"]), "opponent": r["opponent"], "my_score": float(r["my_score"]),
             "opp_score": float(r["opp_score"]), "result": r["result"]}
            for r in _csv(journal / "results" / "weekly.csv")]
    return len(rows), rows


def _plays(data: Path, season: int):
    rows = []
    for p in sorted((data / "live").glob("plays-week-*.jsonl")):
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                rows.append({"season": season, "week": r["week"], "at": r["at"], "player_id": r["player_id"],
                             "team_id": r.get("team_id"), "total": r["total"], "body": r})
    return len(rows), rows


_OUTLOOK = re.compile(r"^(week|waivers-week)-(\d+)\.json$")  # anchored: `week` must not also take `waivers-week`


def _outlooks(data: Path, season: int):
    rows, source = [], 0
    for p in sorted((data / "outlook").glob("*week-*.json")):
        m = _OUTLOOK.match(p.name)
        if not m:
            continue
        updated = _mtime(p)
        for pid, entry in json.loads(p.read_text()).items():
            if not pid.isdigit():
                continue
            source += 1
            rows.append({"season": season, "week": int(m[2]), "kind": m[1], "player_id": int(pid),
                         "entry": entry, "updated": updated})
    return source, rows


def _calibration(data: Path, season: int, home):
    source = made = 0
    for p in sorted((data / "calibration").glob("week-*.json")):
        doc = json.loads(p.read_text())
        source += len(doc["rows"])
        if db.put_snapshot(doc["week"], doc["k"], doc["rows"], season, ref=home):
            made += len(doc["rows"])
    return source, made


def _sigma(data: Path, season: int):
    p = data / "calibration" / "sigma.json"
    if not p.exists():
        return 0, []
    return 1, [{"season": season, "fitted_at": _mtime(p), "fit": json.loads(p.read_text())}]


def _plans(journal: Path, season: int):
    rows = []
    for p in sorted((journal / "waivers").glob("week-*.json")):
        doc = json.loads(p.read_text())
        rows.append({"season": season, "week": doc["week"], "plan": doc, "updated": doc.get("saved_at") or _mtime(p)})
    return len(rows), rows


def _decisions(journal: Path, data: Path, season: int):
    from . import decisions, season as season_mod
    p = journal / "DECISIONS.md"
    # an in-season entry with no "Week N" in its title takes its date's week; with no week1_start it stays weekless
    s = data / "settings.json"
    w1 = json.loads(s.read_text()).get("week1_start") if s.exists() else None
    week_of = (lambda day: season_mod.week_of(date.fromisoformat(day), date.fromisoformat(w1))) if w1 else None
    rows = [{"season": season, **r} for r in decisions.parse_journal(p.read_text(), week_of)] if p.exists() else []
    return len(rows), rows


def _home():
    from . import leagues
    return leagues.home()


# the tables run() files under the home league, whose the old files all are (_picks stamps its own; sigma_fits is global)
_LEAGUE_TABLES = {"transactions", "results", "plays", "outlooks", "claim_plans", "decisions"}


def run(journal: Path, data: Path, season: int) -> list[dict]:
    report, home = [], _home()                      # rows are stamped and counted in the same (home) league
    for table, (source, rows) in [("transactions", _transactions(journal, season)), ("draft_picks", _picks(journal, season)),
                                  ("results", _results(journal, season)), ("plays", _plays(data, season)),
                                  ("outlooks", _outlooks(data, season)), ("sigma_fits", _sigma(data, season)),
                                  ("claim_plans", _plans(journal, season)), ("decisions", _decisions(journal, data, season))]:
        if table in _LEAGUE_TABLES:
            rows = [{"league": home.key, **r} for r in rows]
        # first row per key wins (DO NOTHING keeps it); source counts distinct keys, dupes the rows lost to a shared key
        distinct = {}
        for r in rows:
            distinct.setdefault(tuple(r[k] for k in db._KEYS[table]), r)
        new = db.insert_new(table, list(distinct.values()))
        report.append({"table": table, "source": len(distinct), "dupes": len(rows) - len(distinct),
                       "in_db": db.count(table, season, ref=home), "new": new})
    source, made = _calibration(data, season, home)  # put_snapshot per week file; rows newly written
    report.append({"table": "calibration_rows", "source": source, "dupes": 0,
                   "in_db": db.count("calibration_rows", season, ref=home), "new": made})
    return report


def main(journal: Path, data: Path, season: int) -> int:
    report = run(journal, data, season)
    for r in report:
        print(f"{r['table']:<18} source {r['source']:>5}  dupes {r['dupes']:>3}  in db {r['in_db']:>5}  new {r['new']:>5}")
    # the same player, second and total is one play event, so plays dupes are accepted; anywhere else a shared key is a lost row
    lost = [r["table"] for r in report if r["dupes"] > 0 and r["table"] != "plays"]
    if lost:
        print(f"import: rows sharing a key in the files, only the first was kept: {', '.join(lost)}")
        return 1
    short = [r["table"] for r in report if r["in_db"] < r["source"]]
    if short:
        print(f"import: fewer rows in the DB than in the files for {', '.join(short)}")
        return 1
    print("import: done")
    return 0

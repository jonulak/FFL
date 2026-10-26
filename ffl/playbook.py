"""Every NFL play, stored once, and the plays called back that cost a league starter points.

A play is an entry {play_id, game_id, week, quarter, clock, text, offence, defence, roles: [{id, type}], at,
source}. Two sources write them to the nfl_plays table (ffl/db.py): FastCast's live document as the feed sees it
(`from_fastcast`: lastPlay, the drive's plays, the scoring plays; source "live") and ESPN's core-API drives
(`from_drives`, the NFL tab's read; source "espn"), which win once read: they carry the officials' final wording.
Both use the same play ids, so a play-feed row (ffl/plays.py) links to its play by `play_id` and shows its text.

A play called back ("NULLIFIED", "- No Play") moves no points, so the feed, built off points changes, never had
a row for it. `callbacks` makes one per starter it cost: what the play would have scored him (`would_be`: the text
before the flag, read for yards, a touchdown, a field goal, a sack or an interception, scored by the league's
table) against what it kept him (his feed rows on that play: yards to the spot of a foul still count). The text
is read, not the stats, so it is an estimate: a lateral or a fumble on the play is not read.

    uv run python -m ffl.playbook backfill 1-7     # weeks' plays from ESPN's drives, and their called-back rows
"""
from __future__ import annotations

import re
import sys
from datetime import datetime

from . import fastcast as fc
from .nfl import club

CALLED_BACK = ("nullified", "no play")
_FLAG = re.compile(r"penalty on", re.I)
_YARDS = re.compile(r"\bfor (-?\d+) yards?|\bfor (no gain)", re.I)
_FG = re.compile(r"(\d+) yard field goal is good", re.I)


def called_back(text: str | None) -> bool:
    """Whether a play's text says it no longer counts (a review's "REVERSED" can make a catch: it is not one)."""
    low = (text or "").lower()
    return any(w in low for w in CALLED_BACK)


def offence(play: dict) -> str | None:
    """FastCast: the ESPN team id ("16") that had the ball at the snap."""
    team = (play.get("start") or {}).get("team")
    return str(team.get("id") if isinstance(team, dict) else team) if team is not None else None


def _mmss(secs) -> str | None:
    return f"{int(secs) // 60}:{int(secs) % 60:02d}" if isinstance(secs, (int, float)) else None


def _local(iso: str | None) -> str | None:
    """ESPN's UTC wallclock ("2026-10-18T20:06:46Z") as the feed's naive local time."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    except (AttributeError, ValueError):
        return None


def _int(raw) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def from_fastcast(doc: dict, week: int, at: datetime) -> list[dict]:
    """The plays in a FastCast document, each once; `at` (when the feed first saw them) stands for their time."""
    out: dict[str, dict] = {}
    for e in doc.get("events", []):
        clubs = {str(c["id"]): club(c["abbreviation"]) for c in e.get("competitors", [])
                 if c.get("id") is not None and c.get("abbreviation")}
        seen = [e.get("lastPlay") or {}] + ((e.get("drive") or {}).get("plays") or []) + (e.get("scoringPlays") or [])
        for p in seen:
            if not p.get("playId"):
                continue
            off = clubs.get(offence(p) or "")
            out[str(p["playId"])] = {
                "play_id": str(p["playId"]), "game_id": str(e.get("id")), "week": week,
                "quarter": p.get("period") if isinstance(p.get("period"), int) else (p.get("period") or {}).get("number"),
                "clock": _mmss(p.get("clock")), "text": (p.get("text") or "").strip(), "offence": off,
                "defence": next((c for c in clubs.values() if c != off), None) if off else None,
                "roles": [{"id": i, "type": x.get("type")} for x in p.get("players") or [] if (i := _int(x.get("playerId")))],
                "at": at.isoformat(timespec="seconds"), "source": "live"}
    return list(out.values())


def from_drives(drives: list[dict], game_id: str, week: int, clubs: tuple[str, str]) -> list[dict]:
    """The plays of one game's drives (nfl.parse_drives), source "espn"."""
    out = []
    for d in drives:
        for p in d["plays"]:
            off = club(p["team"]) if p.get("team") else None
            out.append({"play_id": str(p["id"]), "game_id": str(game_id), "week": week, "quarter": p.get("quarter"),
                        "clock": p.get("clock"), "text": p.get("text") or "", "offence": off,
                        "defence": next((club(c) for c in clubs if club(c) != off), None) if off else None,
                        "roles": p.get("roles") or [], "at": _local(p.get("wallclock")), "source": "espn"})
    return out


_SAME = ("text", "roles", "quarter", "clock", "offence", "defence")


def changes(old: dict | None, new: dict) -> bool:
    """Whether `new` should replace the stored play: ESPN's read wins over the live feed's, never the reverse."""
    if old is None:
        return True
    if old["source"] == "espn" and new["source"] == "live":
        return False
    return old["source"] != new["source"] or any(old.get(k) != new.get(k) for k in _SAME)


def versioned(old: dict | None, new: dict, seen: str) -> dict:
    """`new` with the play's wordings so far (`texts`: [{at, text}], oldest first), its own added as first
    `seen` when it reads differently from the last."""
    texts = list((old or {}).get("texts") or ([{"at": old.get("at") or seen, "text": old["text"]}] if old else []))
    if not texts or texts[-1]["text"] != new["text"]:
        texts.append({"at": seen, "text": new["text"]})
    return {**new, "texts": texts}


# Rows about the play changing: they show its wording as it stands now
REVISIONS = ("reversed", "adjusted", "called_back")


def wording(play: dict, row: dict) -> str | None:
    """The text a feed row on `play` shows: a revision row (`REVISIONS`, or one a called-back row was folded
    into: plays.fold_called_back) the newest wording, any other the
    wording in force when the row happened, so a catch credited at 55 yards still reads 55 above the stat
    fix that made it 54. A row older than every stored wording (weeks before plays were stored) keeps the
    text it was written with."""
    texts = play.get("texts") or [{"at": play.get("at") or "", "text": play.get("text") or ""}]
    if row.get("match") in REVISIONS or row.get("called_back"):
        return texts[-1]["text"] or row.get("text")
    then = [t for t in texts if t["at"] <= (row.get("at") or "")]
    return (then[-1]["text"] if then else row.get("text") or texts[0]["text"]) or None


def _yards(run: str) -> int | None:
    m = _YARDS.search(run)
    return None if m is None else 0 if m.group(2) else int(m.group(1))


def _fg(dist: int) -> str:
    """The league's FG-made stat by distance: 0-39, 40-49, 50-59, 60+."""
    return "80" if dist < 40 else "77" if dist < 50 else "198" if dist < 60 else "201"


def play_stats(play: dict, player_id: int, pos: str, team: str | None) -> dict[str, float]:
    """The stats a play would have given one player, read off its text up to the flag."""
    text = play.get("text") or ""
    m = _FLAG.search(text)
    run = text[:m.start()] if m else text
    low = run.lower()
    td = "touchdown" in low
    if pos == "D/ST":
        if not team or club(team) != play.get("defence"):
            return {}
        out = {"99": 1.0} if "sacked" in low else {}
        if "intercepted" in low:
            out.update({"95": 1.0, **({"103": 1.0} if td else {})})
        return out
    types = {r.get("type") for r in play.get("roles") or [] if r.get("id") == player_id}
    yards = _yards(run)
    passed = "pass" in low and "incomplete" not in low and "intercepted" not in low
    out: dict[str, float] = {}
    if passed and yards is not None and "passer" in types:
        out.update({"3": yards, **({"4": 1.0} if td else {})})
    if passed and yards is not None and "receiver" in types:
        out.update({"53": 1.0, "42": yards, **({"43": 1.0} if td else {})})
    if "pass" not in low and yards is not None and "rusher" in types:
        out.update({"24": yards, **({"25": 1.0} if td else {})})
    if "kicker" in types:
        if fg := _FG.search(run):
            out[_fg(int(fg.group(1)))] = 1.0
        elif "extra point is good" in low:
            out["86"] = 1.0
    return out


def would_be(play: dict, player_id: int, pos: str, team: str | None, table: dict[str, dict[int, float]]) -> float:
    return round(fc.player_points(play_stats(play, player_id, pos, team), fc.position_table(table, pos)), 2)


def callbacks(play: dict, roster: dict[int, dict], kept: dict[int, float], table: dict[str, dict[int, float]]) -> list[dict]:
    """The called-back rows of one play: a starter it names (a D/ST: its club on defence) whose points it would
    have raised past what it kept him (`kept`: his feed rows' deltas on this play). [] once it is not called back."""
    if not called_back(play.get("text")):
        return []
    named = {r.get("id") for r in play.get("roles") or []}
    rows = []
    for pid, who in roster.items():
        dst = who["pos"] == "D/ST" and play.get("defence") and club(who.get("team") or "") == play["defence"]
        if pid not in named and not dst:
            continue
        full, have = would_be(play, pid, who["pos"], who.get("team"), table), round(kept.get(pid, 0.0), 2)
        if full <= have:
            continue
        rows.append({"at": play.get("at"), "week": play["week"], "player_id": pid, **who,
                     "delta": 0.0, "total": None, "would_be": full, "kept": have,
                     "text": play.get("text") or None, "play_id": play["play_id"], "quarter": play.get("quarter"),
                     "clock": play.get("clock"), "game_id": play.get("game_id"), "match": "called_back"})
    return rows


def _weeks(arg: str) -> list[int]:
    lo, _, hi = arg.partition("-")
    return list(range(int(lo), int(hi or lo) + 1))


def backfill(weeks: list[int]) -> None:
    """Each week's finished games' plays from ESPN's drives, through that week's feed store."""
    from . import db, espn_season, nfl, plays
    from .settings import load
    table = fc.scoring_table(load())
    done = set(db.get_league_state("final_plays_done", []))  # the tick's record (api.final_plays): these need no read
    for w in weeks:
        roster = plays.roster_index(espn_season.league_starters(w))
        store = plays.Store(w)
        games = [g for g in nfl.scoreboard(w)[0]["games"] if g["status"] == "post"]
        n = 0
        for g in games:
            clubs = (g["away"]["abbr"], g["home"]["abbr"])
            ids = {g["away"]["id"]: clubs[0], g["home"]["id"]: clubs[1]}
            entries = from_drives(nfl.drives(g["event_id"], g["status"], ids)[0], g["event_id"], w, clubs)
            store.record(entries, roster, table)
            n += len(entries)
            done.add(g["event_id"])
        db.update_league_state("final_plays_done", lambda cur: sorted(set(cur or []) | done), [])
        fixed = plays.old_revisions(store.rows)
        db.update_plays(fixed)
        print(f"week {w}: {len(games)} games, {n} plays, {len(store.called)} called back, {len(fixed)} old rows retagged")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "backfill":
        backfill(_weeks(sys.argv[2]))
    else:
        sys.exit(__doc__)

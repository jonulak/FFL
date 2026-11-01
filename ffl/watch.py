"""The watchlist, pure: what moved for a player I am watching, what the push says, and how the Watch page groups them.

A signal is `{key, player_id, kind, text, at}`, one of KINDS (the four detectors, a chip each on every row) or
`available` (the drop that frees him: it moves a player to the top of the page, pushes once, and adds its chip only
once it has fired). `key` is the dedupe: the same event found on the
next run is the same key. Thresholds are the spec's: usage compares the newest game with the mean of the earlier
games in his form window; a depth-chart teammate counts when Sleeper's chart or ESPN's feed says Doubtful, Out or IR
(Questionable never). Nothing here reads the db or the network; the caller hands in the pool rows, the chart, the
statuses and the stored state.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .adp import norm_name
from .notify import BODY_MAX

SNAP_JUMP = 15
TGT_JUMP = 3
RZ_JUMP = 2
NEWS_PROB = 0.5
MOVING_DAYS = 3
KINDS = ("ahead", "usage", "news", "bench")
SEP = " · "
HURT = {"doubtful": "Doubtful", "out": "Out", "ir": "IR", "injured reserve": "IR", "injury_reserve": "IR"}
RECEIVERS = ("RB", "WR", "TE")


def hurt_status(s: str | None) -> str | None:
    """Doubtful, Out or IR under any of their spellings; Questionable, Day-To-Day, Active and nothing are None."""
    return HURT.get((s or "").strip().lower())


def above(player: dict, chart_rows: list[dict]) -> set[str]:
    """Normalized names at his position ahead of him on his club's chart; empty when the chart does not name him."""
    me = norm_name(player["name"])
    mine = next((r for r in chart_rows if r["pos"] == player["pos"] and norm_name(r["name"]) == me), None)
    if mine is None:
        return set()
    return {norm_name(r["name"]) for r in chart_rows if r["pos"] == player["pos"] and r["order"] < mine["order"]}


def ahead_signals(player: dict, chart_rows: list[dict], statuses: dict[str, str | None], seen_above: set[str],
                  at: str, names: dict[str, str] | None = None) -> tuple[list[dict], set[str]]:
    """A hurt player ahead of him. `statuses` is norm name → status, ESPN's feed, and it wins: a name it has is read
    there alone (its Questionable over the chart's Out is no signal); only a name it lacks reads the chart's own
    `injury`. `seen_above` is everyone who has stood ahead of him: Sleeper
    moves a hurt starter down its order, and he is still the one whose absence matters. The chip names the
    teammate by his chart name, else `names` (norm name → display, the feed's), else the normalized name.
    Returns the signals and the new seen set."""
    now_above = above(player, chart_rows)
    seen = seen_above | now_above
    on_chart = {norm_name(r["name"]): r for r in chart_rows if r["pos"] == player["pos"]}
    out = []
    for n in sorted(seen):
        row = on_chart.get(n)
        status = hurt_status(statuses[n]) if n in statuses else hurt_status(row and row.get("injury"))
        if status:
            display = (row and row["name"]) or (names or {}).get(n) or n
            out.append({"key": f"ahead:{player['player_id']}:{n}:{status}", "player_id": player["player_id"],
                        "kind": "ahead", "text": f"{display} {status}", "at": at})
    return out, seen


def _mean(rows: list[dict], f) -> float | None:
    vals = [v for v in map(f, rows) if v is not None]
    return sum(vals) / len(vals) if vals else None


def _looks(r: dict) -> int | None:
    a, b = r.get("rz_tgt"), r.get("rz_att")
    return None if a is None and b is None else (a or 0) + (b or 0)


def usage_signal(player: dict, at: str) -> dict | None:
    """The newest game's snap share, targets or red-zone looks well above the earlier games' mean, the biggest
    jump (against its threshold) named. A field the rows lack skips that test; one game has nothing to compare."""
    rows = sorted((player.get("form") or {}).get("rows") or [], key=lambda r: r["week"])
    if len(rows) < 2:
        return None
    new, earlier = rows[-1], rows[:-1]
    tests = [("snaps", lambda r: r.get("snap_pct"), SNAP_JUMP, "%")]
    if player["pos"] in RECEIVERS:
        tests.append(("targets", lambda r: r.get("tgt"), TGT_JUMP, ""))
    tests.append(("red-zone looks", _looks, RZ_JUMP, ""))
    best = None
    for label, f, jump, unit in tests:
        now, was = f(new), _mean(earlier, f)
        if now is None or was is None or now - was < jump:
            continue
        if best is None or (now - was) / jump > best[0]:
            best = ((now - was) / jump, f"{label} {round(was)}→{round(now)}{unit}")
    if best is None:
        return None
    return {"key": f"usage:{player['player_id']}:{new['week']}", "player_id": player["player_id"], "kind": "usage",
            "text": best[1], "at": at}


def weakest_at(roster: list[dict], pos: str) -> dict | None:
    """My lowest usage-adjusted ROS at `pos`, IR left out like `season.weakest_rostered`."""
    mine = [p for p in roster if p["pos"] == pos and p.get("slot") != "IR"]
    return min(mine, key=lambda p: p["ros_adj"]) if mine else None


def bench_signal(player: dict, prev_delta: float | None, weakest: dict | None, ros_week: int, at: str) -> dict | None:
    """His worth to my roster crossed from nothing (or less) to something since the last run."""
    if prev_delta is None or not prev_delta <= 0 < player["delta_roster"]:
        return None
    text = f"passes {weakest['name']} on your bench" if weakest else f"passes your weakest {player['pos']}"
    return {"key": f"bench:{player['player_id']}:{ros_week}", "player_id": player["player_id"], "kind": "bench",
            "text": text, "at": at}


def available_signal(player: dict, prev_owner: int | None, day: str, at: str) -> dict | None:
    """He was on a roster at the last run and is on none now."""
    if prev_owner is None or player.get("owner") is not None:
        return None
    return {"key": f"available:{player['player_id']}:{day}", "player_id": player["player_id"], "kind": "available",
            "text": "available: dropped", "at": at}


def news_signal(story: dict, row: dict, at: str) -> dict | None:
    """A news-desk ▲ on him that the tagger is at least NEWS_PROB sure of, with a reason, unflagged."""
    if (row["effect"] != "up" or row["prob"] < NEWS_PROB or row.get("flag")
            or not (row.get("reason") or "").strip()):
        return None
    return {"key": f"news:{row['player_id']}:{story['id']}:{story['rev']}", "player_id": row["player_id"],
            "kind": "news", "text": f"▲ {row['reason'].strip()}", "at": at}


def notice_for(player: dict, signals: list[dict], day: str, at: str) -> dict:
    """The day's one push for a player: his name, every signal's text. `day` in the key is what limits him to one
    a day, since `add_notices` is insert-if-absent."""
    body = SEP.join(s["text"] for s in signals)
    if len(body) > BODY_MAX:
        body = body[:BODY_MAX - 1] + "…"
    return {"key": f"watch:{player['player_id']}:{day}", "kind": "watch", "title": player["name"], "body": body,
            "url": "/players?watch=1", "created_at": at}


def page(rows: list[dict], signals: list[dict], now: datetime) -> dict:
    """The Watch page: each row with a chip per KINDS (his newest signal of that kind, lit within MOVING_DAYS), and an
    `available` chip after them once that has fired, so a row Moving on a drop alone says why; grouped. Elsewhere is whoever is rostered; Moving has a lit chip or an `available` signal inside the window,
    newest first; Quiet is the rest by `add`."""
    cutoff = now - timedelta(days=MOVING_DAYS)
    by_player: dict[int, list[dict]] = {}
    for s in signals:
        by_player.setdefault(s["player_id"], []).append(s)
    groups: dict[str, list[tuple[datetime, dict]]] = {"moving": [], "quiet": [], "elsewhere": []}
    for row in rows:
        mine = by_player.get(row["player_id"], [])
        chips = []
        for kind in (*KINDS, "available"):
            s = max((s for s in mine if s["kind"] == kind), key=lambda s: s["at"], default=None)
            if kind == "available" and s is None:
                continue
            chips.append({"kind": kind, "text": s and s["text"], "at": s and s["at"],
                          "lit": bool(s) and datetime.fromisoformat(s["at"]) >= cutoff})
        fresh = [t for t in (datetime.fromisoformat(s["at"]) for s in mine) if t >= cutoff]
        row = {**row, "chips": chips}
        if row.get("owner") is not None:
            groups["elsewhere"].append((datetime.min, row))
        elif fresh:
            groups["moving"].append((max(fresh), row))
        else:
            groups["quiet"].append((datetime.min, row))
    return {"moving": [r for _, r in sorted(groups["moving"], key=lambda t: t[0], reverse=True)],
            "quiet": sorted((r for _, r in groups["quiet"]), key=lambda r: r.get("add") or 0, reverse=True),
            "elsewhere": [r for _, r in groups["elsewhere"]]}

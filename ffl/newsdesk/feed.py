"""The news feed as the pages read it, pure: stories dressed with each player's name and, read at request time,
who holds him. Owners are never stored; the same story shows a different tag the day he is claimed or dropped."""
from __future__ import annotations

from ..adp import norm_name
from .players import Index

FILTERS = ("all", "mine", "fa", "others")
SEARCH_MAX = 10


def owner_tag(pid: int, owners: dict[int, dict], me: int) -> tuple[dict | None, bool]:
    """(the team holding him as {team_id, name, abbrev}, or None for a free agent; whether it is mine)."""
    team = owners.get(pid)
    if team is None:
        return None, False
    return {"team_id": team.get("team_id"), "name": team.get("name"), "abbrev": team.get("abbrev")}, team.get("team_id") == me


def player_head(pid: int, owners: dict[int, dict], me: int, index: Index) -> dict:
    info = index.by_id.get(pid) or {}
    owner, mine = owner_tag(pid, owners, me)
    return {"player_id": pid, "name": info.get("name") or f"Player {pid}", "pos": info.get("pos"),
            "team": info.get("team"), "owner": owner, "mine": mine}


def _row(p: dict, owners: dict[int, dict], me: int, index: Index) -> dict:
    return {**player_head(p["player_id"], owners, me, index), **{k: p[k] for k in ("effect", "kind", "reason", "flag")}}


def _order(story_players: list[dict]) -> list[dict]:
    """▲/▼ first by prob, then ●."""
    return sorted(story_players, key=lambda p: (p["effect"] == "none", -p["prob"]))


def _cite(item: dict, sources: dict[str, dict]) -> dict:
    s = sources.get(item["source_id"]) or {}
    return {"source": s.get("name") or item["source_id"], "url": item["url"] or s.get("url"), "at": item["published_at"]}


def shape(stories: list[dict], owners: dict[int, dict], me: int, index: Index, sources: dict[str, dict],
          player: int | None = None) -> list[dict]:
    out = []
    for s in stories:
        # a withdrawn item is no longer a source, unless every one was: the retracted story still names what it was
        items = [i for i in s["items"] if not i.get("withdrawn_at")] or s["items"]
        cites = [_cite(i, sources) for i in sorted(items, key=lambda i: (i["published_at"], i["id"]))]
        rows = _order(s["players"])
        if player is not None:
            rows = [p for p in rows if p["player_id"] == player]
        out.append({"id": s["id"], "headline": s["headline"], "overview": s["overview"], "status": s["status"],
                    "status_note": (sources.get(s["status_note"]) or {}).get("name") or s["status_note"], "updated": s["rev"] > 1, "at": s["updated_at"],
                    "first": cites[0] if cites else None, "sources": cites[1:],
                    "players": [_row(p, owners, me, index) for p in rows]})
    return out


def keep(story: dict, filter: str) -> bool:
    """Whether a shaped story shows under the filter: some row is mine / a free agent / on another team."""
    rows = story["players"]
    if filter == "mine":
        return any(p["mine"] for p in rows)
    if filter == "fa":
        return any(p["owner"] is None for p in rows)
    if filter == "others":
        return any(p["owner"] is not None and not p["mine"] for p in rows)
    return True


def moves(story: dict) -> bool:
    """A shaped story moves somebody: some row is ▲ or ▼. The /news feed hides the rest unless asked (`quiet`)."""
    return any(p["effect"] != "none" for p in story["players"])


def search(index: Index, q: str) -> list[dict]:
    """Up to SEARCH_MAX players: a prefix on any name token first, then the name containing it; the more
    widely held first within each."""
    needle = norm_name(q)
    if not needle:
        return []
    hits = []
    for pid, info in index.by_id.items():
        name = norm_name(info["name"])
        rank = 0 if any(t.startswith(needle) for t in name.split()) or name.startswith(needle) else 1 if needle in name else None
        if rank is not None:
            hits.append((rank, -index.owned.get(pid, 0.0), info["name"], pid))
    hits.sort()
    return [{"player_id": pid, "name": index.by_id[pid]["name"], "pos": index.by_id[pid]["pos"],
             "team": index.by_id[pid]["team"]} for _, _, _, pid in hits[:SEARCH_MAX]]

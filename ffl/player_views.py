"""The Players table's views (ffl-8iz): the columns and filters a view may name, the filters
themselves, the ALL cap, and the presets kept under one state key (`player_presets`). Pure:
api.py reads and writes the store. COLUMNS mirrors web/lib/player-columns.ts, id for id."""
import math
import re

POSITIONS = ("QB", "RB", "WR", "TE", "D/ST", "K")
ALL = "ALL"
CAP = 150  # ALL with no filter: the first CAP by the view's sort (espn_season.POOL_SIZE's depth)

_TOP = {"add": "add", "wk": "week_proj", "ros": "ros_adj", "ros_model": "ros_proj", "ros_spread": "ros_spread",
        "d_roster": "delta_roster", "d_pool": "delta_pool"}
_FORM = ("l3", "beat", "pace_gap", "opp", "snap_pct", "tgt_snap", "ypt", "weight", "air_share", "air_catch",
         "rz_tgt", "rz_att", "rz_pass")
COLUMNS = (*_TOP, *_FORM, "exp")
SORTS = ("name", *COLUMNS)
# the set filters: a row's bucket, and the buckets a filter may name
_INJURY = {"QUESTIONABLE": "qd", "DOUBTFUL": "qd", "OUT": "out", "INJURY_RESERVE": "out", "SUSPENSION": "out"}
SETS = {"injury": ("healthy", "qd", "out"), "avail": ("fa", "waivers", "theirs", "mine")}
_TEAM = re.compile(r"^[A-Z]{2,4}$")

SEED = {"id": "waivers", "name": "Waivers", "pos": "RB", "fa": True,
        "cols": ["add", "wk", "ros", "beat", "pace_gap", "opp", "snap_pct"], "filters": [],
        "sort": {"id": "add", "desc": True}}
_FIELDS = ("name", "pos", "fa", "cols", "filters", "sort")


def value(row: dict, col: str):
    """A column's figure on a pool row; None when he has none."""
    if col in _TOP:
        return row.get(_TOP[col])
    if col == "exp":
        return row.get("exp")
    return (row.get("form") or {}).get(col)


def _bucket(row: dict, field: str) -> str:
    if field == "injury":
        return _INJURY.get(row.get("injury") or "", "healthy")
    if field == "avail":
        if row.get("mine"):
            return "mine"
        if row.get("owner"):
            return "theirs"
        return "fa" if row.get("status") == "FREEAGENT" else "waivers"  # a failed read (None) is a claim
    return (row.get("team") or "").upper()


def _passes(row: dict, f: dict) -> bool:
    if f["op"] == "in":
        return _bucket(row, f["field"]) in f["value"]
    v = value(row, f["field"])
    if v is None:
        return False  # no figure fails the filter: no form yet, a kicker's snaps
    return v >= f["value"] if f["op"] == "ge" else v <= f["value"]


def filter_players(rows: list[dict], filters: list[dict]) -> list[dict]:
    """The rows every filter passes, in their order."""
    return [r for r in rows if all(_passes(r, f) for f in filters)]


def cap(rows: list[dict], sort: dict) -> tuple[list[dict], bool]:
    """ALL with no filter: the first CAP rows by the view's sort, a missing figure last either way."""
    if len(rows) <= CAP:
        return rows, False
    if sort["id"] == "name":
        ordered = sorted(rows, key=lambda r: r.get("name") or "", reverse=sort["desc"])
    else:
        have = [r for r in rows if value(r, sort["id"]) is not None]
        ordered = sorted(have, key=lambda r: value(r, sort["id"]), reverse=sort["desc"]) \
            + [r for r in rows if value(r, sort["id"]) is None]
    return ordered[:CAP], True


def _filter(f) -> dict:
    if not isinstance(f, dict) or set(f) != {"field", "op", "value"}:
        raise ValueError(f"a filter is field, op and value: {f!r}")
    field, op, v = f["field"], f["op"], f["value"]
    if field in COLUMNS:
        if op not in ("ge", "le") or isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{field} takes ge or le and a number")
        try:
            n = float(v)
        except OverflowError:
            raise ValueError(f"{field} takes a finite number, not {v!r}") from None
        if not math.isfinite(n):
            raise ValueError(f"{field} takes a finite number, not {v!r}")
        return {"field": field, "op": op, "value": n}
    if field not in SETS and field != "team":
        raise ValueError(f"no filter on {field}")
    if op != "in" or not isinstance(v, list) or not v or not all(isinstance(x, str) for x in v):
        raise ValueError(f"{field} takes in and a list")
    if field in SETS and not set(v) <= set(SETS[field]):
        raise ValueError(f"{field} is one of {', '.join(SETS[field])}")
    if field == "team" and not all(_TEAM.match(x) for x in v):
        raise ValueError("team is a club's abbreviation (DET)")
    return {"field": field, "op": "in", "value": list(dict.fromkeys(v))}


def _cols(cols) -> list[str]:
    if not isinstance(cols, list) or not all(c in COLUMNS for c in cols) or len(set(cols)) != len(cols):
        raise ValueError(f"columns are distinct, from {', '.join(COLUMNS)}")
    return list(cols)


def _sort(s) -> dict:
    if not isinstance(s, dict) or s.get("id") not in SORTS or not isinstance(s.get("desc"), bool):
        raise ValueError("sort is a column id and desc true or false")
    return {"id": s["id"], "desc": s["desc"]}


def clean(body: dict, partial: bool = False) -> dict:
    """A preset's fields, checked; partial takes any subset (an update). ValueError names the first fault."""
    if not isinstance(body, dict):
        raise ValueError("a preset is an object")
    missing = [k for k in _FIELDS if k not in body]
    if missing and not partial:
        raise ValueError(f"missing {', '.join(missing)}")
    out: dict = {}
    if "name" in body:
        name = body["name"].strip() if isinstance(body["name"], str) else ""
        if not 1 <= len(name) <= 40:
            raise ValueError("a name is 1 to 40 characters")
        out["name"] = name
    if "pos" in body:
        if body["pos"] not in (*POSITIONS, ALL):
            raise ValueError(f"pos is one of {', '.join((*POSITIONS, ALL))}")
        out["pos"] = body["pos"]
    if "fa" in body:
        if not isinstance(body["fa"], bool):
            raise ValueError("fa is true or false")
        out["fa"] = body["fa"]
    if "cols" in body:
        out["cols"] = _cols(body["cols"])
    if "filters" in body:
        if not isinstance(body["filters"], list):
            raise ValueError("filters is a list")
        out["filters"] = [_filter(f) for f in body["filters"]]
    if "sort" in body:
        out["sort"] = _sort(body["sort"])
    return out


def parse_params(*, pos, fa, cols, f, sort) -> dict:
    """The URL's view overrides (the contract in web/lib/player-view.ts); absent params stay absent."""
    out: dict = {}
    if pos is not None:
        out["pos"] = pos
    if fa is not None:
        out["fa"] = fa
    if cols is not None:
        out["cols"] = _cols([c for c in cols.split(",") if c])
    if f is not None:
        filters = []
        for raw in f:
            if not raw:
                continue  # a bare `f=` says "no filters" over a preset that has some
            parts = raw.split(":", 2)
            if len(parts) != 3:
                raise ValueError(f"a filter is field:op:value, got {raw!r}")
            field, op, v = parts
            if op == "in":
                filters.append(_filter({"field": field, "op": op, "value": [x for x in v.split(",") if x]}))
                continue
            try:
                n = float(v)
            except ValueError:
                raise ValueError(f"{field} needs a number, got {v!r}") from None
            filters.append(_filter({"field": field, "op": op, "value": n}))
        out["filters"] = filters
    if sort is not None:
        sid, _, d = sort.partition(":")
        if d not in ("desc", "asc"):
            raise ValueError("sort is <column>:desc or <column>:asc")
        out["sort"] = _sort({"id": sid, "desc": d == "desc"})
    return out


def load(stored) -> dict:
    """The stored presets, the seed when there are none, the default repaired to one that exists."""
    presets = list((stored or {}).get("presets") or [])
    if not presets:
        return {"presets": [dict(SEED)], "default": SEED["id"]}
    ids = [p["id"] for p in presets]
    default = (stored or {}).get("default")
    return {"presets": presets, "default": default if default in ids else ids[0]}


def _find(store: dict, pid: str) -> dict:
    for p in store["presets"]:
        if p["id"] == pid:
            return p
    raise KeyError(pid)


def resolve(store: dict, preset_id, overrides: dict) -> dict:
    """The view a request asks for: its preset (the default when it names none, or one since
    deleted, so an old link still opens) with the URL's overrides laid over."""
    try:
        base = _find(store, preset_id or store["default"])
    except KeyError:
        base = _find(store, store["default"])
    return {**{k: base[k] for k in _FIELDS}, **overrides, "preset": base["id"]}


def create(store: dict, body: dict, new_id: str) -> dict:
    return {**store, "presets": [*store["presets"], {"id": new_id, **clean(body)}]}


def update(store: dict, pid: str, body: dict) -> dict:
    changed = {**_find(store, pid), **clean(body, partial=True)}
    return {**store, "presets": [changed if p["id"] == pid else p for p in store["presets"]]}


def delete(store: dict, pid: str) -> dict:
    """Drop a preset. The default gone: the seed takes its place (put back if it was deleted too)."""
    _find(store, pid)
    presets = [p for p in store["presets"] if p["id"] != pid]
    default = store["default"]
    if default == pid or not presets:
        if not any(p["id"] == SEED["id"] for p in presets):
            presets = [dict(SEED), *presets]
        default = SEED["id"]
    return {"presets": presets, "default": default}


def set_default(store: dict, pid: str) -> dict:
    _find(store, pid)
    return {**store, "default": pid}

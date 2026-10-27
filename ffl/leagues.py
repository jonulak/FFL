"""The league registry: every league the app knows, the home one included, lives in the `leagues` table.

    uv run python -m ffl.leagues list
    uv run python -m ffl.leagues add espn <id> [--season N] [--home]   # registers it and stores its settings
    uv run python -m ffl.leagues join <key> --user <email> --team <id> [--default]   # a signed-in user joins it
    uv run python -m ffl.leagues caps <key> [POS=N ...]                # my roster caps (no args: the table)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace

from . import config, db


@dataclass(frozen=True)
class LeagueRef:
    key: str                     # "espn:100000001"
    provider: str                # "espn"
    provider_id: str             # "100000001"
    season: int
    name: str
    team_id: int | None
    home: bool
    lineup_format: str | None    # lineup_formats key, set when its settings are stored (formats.store)
    strategy: str | None         # set by task 4
    # whose view of the league this is (ffl-soc.6.2.2): name, team_id and strategy are that member's. `home` stays the
    # league's own flag (the module-level singletons key off it until 4.12); `is_default` is the member's default.
    user_id: int | None = None
    is_default: bool = False
    owner: bool = False          # the member is the league's lowest user id (whose view jobs and the CLI take)


_FIELDS = ("key", "provider", "provider_id", "season", "name", "team_id", "home", "lineup_format", "strategy")
_REF_FIELDS = _FIELDS + ("user_id", "is_default", "owner")


def key_for(provider: str, provider_id: str | int) -> str:
    return f"{provider}:{provider_id}"


def _ref(row: dict) -> LeagueRef:
    return LeagueRef(**{k: row.get(k) for k in _REF_FIELDS})


def all_leagues() -> list[LeagueRef]:
    """Every registered league through its owner's membership (the league's own columns when it has no member),
    home first."""
    return [_ref(r) for r in db.member_rows()]


def _raw(key: str) -> LeagueRef | None:
    """The league's own registry row, no member's view laid over it: what a write to the leagues row starts from."""
    row = next((r for r in db.league_rows() if r["key"] == key), None)
    return _ref(row) if row else None


def for_user(user_id: int) -> list[LeagueRef]:
    """The user's leagues, their default first."""
    return [_ref(r) for r in db.member_rows(user_id)]


def membership(user_id: int, key: str) -> LeagueRef:
    """The user's view of league `key`; SystemExit when they are not a member, the same as an unknown key."""
    ref = next((r for r in for_user(user_id) if r.key == key), None)
    if ref is None:
        raise SystemExit(f"unknown league {key!r}")
    return ref


def default_for(user_id: int) -> LeagueRef | None:
    """The user's default league: the one marked, else their first; None when they have none."""
    refs = for_user(user_id)
    return refs[0] if refs else None


def join(key: str, email: str, team_id: int | None, default: bool = False) -> LeagueRef:
    """Make the user with `email` a member of league `key` holding `team_id`. The only ways a membership is made are
    this, `register`'s owner row and the migration's backfill: a sign-in never makes one."""
    user = db.user_by_email(email)
    if user is None:
        raise SystemExit(f"no user {email!r}: they sign in first")
    if _raw(key) is None:
        raise SystemExit(f"unknown league {key!r}; known: {', '.join(r.key for r in all_leagues()) or 'none'}")
    db.put_membership(user["id"], key, team_id=team_id, **({"is_default": True} if default else {}))
    return membership(user["id"], key)


def home() -> LeagueRef:
    for ref in all_leagues():
        if ref.home:
            return ref
    raise SystemExit("no home league: run `uv run python -m ffl.leagues add espn <id> --home`")


def get(key: str) -> LeagueRef:
    for ref in all_leagues():
        if ref.key == key:
            return ref
    raise SystemExit(f"unknown league {key!r}; known: {', '.join(r.key for r in all_leagues()) or 'none'}")


def current() -> LeagueRef:
    """FFL_LEAGUE (read now, not at import) names a league; unset is the home league. TRANSITIONAL (1b removes the default)."""
    key = os.environ.get("FFL_LEAGUE")
    return get(key) if key else home()


def register(ref: LeagueRef) -> LeagueRef:
    """Upsert the league's row and its owner's membership (the lowest user id; none when there is no user). A member's
    view never reaches the row: an existing league keeps its own name, and takes team and strategy from its owner only."""
    raw = _raw(ref.key)
    row = {k: getattr(ref, k) for k in _FIELDS}
    if raw is not None and ref.user_id is not None:
        row["name"] = raw.name
        if not ref.owner:
            row["team_id"], row["strategy"] = raw.team_id, raw.strategy
    db.put_league(row)
    uid = ref.user_id if ref.user_id is not None else db.owner_id()
    if uid is not None and (ref.user_id is None or ref.owner):
        db.put_membership(uid, ref.key, team_id=ref.team_id, strategy=ref.strategy,
                          **({"is_default": True} if ref.home or ref.is_default else {}))
    return get(ref.key)


NAME_MAX = 60


def rename(ref: LeagueRef, name: str) -> LeagueRef:
    """The league's name as the app shows it (the switcher, pushes, Settings): ESPN's until renamed. Re-reading its
    settings keeps it; only `add` takes ESPN's again. ValueError on a blank name or one over NAME_MAX."""
    clean = " ".join(name.split())
    if not clean or len(clean) > NAME_MAX:
        raise ValueError(f"a league's name is 1 to {NAME_MAX} characters")
    if ref.user_id is not None:  # the member's own name for it (ffl-soc.6.2.2), never anyone else's
        db.put_membership(ref.user_id, ref.key, name=clean)
        return membership(ref.user_id, ref.key)
    return register(replace(ref, name=clean))


def my_team_id(settings: dict, ref: LeagueRef | None = None) -> int:
    """Which team is mine, in the league `ref` names.

    The SWID is the authority when it identifies a team, because it is per-account and right in every
    league. The registry's team_id is per-league: a disagreement resolves to the SWID, loudly. A league
    whose registry row has no team and whose owners lack my SWID has no answer (never another league's).
    `ref` defaults to the registered league `settings` describe, else the home league when they name none.
    """
    key = None
    if ref is None:
        if settings.get("league_id") is None:
            ref = home()
        else:
            key = key_for("espn", settings["league_id"])
            ref = next((r for r in all_leagues() if r.key == key), None)
    else:
        key = ref.key
    registered = ref.team_id if ref else None
    swid = config.SWID
    by_swid = None
    if swid:
        by_swid = next((t["id"] for t in settings["teams"] if swid in t.get("owner_swids", [])), None)
    if by_swid and registered and by_swid != registered:
        name = next((t.get("name", "?") for t in settings["teams"] if t["id"] == by_swid), "?")
        print(f"!! {key or ref.key} says team {registered} but the SWID owns team {by_swid} ({name}) "
              f"in this league — using {by_swid}.", flush=True)
    if by_swid:
        return by_swid
    if registered:
        return registered
    raise SystemExit(f"Could not identify my team in {key or ref.key}: register it in the leagues table (ffl.leagues)")


def add(provider_id: str, season: int | None = None, make_home: bool = False) -> LeagueRef:
    """Register an ESPN league: name from its settings, my team by the SWID among its owners."""
    key = key_for("espn", provider_id)
    existing = next((r for r in all_leagues() if r.key == key), None)
    if existing and existing.home:
        raise SystemExit(f"{key} is already the home league")
    if make_home and any(r.home for r in all_leagues()):
        raise SystemExit("a home league is already registered; --home only works on an empty registry")
    ref = LeagueRef(key, "espn", str(provider_id), season or config.SEASON, key, None, False, None, None)
    raw = config.raw_league_get(["mSettings", "mTeam"], ref=ref)
    name = (raw.get("settings") or {}).get("name") or key
    swid = config.SWID
    team = next((t for t in raw.get("teams", []) if swid and swid in t.get("owners", [])), None)
    ref = register(replace(ref, name=name, team_id=team["id"] if team else None, home=make_home))
    from . import settings
    settings.collect(ref)   # its settings, formats and the league's caps
    return get(ref.key)


CAP_POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST")


def caps(ref: LeagueRef) -> dict[str, int]:
    """How many of each position I may roster: the lower of my cap and the league's limit (either alone
    when the other is unset; a position with neither is left out, uncapped)."""
    out = {}
    for pos, c in db.caps_rows(ref.key).items():
        vals = [v for v in (c["my_cap"], c["provider_limit"]) if v is not None]
        if vals:
            out[pos] = min(vals)
    return {p: out[p] for p in CAP_POSITIONS if p in out}


def set_cap(ref: LeagueRef, pos: str, n: int) -> None:
    """My cap at a position; never above what the league allows."""
    if pos not in CAP_POSITIONS:
        raise SystemExit(f"unknown position {pos!r}; one of {', '.join(CAP_POSITIONS)}")
    if n < 0:
        raise SystemExit(f"a cap is a count of players, not {n}")
    limit = db.caps_rows(ref.key).get(pos, {}).get("provider_limit")
    if limit is not None and n > limit:
        raise SystemExit(f"the league allows at most {limit} {pos}; {pos}={n} not set")
    db.put_my_cap(ref.key, pos, n)


def _caps_table(ref: LeagueRef) -> str:
    rows = db.caps_rows(ref.key)
    eff = caps(ref)
    show = lambda v: "-" if v is None else str(v)
    lines = [f"{ref.key}  {ref.name}", f"{'pos':5} {'league':>6} {'mine':>5} {'cap':>4}"]
    for p in CAP_POSITIONS:
        c = rows.get(p, {})
        lines.append(f"{p:5} {show(c.get('provider_limit')):>6} {show(c.get('my_cap')):>5} {show(eff.get(p)):>4}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv[:1] == ["list"]:
        for r in all_leagues():
            print(f"{r.key}  {r.season}  team {r.team_id if r.team_id is not None else '-'}  "
                  f"{'home' if r.home else '    '}  {r.name}")
        return 0
    if argv[:1] == ["join"] and len(argv) >= 3 and "--user" in argv and "--team" in argv:
        rest = argv[2:]
        ref = join(argv[1], rest[rest.index("--user") + 1], int(rest[rest.index("--team") + 1]),
                   default="--default" in rest)
        print(f"{ref.key}  user {ref.user_id}  team {ref.team_id}  {'default' if ref.is_default else ''}")
        return 0
    if argv[:2] == ["add", "espn"] and len(argv) >= 3:
        rest = argv[3:]
        season = int(rest[rest.index("--season") + 1]) if "--season" in rest else None
        ref = add(argv[2], season, make_home="--home" in rest)
        print(f"{ref.key}  {ref.season}  {ref.name}  "
              f"{'team ' + str(ref.team_id) if ref.team_id is not None else 'my team not found among its owners'}")
        return 0
    if argv[:1] == ["caps"] and len(argv) >= 2:
        ref = get(argv[1])
        sets = []
        for a in argv[2:]:
            pos, eq, n = a.partition("=")
            if not eq or not n.strip().isdigit():
                raise SystemExit(f"expected POS=N, got {a!r}")
            sets.append((pos.strip().upper(), int(n)))
        for pos, n in sets:
            set_cap(ref, pos, n)
        print(_caps_table(ref))
        return 0
    print("usage: python -m ffl.leagues list | add espn <id> [--season N] [--home] | caps <key> [POS=N ...]"
          " | join <key> --user <email> --team <id> [--default]")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))

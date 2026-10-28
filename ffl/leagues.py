"""The league registry: every league the app knows lives in the `leagues` table, and a user's place in one (their team,
their default) in `memberships`. Nothing here picks a league on its own: a caller names one (ffl-soc.4.12).

The CLI acts as FFL_USER (their sign-in email, in .env) on FFL_LEAGUE (or --league), else that user's default:

    uv run python -m ffl.leagues list
    uv run python -m ffl.leagues user add <email>          # a user for the CLI to act as, in a database nobody signs in to
    uv run python -m ffl.leagues add espn <id> [--season N] [--default]   # registers it for FFL_USER, stores its settings
    uv run python -m ffl.leagues join <key> --user <email> --team <id> [--default]   # a user joins a registered league
    uv run python -m ffl.leagues caps <key> [POS=N ...]    # my roster caps (no args: the table)
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
    lineup_format: str | None    # lineup_formats key, set when its settings are stored (formats.store)
    strategy: str | None         # set by task 4
    # whose view of the league this is (ffl-soc.6.2.2): name, team_id and strategy are that member's; `is_default` is
    # the member's default league. None: the league seen through no member (a league nobody has joined).
    user_id: int | None = None
    is_default: bool = False
    owner: bool = False          # the member is the league's lowest user id (whose view jobs take)


_FIELDS = ("key", "provider", "provider_id", "season", "name", "lineup_format", "strategy")  # the leagues row
_REF_FIELDS = _FIELDS + ("team_id", "user_id", "is_default", "owner")


def key_for(provider: str, provider_id: str | int) -> str:
    return f"{provider}:{provider_id}"


def _ref(row: dict) -> LeagueRef:
    return LeagueRef(**{k: row.get(k) for k in _REF_FIELDS})


def all_leagues() -> list[LeagueRef]:
    """Every registered league through its owner's membership (no member: no team), oldest first."""
    return [_ref(r) for r in db.member_rows()]


def joined() -> list[LeagueRef]:
    """`all_leagues` less those nobody is in: what the jobs walk. A league whose last member deleted their account
    (ffl-soc.6.2.4) stays registered, its rows kept, and nothing runs for it until someone joins it again."""
    return [r for r in all_leagues() if r.user_id is not None]


def memberships() -> list[LeagueRef]:
    """Every membership of every league (league oldest first, then user): what personal work walks (each member's
    pushes, watch pass, injuries)."""
    return [_ref(r) for r in db.member_rows(everyone=True)]


def members(key: str) -> list[LeagueRef]:
    """Every member's view of league `key`, lowest user id (the owner) first; [] when nobody has joined it."""
    return [r for r in memberships() if r.key == key]


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


def _user(email: str) -> dict:
    user = db.user_by_email(email)
    if user is None:
        raise SystemExit(f"no user {email!r} in this database: they sign in first, or "
                         f"`uv run python -m ffl.leagues user add {email}` where nobody signs in (a mock database)")
    return user


def join(key: str, email: str, team_id: int | None, default: bool = False) -> LeagueRef:
    """Make the user with `email` a member of league `key` holding `team_id`. The only ways a membership is made are
    this, `register` and the migration's backfill: a sign-in never makes one."""
    user = _user(email)
    if _raw(key) is None:
        raise SystemExit(f"unknown league {key!r}; known: {', '.join(r.key for r in all_leagues()) or 'none'}")
    db.put_membership(user["id"], key, team_id=team_id, **({"is_default": True} if default else {}))
    return membership(user["id"], key)


def acting_user() -> dict:
    """The user a CLI command acts as: FFL_USER (an email, read now), who must exist in this database."""
    email = (os.environ.get("FFL_USER") or "").strip()
    if not email:
        raise SystemExit("FFL_USER is not set: add `FFL_USER=<your sign-in email>` to .env (the CLI acts as that user)")
    return _user(email)


def acting(key: str | None = None) -> LeagueRef:
    """The league a CLI command acts on, as FFL_USER's membership: `key`, else FFL_LEAGUE (read now), else that user's
    default. SystemExit when they are not a member of it, or have no league."""
    user = acting_user()
    key = key or os.environ.get("FFL_LEAGUE") or None
    if key:
        try:
            return membership(user["id"], key)
        except SystemExit:
            raise SystemExit(f"{user['email']} is not a member of {key!r}; theirs: "
                             f"{', '.join(r.key for r in for_user(user['id'])) or 'none'}") from None
    ref = default_for(user["id"])
    if ref is None:
        raise SystemExit(f"{user['email']} has no league: `uv run python -m ffl.leagues add espn <id> --default`")
    return ref


def oldest() -> LeagueRef:
    """The oldest registered league, through its owner. Only for a read of NFL-wide data through a league's own view
    (kona_player_info's player pool is the same in every league): never anyone's data (the app's own notices go to
    the operators, `notify.app_notices`). SystemExit when none is registered."""
    refs = all_leagues()
    if not refs:
        raise SystemExit("no league registered: `uv run python -m ffl.leagues add espn <id> --default`")
    return refs[0]


def get(key: str) -> LeagueRef:
    """League `key` through its owner's membership, as the jobs see it."""
    for ref in all_leagues():
        if ref.key == key:
            return ref
    raise SystemExit(f"unknown league {key!r}; known: {', '.join(r.key for r in all_leagues()) or 'none'}")


def register(ref: LeagueRef, *, user_id: int | None) -> LeagueRef:
    """Upsert the league's row and, for `user_id`, their membership (team `ref.team_id`, their default when
    `ref.is_default`); None files the row alone. A member's view never reaches the row: an existing league keeps its
    own name and strategy when `ref` is a member's."""
    raw = _raw(ref.key)
    row = {k: getattr(ref, k) for k in _FIELDS}
    if raw is not None and ref.user_id is not None:
        row["name"], row["strategy"] = raw.name, raw.strategy
    if raw is not None:  # a ref that does not know them never blanks them
        row["lineup_format"] = row["lineup_format"] or raw.lineup_format
        row["strategy"] = row["strategy"] or raw.strategy
    db.put_league(row)
    if user_id is None:
        return get(ref.key)
    db.put_membership(user_id, ref.key, team_id=ref.team_id, **({"is_default": True} if ref.is_default else {}))
    return membership(user_id, ref.key)


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
    return register(replace(ref, name=clean), user_id=None)


def my_team_id(settings: dict, ref: LeagueRef) -> int:
    """Which team is mine in the league `ref` names: the membership's (ffl-soc.6.2.3). The ESPN keys in the environment
    decide nothing: they are one person's cookie, and would hand any other member that person's team. A membership
    with no team has no answer (never another league's, never the cookie's)."""
    if ref.team_id:
        return ref.team_id
    raise SystemExit(f"Could not identify my team in {ref.key}: `uv run python -m ffl.leagues join` it with --team")


def add(provider_id: str, season: int | None = None, *, user_id: int, default: bool = False) -> LeagueRef:
    """Register an ESPN league for `user_id`: name from its settings, their team by their own SWID among its owners. A league
    already registered is refused: its row (strategy, name) is not this call's to rewrite."""
    key = key_for("espn", provider_id)
    if _raw(key) is not None:
        raise SystemExit(f"{key} is already registered: re-read its settings with `uv run python -m ffl.settings {key}`, "
                         f"or join it with `uv run python -m ffl.leagues join {key} --user <email> --team <id>`")
    ref = LeagueRef(key, "espn", str(provider_id), season or config.SEASON, key, None, None, None)
    from . import espn_auth
    mine = espn_auth.own(user_id)   # their own cookies read it (they are no member yet) and their SWID finds the team
    raw = config.raw_league_get(["mSettings", "mTeam"], cookies=mine, ref=ref)
    name = (raw.get("settings") or {}).get("name") or key
    swid = mine.swid if mine else None
    team = next((t for t in raw.get("teams", []) if swid and swid in t.get("owners", [])), None)
    ref = register(replace(ref, name=name, team_id=team["id"] if team else None, is_default=default), user_id=user_id)
    from . import settings
    settings.collect(ref)   # its settings, formats and the league's caps
    return membership(user_id, ref.key)


CAP_POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST")


def caps(ref: LeagueRef) -> dict[str, int]:
    """How many of each position I may roster: the lower of my cap and the league's limit (either alone
    when the other is unset; a position with neither is left out, uncapped)."""
    out = {}
    for pos, c in db.caps_rows(ref=ref).items():
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
    limit = db.caps_rows(ref=ref).get(pos, {}).get("provider_limit")
    if limit is not None and n > limit:
        raise SystemExit(f"the league allows at most {limit} {pos}; {pos}={n} not set")
    db.put_my_cap(pos, n, ref=ref)


def _caps_table(ref: LeagueRef) -> str:
    rows = db.caps_rows(ref=ref)
    eff = caps(ref)
    show = lambda v: "-" if v is None else str(v)
    lines = [f"{ref.key}  {ref.name}", f"{'pos':5} {'league':>6} {'mine':>5} {'cap':>4}"]
    for p in CAP_POSITIONS:
        c = rows.get(p, {})
        lines.append(f"{p:5} {show(c.get('provider_limit')):>6} {show(c.get('my_cap')):>5} {show(eff.get(p)):>4}")
    return "\n".join(lines)


USAGE = ("usage: python -m ffl.leagues list | user add <email> | add espn <id> [--season N] [--default]"
         " | join <key> --user <email> --team <id> [--default] | caps <key> [POS=N ...]")


def main(argv: list[str]) -> int:
    if argv[:1] == ["list"]:
        for r in all_leagues():
            print(f"{r.key}  {r.season}  owner {r.user_id if r.user_id is not None else '-'}  "
                  f"team {r.team_id if r.team_id is not None else '-'}  {r.name}")
        return 0
    if argv[:2] == ["user", "add"] and len(argv) == 3:
        print(f"user {db.ensure_user(argv[2], None)}  {argv[2]}")
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
        ref = add(argv[2], season, user_id=acting_user()["id"], default="--default" in rest)
        print(f"{ref.key}  {ref.season}  {ref.name}  "
              f"{'team ' + str(ref.team_id) if ref.team_id is not None else 'my team not found among its owners'}")
        return 0
    if argv[:1] == ["caps"] and len(argv) >= 2:
        ref = acting(argv[1])
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
    print(USAGE)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))

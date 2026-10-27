"""One news run (`POST /news/run`, launchd/com.jonulak.ffl-news every 10 minutes): seed, index, collect, recheck,
media, tag, write, notices, health. Each stage is tried on its own and a failure is reported, the rest still run,
like notify.tick; the stages that need the player index are skipped without one. The stage dict is kept as state
`news_last_run` for `GET /news/status`.

Notices go out for the story revisions Haiku wrote. They wait in state `news_to_notify` ([story, rev, at]) until a
notices stage reads the owners, so a revision written while ESPN is down pushes once it is back (if still within
notify.MAX_AGE); one whose story moved on is dropped, its next revision being written and pushed in its place.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import db, notify, watch
from . import collect, health, media, notices, players, seed, tag, write

PENDING = "news_to_notify"


def owners(*, ref) -> tuple[dict[int, dict], int]:
    """(player_id -> the fantasy team holding him this week, my team id) in a league: the one owner
    lookup, read fresh at request time too, never stored. Raises (SystemExit with no league config) rather than guess
    everyone free. The week is the league's own (league_week): before its first week ESPN's has no matchups."""
    from .. import espn_season
    lg = espn_season.fresh_league(ref)
    return (espn_season.owners_in(espn_season.box_scores(lg, espn_season.league_week(lg, ref=ref), ref=ref)),
            espn_season.my_team_id(lg, ref))


def _queue(written: list, *, ref) -> list[list]:
    """A league's revisions waiting for their push, this run's added; past notify.MAX_AGE they are let go."""
    at, t = notify.now(), datetime.now().astimezone()

    def add(cur):
        pending = [p for p in cur or [] if t - datetime.fromisoformat(p[2]) <= notify.MAX_AGE]
        known = {(sid, rev) for sid, rev, _ in pending}
        return pending + [[sid, rev, at] for sid, rev in written if (sid, rev) not in known]
    return db.update_league_state(PENDING, add, [], ref=ref)


def _queued(t: datetime, *, ref) -> tuple[list[dict], dict[int, dict]]:
    """A league's news notices of the dedupe window and the stories they name."""
    since = (t - notices.DEDUPE * 2).isoformat(timespec="seconds")  # a margin over text order; recent() re-checks
    queued = db.notices_since("news:", since, ref=ref)
    sids = {sr[0] for n in queued if (sr := notices.rev_key(n["key"]))}
    return queued, {sid: s for sid in sids if (s := db.story(sid))}


def notify_written(written: list, index: players.Index) -> dict:
    """Queue every league's pushes for the written revisions (`_notify_league`), each league's counts under `leagues`.
    A league whose owners cannot be read keeps its revisions waiting for the next run and never holds back another's;
    its error is in its own line."""
    from .. import leagues
    out: dict = {}
    for r in leagues.all_leagues():
        try:
            out[r.key] = _notify_league(written, index, ref=r)
        except (Exception, SystemExit) as e:  # noqa: BLE001 — its revisions wait in its own queue
            out[r.key] = {"error": f"{type(e).__name__}: {e}"[:300]}
    return {"leagues": out}


def _notify_league(written: list, index: players.Index, *, ref) -> dict:
    """Queue one league's pushes for the written revisions (and any still waiting from a
    run that could not read owners or the player index, whose names the pushes carry).

    A story's own earlier notices never hold back its newer revision: one already sent makes it a material update,
    and one still waiting (quiet hours) is marked `superseded` whatever the newer revision produces: a revision that
    no longer pushes means the news no longer warrants one, so its outdated predecessor must not go out either."""
    pending = _queue(written, ref=ref)
    if not pending:
        return {"stories": 0, "queued": 0, "superseded": 0, "watch": 0}
    who, me = owners(ref=ref)
    muted = {int(p) for p in db.get_state("news_mutes", []) or []}
    pushed = db.notice_keys("news:", ref=ref)
    waiting = [n["key"] for n in db.unsent_notices(ref=ref) if n["key"].startswith("news:")]
    at, t = notify.now(), datetime.now().astimezone()
    queued, stories = _queued(t, ref=ref)
    sources = {s["id"]: s for s in db.news_sources(active_only=False)}
    added: set[tuple[int, str]] = set()  # rows pushed by this run
    out, superseded = [], []
    try:
        watched = set(db.watched_ids(ref=ref))
    except db.DBError as e:  # the watch part is extra: the news pushes go out without it
        print(f"news: watched ids unreadable, no watch signals: {e}")
        watched = set()
    day = at[:10]  # the watch pass's day key, so its push and a news push the same day are one
    found: list[dict] = []
    carried: set[int] = set()  # players a news push of this run already names
    for sid, rev, _ in pending:
        s = db.story(sid)
        if s is None or s["rev"] != rev or s["written_rev"] != rev:
            continue
        for p in s["players"]:
            info = index.info(p["player_id"])
            p["name"], p["pos"] = (index.by_id.get(p["player_id"]) or {}).get("name"), info.get("pos")
        own = f"news:{sid}:"
        mine = [k for k in pushed if k.startswith(own)]
        stale = [k for k in waiting if k.startswith(own)]
        # the status push says "the news you got changed": only for a story that pushed before (sent, none waiting) and
        # whose status push has not gone yet. A correction of news never pushed is news in its own right
        if s["status"] == "retracted" or (s["status"] == "corrected" and mine and not stale
                                          and f"{own}corrected" not in pushed):
            n = notices.for_status(s, mine, at, sources)
        else:
            if notices.is_update(s, mine):
                skip = set()
            else:
                skip = added | notices.recent([n for n in queued if not n["key"].startswith(own)], stories, t)
            n = notices.for_story(s, who, me, muted, at, skip)
            if n:
                rows = notices.rows(s, who, me, muted, skip)
                added |= set(rows)
                carried |= {pid for pid, _ in rows}
        # news still new, like the regular push: a catch-up run's old stories stay in the feed
        fresh = notices._new(max((i["published_at"] for i in s.get("items") or []), default=None), at)
        found += [sig for p in s["players"] if fresh and s["status"] != "retracted" and p["player_id"] in watched
                  and (sig := watch.news_signal(s, p, at))]
        if n:
            out.append(n)
        superseded += [k for k in stale if not n or k != n["key"]]
    db.mark_notices(superseded, "superseded", at, ref=ref)
    try:
        new = db.add_watch_signals(found, ref=ref)
    except db.DBError as e:
        print(f"news: watch signals not saved: {e}")
        new = []
    by_player: dict[int, list[dict]] = {}
    for sig in new:
        by_player.setdefault(sig["player_id"], []).append(sig)
    out += [watch.notice_for({"player_id": pid, "name": (index.by_id.get(pid) or {}).get("name") or f"Player {pid}"},
                             sigs, day, at) for pid, sigs in by_player.items() if pid not in carried and pid not in who and pid not in muted]
    n_queued = db.add_notices(out, ref=ref)
    done = {(sid, rev) for sid, rev, _ in pending}  # only these: one queued since this run read the queue waits on
    db.update_league_state(PENDING, lambda cur: [p for p in cur or [] if (p[0], p[1]) not in done], [], ref=ref)
    return {"stories": len(pending), "queued": n_queued, "superseded": len(superseded), "watch": len(new)}


def _health() -> int:
    return notify.app_notices(health.notices(db.news_sources(), datetime.now().astimezone(), notify.now()))


def run() -> dict:
    out: dict = {}
    index: players.Index | None = None
    now = datetime.now(timezone.utc)

    def stage(name: str, fn, needs_index: bool = False):
        if needs_index and index is None:
            out[name] = {"skipped": "no player index"}
            return None
        try:
            out[name] = fn()
        # reported, never raised: SystemExit (a league's missing settings, an empty registry) would end the API's run thread
        except (Exception, SystemExit) as e:  # noqa: BLE001
            out[name] = {"error": f"{type(e).__name__}: {e}"[:300]}
        return out[name]

    stage("seed", seed.ensure)
    index = stage("index", players.fetch_index)
    if isinstance(index, players.Index):
        out["index"] = len(index.by_id)
    else:
        index = None
    stage("collect", lambda: collect.run_all(index, now), needs_index=True)
    stage("recheck", lambda: collect.recheck(now))  # after run_all: its "still in the feed" is the poll just made
    stage("media", lambda: media.run_all(index, now), needs_index=True)
    stage("tag", lambda: tag.run(index, now=now), needs_index=True)
    stage("write", lambda: write.run(index), needs_index=True)
    written = out["write"].get("written_ids", []) if isinstance(out["write"], dict) else []
    stage("notices", lambda: notify_written(written, index), needs_index=True)  # its revisions wait for the index
    stage("health", _health)
    try:
        db.put_state("news_last_run", {"at": notify.now(), "stages": out})
    except db.DBError as e:
        out["saved"] = {"error": str(e)}
    return out

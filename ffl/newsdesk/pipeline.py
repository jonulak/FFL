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

from .. import db, notify
from . import collect, health, media, notices, players, seed, tag, write

PENDING = "news_to_notify"


def owners() -> tuple[dict[int, dict], int]:
    """(player_id -> the fantasy team holding him this week, my team id): the one owner lookup, read fresh at
    request time too, never stored. Raises (SystemExit with no league config) rather than guess everyone free."""
    from .. import espn_season
    lg = espn_season.fresh_league()
    return espn_season.owners_in(espn_season.box_scores(lg, lg.current_week)), espn_season.my_team_id(lg)


def _queue(written: list) -> list[list]:
    """The revisions waiting for their push, this run's added; past notify.MAX_AGE they are let go."""
    at, t = notify.now(), datetime.now().astimezone()
    pending = [p for p in db.get_state(PENDING, []) or [] if t - datetime.fromisoformat(p[2]) <= notify.MAX_AGE]
    known = {(sid, rev) for sid, rev, _ in pending}
    pending += [[sid, rev, at] for sid, rev in written if (sid, rev) not in known]
    db.put_state(PENDING, pending)
    return pending


def _queued(t: datetime) -> tuple[list[dict], dict[int, dict]]:
    """The news notices of the dedupe window and the stories they name."""
    since = (t - notices.DEDUPE * 2).isoformat(timespec="seconds")  # a margin over text order; recent() re-checks
    queued = db.notices_since("news:", since)
    sids = {sr[0] for n in queued if (sr := notices.rev_key(n["key"]))}
    return queued, {sid: s for sid in sids if (s := db.story(sid))}


def notify_written(written: list, index: players.Index) -> dict:
    """Queue the pushes for the written revisions (and any still waiting from a run that could not read owners or
    the player index, whose names the pushes carry).

    A story's own earlier notices never hold back its newer revision: one already sent makes it a material update,
    and one still waiting (quiet hours) is marked `superseded` whatever the newer revision produces: a revision that
    no longer pushes means the news no longer warrants one, so its outdated predecessor must not go out either."""
    pending = _queue(written)
    if not pending:
        return {"stories": 0, "queued": 0, "superseded": 0}
    who, me = owners()
    muted = {int(p) for p in db.get_state("news_mutes", []) or []}
    pushed = db.notice_keys("news:")
    waiting = [n["key"] for n in db.unsent_notices() if n["key"].startswith("news:")]
    at, t = notify.now(), datetime.now().astimezone()
    queued, stories = _queued(t)
    sources = {s["id"]: s for s in db.news_sources(active_only=False)}
    added: set[tuple[int, str]] = set()  # rows pushed by this run
    out, superseded = [], []
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
                added |= set(notices.rows(s, who, me, muted, skip))
        if n:
            out.append(n)
        superseded += [k for k in stale if not n or k != n["key"]]
    db.mark_notices(superseded, "superseded", at)
    n_queued = db.add_notices(out)
    db.put_state(PENDING, [])
    return {"stories": len(pending), "queued": n_queued, "superseded": len(superseded)}


def _health() -> int:
    return db.add_notices(health.notices(db.news_sources(), datetime.now().astimezone(), notify.now()))


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
        # reported, never raised: SystemExit is config's "no LEAGUE_ID", which would end the API's run thread
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

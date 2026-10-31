"""The news bench: a grouping or prompt change tried in seconds on frozen news, not an hour on the live DB.

    uv run python -m ffl.newsdesk bench freeze [--from DATE --to DATE]   # the slice: collected items + sources + index
    uv run python -m ffl.newsdesk bench [--player NAME|ID ...] [--write] [--refresh]

`freeze` copies the collected items (untagged) and the source registry out of the database FFL_DB_URL names,
read-only, into the Postgres database `ffl_bench` on the same server, with the player index beside them in its
`bench_index` table, so a later index change never moves a result. A run
makes `ffl_bench_run` from that (CREATE DATABASE ... TEMPLATE) and replays it as production would have seen it:
each item released at its published time
+ DELAY, `tag.run` at every TICK (and `write.run` after it with --write). Jev's and Haiku's answers are kept in
`jev-cache.sqlite` keyed by a hash of the question, so a rerun costs nothing and a changed question pays only for
its own calls (--refresh asks again). The replay is then checked against bench_cases.CASES and diffed against the
last run of the same scope (the cache's `last_runs` table, in the stdlib SQLite `jev-cache.sqlite`). Nothing but
those two bench databases and that file is written; production never reads any of it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa

from .. import claude_cli, db
from ..adp import norm_name
from ..config import DATA
from . import bench_cases, jev, tag, write
from .players import Index

BENCH = DATA / "bench"
FROZEN_DB = "ffl_bench"  # the slice, and its player index (bench_index): a database on FFL_DB_URL's server
RUN_DB = "ffl_bench_run"
CACHE = BENCH / "jev-cache.sqlite"  # saved answers, and each scope's last report (last_runs)
DELAY = timedelta(minutes=15)  # collected and queued: an item reaches tagging this long after it is published
TICK = timedelta(minutes=10)  # launchd's pipeline interval
HOLD = "1999-01-01T00:00:00+00:00"  # tagged_at of an item not yet released: untagged_items passes it over
TOP = 5


# --- saved answers -----------------------------------------------------------------------------------------------

class Cache:
    """Jev's and Haiku's answers on disk, keyed by a hash of what was asked. A hit returns the saved answer with no
    usage (it cost nothing); a miss asks for real and saves. `stats`: hits and misses per model this run."""

    def __init__(self, path: Path = CACHE, refresh: bool = False, ask=jev.ask, run_cli=claude_cli.run):
        self.path, self.refresh, self._ask, self._run_cli = path, refresh, ask, run_cli
        self.stats = {"jev": {"hits": 0, "misses": 0}, "haiku": {"hits": 0, "misses": 0}}
        self.missed: list[str] = []  # the first few misses' question keys: why a rerun was not free
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as c, c:
            c.execute("create table if not exists answers (key text primary key, model text not null, "
                      "answer text not null, usage text not null, at text not null)")
            c.execute("create table if not exists last_runs (scope text primary key, doc text not null, "
                      "at text not null)")

    def last_runs(self) -> dict[str, dict]:
        """{scope: the report of the last run of that scope}, what a run is diffed against."""
        with closing(sqlite3.connect(self.path)) as c:
            return {s: json.loads(d) for s, d in c.execute("select scope, doc from last_runs")}

    def put_last(self, scope: str, doc: dict) -> None:
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute("insert or replace into last_runs values (?, ?, ?)",
                      (scope, json.dumps(doc, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))

    @staticmethod
    def _key(*parts) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                              .encode()).hexdigest()

    def _get(self, key: str):
        if self.refresh:
            return None
        with closing(sqlite3.connect(self.path)) as c:
            row = c.execute("select answer from answers where key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _put(self, key: str, model: str, answer: dict, usage: dict) -> None:
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute("insert or replace into answers values (?, ?, ?, ?, ?)",
                      (key, model, json.dumps(answer), json.dumps(usage), datetime.now(timezone.utc).isoformat()))

    def _miss(self, model: str, what: str) -> None:
        self.stats[model]["misses"] += 1
        if len(self.missed) < 5:
            self.missed.append(f"{model}: {what}")

    def ask(self, state, questions: dict) -> jev.Answers:
        """jev.ask, saved."""
        key = self._key(jev.MODEL, state, questions)
        saved = self._get(key)
        if saved is not None:
            self.stats["jev"]["hits"] += 1
            got = jev.Answers(saved)
            got.usage = {}
            return got
        got = self._ask(state, questions)
        self._miss("jev", ",".join(sorted(questions))[:120])
        self._put(key, jev.MODEL, dict(got), getattr(got, "usage", None) or {})
        return got

    def run_cli(self, prompt: str, *, model: str, schema: dict, tools: str, cwd: Path, timeout: int):
        """claude_cli.run, saved."""
        key = self._key(model, prompt, schema)
        saved = self._get(key)
        if saved is not None:
            self.stats["haiku"]["hits"] += 1
            out = claude_cli.Structured(saved)
            out.usage = {"input": 0, "output": 0, "cost_usd": 0.0}
            return out
        out = self._run_cli(prompt, model=model, schema=schema, tools=tools, cwd=cwd, timeout=timeout)
        self._miss("haiku", "stories " + ",".join(p.split(" ", 1)[0] for p in prompt.split("## Story ")[1:]))
        self._put(key, model, dict(out), getattr(out, "usage", None) or {})
        return out


# --- the frozen slice --------------------------------------------------------------------------------------------

def _url_for(name: str) -> str:
    """FFL_DB_URL with its database replaced: a bench database lives on the server the app's own does."""
    return sa.engine.make_url(db.url()).set(database=name).render_as_string(hide_password=False)


def _create(name: str, template: str | None = None) -> str:
    """A fresh database `name` (an old one dropped) on FFL_DB_URL's server, optionally copied from `template`; its URL.
    Never the database FFL_DB_URL itself names, and never a server that is not this machine."""
    u = sa.engine.make_url(db.url())
    if u.host not in (None, "", "localhost", "127.0.0.1", "::1") and not u.host.startswith("/"):
        raise SystemExit(f"bench: {u.host} is not a local server; the bench drops and remakes its databases")
    if name == u.database:
        raise SystemExit(f"bench: {name} is the database FFL_DB_URL names; it would be dropped")
    admin = sa.create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT",
                             poolclass=sa.pool.NullPool)
    try:
        with admin.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            c.exec_driver_sql(f'CREATE DATABASE "{name}"' + (f' TEMPLATE "{template}"' if template else ""))
    finally:
        admin.dispose()
    return _url_for(name)


def _migrate(url: str) -> None:
    """Alembic's head on a bench DB (a frozen one migrates on each run's copy, so a new revision never breaks it)."""
    from alembic import command
    was = os.environ.get("FFL_DB_URL")
    os.environ["FFL_DB_URL"] = url
    cfg = db._alembic_config()
    cfg.config_file_name = None  # env.py then skips alembic.ini's logging: no INFO line per revision on every run
    try:
        command.upgrade(cfg, "head")
    finally:
        if was is None:
            os.environ.pop("FFL_DB_URL")
        else:
            os.environ["FFL_DB_URL"] = was


def _engine(url: str) -> sa.Engine:
    return sa.create_engine(url, poolclass=sa.pool.NullPool, json_serializer=db._json_dumps)


def _at(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def _day(s: str | None, end: bool) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    return d + timedelta(days=1) if end and len(s) <= 10 else d


def save_index(index: Index, url: str | None = None) -> None:
    """The player index into the frozen slice's own DB (table bench_index, one row): it lives and dies with the
    slice it was frozen beside."""
    eng = _engine(url or _url_for(FROZEN_DB))
    try:
        with eng.begin() as c:
            c.execute(sa.text("create table if not exists bench_index (id integer primary key check (id = 1), "
                              "doc text not null)"))
            c.execute(sa.text("insert into bench_index values (1, :d) on conflict (id) do update set doc = :d"),
                      {"d": json.dumps(asdict(index))})
    finally:
        eng.dispose()


def load_index(url: str | None = None) -> Index | None:
    """The index frozen into the database at `url`; None when there is none (no slice, or one frozen before it lived there)."""
    eng = _engine(url or _url_for(FROZEN_DB))
    try:
        with eng.connect() as c:
            row = c.execute(sa.text("select doc from bench_index where id = 1")).first()
    except (sa.exc.OperationalError, sa.exc.ProgrammingError):  # no such database, or no such table
        return None
    finally:
        eng.dispose()
    if row is None:
        return None
    d = json.loads(row[0])
    return Index(by_id={int(k): v for k, v in d["by_id"].items()}, by_club=d["by_club"], by_full=d["by_full"],
                 owned={int(k): v for k, v in d["owned"].items()}, names={int(k): v for k, v in d.get("names", {}).items()})


def freeze(src: str, since: str | None = None, until: str | None = None, index: Index | None = None,
           name: str = FROZEN_DB) -> dict:
    """The collected items published in [since, until] (dates are whole days, UTC) and every source, from the database
    at URL `src` (read-only), into database `name` in untagged form; and the player index. Returns the counts."""
    from . import players
    lo, hi = _day(since, False), _day(until, True)
    url = _create(name)
    _migrate(url)
    src_eng, dst = db._read_only_engine(src), _engine(url)
    try:
        n = {}
        with src_eng.connect() as s, dst.begin() as d:
            for t in (db.news_sources_table, db.news_items_table):
                rows = [dict(r._mapping) for r in s.execute(sa.select(t))]
                if t is db.news_items_table:
                    rows = [{**r, "jev": None, "tagged_at": None, "story_id": None, "player_ids": []} for r in rows
                            if (lo is None or _at(r["published_at"]) >= lo) and (hi is None or _at(r["published_at"]) < hi)]
                if rows:
                    d.execute(t.insert(), rows)
                n[t.name] = len(rows)
            if n["news_items"]:  # ids kept from the source: the identity sequence has to move past them
                d.execute(sa.text("select setval(pg_get_serial_sequence('news_items', 'id'), "
                                  "(select max(id) from news_items))"))
            span = d.execute(sa.text("select min(published_at), max(published_at) from news_items")).first()
    finally:
        src_eng.dispose()
        dst.dispose()
    save_index(index or players.fetch_index(), url)
    return {**n, "from": span[0], "to": span[1]}


# --- the replay --------------------------------------------------------------------------------------------------

def resolve(names: list[str], index: Index) -> set[int]:
    """--player values: an ESPN id, a full name, or a piece of one that fits a single player."""
    out = set()
    for n in names:
        if n.isdigit():
            out.add(int(n))
            continue
        exact = index.by_full.get(norm_name(n)) or []
        part = [p for p, v in index.by_id.items() if norm_name(n) in norm_name(v["name"])]
        hit = exact if len(exact) == 1 else part
        if len(hit) != 1:
            names_ = ", ".join(f"{index.by_id[p]['name']} ({p})" for p in hit[:10])
            raise SystemExit(f"--player {n!r}: {'no player' if not hit else 'several: ' + names_}")
        out.add(hit[0])
    return out


def _ceil(t: datetime) -> datetime:
    t = t.replace(second=0, microsecond=0) + (timedelta(minutes=1) if t.second or t.microsecond else timedelta())
    return t + timedelta(minutes=-t.minute % (TICK.seconds // 60))


def replay(index: Index, cache: Cache, selected: set[int] | None = None, do_write: bool = False) -> dict:
    """A fresh copy of the frozen slice, tagged (and written) in time order. With `selected`, only the items naming
    those players are kept. Returns the run's totals; the DB is left at ffl_bench_run (FFL_DB_URL points there)."""
    db.reset()  # no engine of ours may hold the frozen database open: it is the copy's template
    run_url = _create(RUN_DB, template=FROZEN_DB)
    _migrate(run_url)
    os.environ["FFL_DB_URL"] = run_url
    db.reset()
    with db._write() as c:
        items = [dict(r._mapping) for r in c.execute(sa.text("select * from news_items where withdrawn_at is null"))]
        if selected is not None:
            keep = {i["id"] for i in items if selected & {p["player_id"] for p in tag._players(i, index)}}
            c.execute(sa.text("delete from news_items where id <> all(:keep)"), {"keep": list(keep)})
            items = [i for i in items if i["id"] in keep]
        c.execute(sa.text("update news_items set tagged_at = :h"), {"h": HOLD})
        watched = {Slice([], index).pid(p) for case in bench_cases.CASES for p in case.players}
        planted = bench_cases.plant(items, watched, lambda i: [p["player_id"] for p in tag._players(i, index)])
        if planted is not None:
            c.execute(sa.text("update news_items set text = coalesce(text, '') || :x where id = :id"),
                      {"x": "\n\n" + bench_cases.INJECTED, "id": planted})
    due = sorted((_at(i["published_at"]) + DELAY, i["id"]) for i in items)
    out = {"items": len(items), "ticks": 0, "tagged": 0, "new": 0, "attached": 0, "updated": 0, "reworded": 0,
           "failed": 0, "given_up": 0, "error": None, "written": 0, "write_missing": 0,
           "jev_usage": {"input": 0, "output": 0, "calls": 0}, "haiku_usage": {"input": 0, "output": 0, "calls": 0}}
    if not due:
        return out
    tick, at, left = _ceil(due[0][0]), 0, tag.TAG_MAX_TRIES + 1
    while at < len(due) or left > 0:
        n = at
        while at < len(due) and due[at][0] <= tick:
            at += 1
        with db._write() as c:
            if at > n:
                c.execute(sa.text("update news_items set tagged_at = null where id = any(:ids)"),
                          {"ids": [i for _, i in due[n:at]]})
            waiting = c.execute(sa.text("select count(*) from news_items where tagged_at is null")).scalar()
        if waiting:
            got = tag.run(index, ask=cache.ask, now=tick)
            out["ticks"] += 1
            for k in ("tagged", "new", "attached", "updated", "reworded"):
                out[k] += got[k]
            out["failed"] += len(got["failed"])
            out["given_up"] += len(got["given_up"])
            for k in got["usage"]:
                out["jev_usage"][k] += got["usage"][k]
            if got["error"]:
                out["error"] = got["error"]
                break
            if do_write:
                w = write.run(index, run_cli=cache.run_cli)
                out["written"] += w["written"]
                out["write_missing"] = w["missing"]
                for k in w["usage"]:
                    out["haiku_usage"][k] += w["usage"][k]
                if w["error"]:
                    out["error"] = f"write: {w['error']}"
                    break
        if at >= len(due):
            left = left - 1 if waiting else 0
        tick += TICK
    return out


# --- the cases ---------------------------------------------------------------------------------------------------

class Slice:
    """The replay as the cases read it: every story (with `players` rows and `items`), the frozen index, and
    whether the stories were written."""

    def __init__(self, stories: list[dict], index: Index, write: bool = False):
        self.stories, self.index, self.write = stories, index, write

    def pid(self, who: int | str) -> int | None:
        if isinstance(who, int):
            return who
        ids = self.index.by_full.get(norm_name(who)) or []
        return ids[0] if len(ids) == 1 else None

    def name(self, pid: int | None) -> str:
        return (self.index.by_id.get(pid) or {}).get("name") or str(pid)

    def arrows(self, pid: int | None, effect: str | None = None, kind: str | None = None) -> list[dict]:
        """The stories with his row ▲/▼ (or the given effect), of `kind` if given."""
        return [s for s in self.stories if any(
            r["player_id"] == pid and r["effect"] != "none" and (effect is None or r["effect"] == effect)
            and (kind is None or r["kind"] == kind) for r in s["players"])]

    def lead(self, story: dict) -> int | None:
        return tag._lead(story)

    def _rows(self, item: dict) -> list[dict]:
        if not item.get("jev"):
            return []
        return [r for r in tag.read(item["jev"], [{"player_id": p} for p in item["player_ids"] or []])["players"]
                if r["effect"] != "none"]

    def movers(self, item: dict, effect: str | None = None) -> list[int]:
        """The players the item itself moves (Jev's stored answers through tag.read: a passing mention is ●)."""
        return [r["player_id"] for r in self._rows(item) if effect is None or r["effect"] == effect]

    def surest(self, item: dict) -> int | None:
        rows = self._rows(item)
        return max(rows, key=lambda r: r["prob"])["player_id"] if rows else None

    def threaded(self, story: dict) -> list[dict]:
        """Its items that neither founded it (the first tagged, as tag._lead orders them) nor were Jev's pick for it."""
        items = sorted((i for i in story["items"] if i.get("tagged_at")),
                       key=lambda i: (i["published_at"], i["id"]), reverse=True)
        items.sort(key=lambda i: i["tagged_at"])
        out = []
        for i in items[1:]:
            pick = (i.get("jev") or {}).get("story") or {}
            p = (pick.get("probabilities") or {}).get(pick.get("choice")) or 0
            if not (pick.get("choice") == f"s{story['id']}" and p >= tag.STORY_ATTACH_PROB):
                out.append(i)
        return out


def evaluate(cases: list, stories: list[dict], index: Index, write: bool = False,
             selected: set[int] | None = None) -> list[dict]:
    """[{id, status: PASS|FAIL|SKIP, detail}]: a case watching players, none of them in `selected`, is skipped."""
    s = Slice(stories, index, write)
    out = []
    for case in cases:
        watched = {s.pid(p) for p in case.players}
        if selected is not None and case.players and not watched & selected:
            out.append({"id": case.id, "status": "SKIP", "detail": "none of its players chosen"})
            continue
        ok, detail = case.check(s)
        out.append({"id": case.id, "status": "PASS" if ok else "FAIL", "detail": detail})
    return out


# --- the report --------------------------------------------------------------------------------------------------

def summary(stories: list[dict], index: Index, cases: list) -> dict:
    s = Slice(stories, index)
    watched = {s.name(s.pid(p)): len(s.arrows(s.pid(p))) for c in cases for p in c.players}
    top = sorted(stories, key=lambda st: (-len(st["items"]), st["id"]))[:TOP]
    return {"stories": len(stories), "watched": watched,
            "top": [{"id": st["id"], "items": len(st["items"]), "lead": s.name(s.lead(st)) if s.lead(st) else "-",
                     "headline": st["headline"] or next((i["title"] for i in st["items"]), "")} for st in top]}


def _diff(prev: dict | None, results: list[dict], summ: dict) -> list[str]:
    if not prev:
        return ["no earlier run of this scope"]
    out = []
    was = {c["id"]: c["status"] for c in prev.get("cases") or []}
    for c in results:
        if c["id"] in was and was[c["id"]] != c["status"]:
            out.append(f"{c['id']}: {was[c['id']]} -> {c['status']}")
    if prev.get("stories") != summ["stories"]:
        out.append(f"stories: {prev.get('stories')} -> {summ['stories']}")
    for name, n in summ["watched"].items():
        before = (prev.get("watched") or {}).get(name)
        if before != n:
            out.append(f"{name} ▲/▼ stories: {before} -> {n}")
    return out or ["no change"]


def _scope(selected: set[int] | None, do_write: bool) -> str:
    return ("players=" + ",".join(map(str, sorted(selected))) if selected else "all") + ("+write" if do_write else "")


def run(selected_names: list[str], do_write: bool, refresh: bool, cases: list = bench_cases.CASES) -> int:
    index = load_index()
    if index is None:
        print("no frozen slice: run `uv run python -m ffl.newsdesk bench freeze` first")
        return 2
    t0 = time.time()
    selected = resolve(selected_names, index) if selected_names else None
    cache = Cache(refresh=refresh)
    tot = replay(index, cache, selected, do_write)
    stories = db.all_stories()
    results = evaluate(cases, stories, index, do_write, selected)
    summ = summary(stories, index, cases)
    for r in results:
        print(f"{r['status']:<4}  {r['id']:<18} {r['detail']}")
    print(f"\n{summ['stories']} stories from {tot['items']} items over {tot['ticks']} ticks; {tot['updated']} rev bumps, "
          f"{tot['reworded']} headline refreshes, {tot['failed']} failed, {tot['given_up']} given up"
          + (f", {tot['written']} written" if do_write else ""))
    if tot["error"]:
        print(f"stopped: {tot['error']}")
    for t in summ["top"]:
        print(f"  #{t['id']:<5} {t['items']:>3} items  {t['lead']:<22} {(t['headline'] or '')[:70]}")
    j, h = cache.stats["jev"], cache.stats["haiku"]
    print(f"\njev: {j['hits']} hits, {j['misses']} misses, {tot['jev_usage']['input']} input tokens spent "
          f"(${tot['jev_usage']['input'] * 0.042 / 1e6:.4f})"
          + (f"; haiku: {h['hits']} hits, {h['misses']} misses, {tot['haiku_usage']['input']} input / "
             f"{tot['haiku_usage']['output']} output tokens" if do_write else ""))
    if cache.missed and (j["hits"] or h["hits"]):
        print("  first misses: " + " | ".join(cache.missed))
    scope = _scope(selected, do_write)
    print(f"\nsince the last {scope} run: " + "; ".join(_diff(cache.last_runs().get(scope), results, summ)))
    cache.put_last(scope, {"at": datetime.now(timezone.utc).isoformat(), "cases": results, **summ})
    print(f"{time.time() - t0:.1f}s")
    return 1 if tot["error"] or any(r["status"] == "FAIL" for r in results) else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["freeze"]:
        ap = argparse.ArgumentParser(prog="python -m ffl.newsdesk bench freeze")
        ap.add_argument("--from", dest="since")
        ap.add_argument("--to", dest="until")
        a = ap.parse_args(argv[1:])
        print(json.dumps(freeze(db.url(), a.since, a.until)))
        return 0
    ap = argparse.ArgumentParser(prog="python -m ffl.newsdesk bench")
    ap.add_argument("--player", nargs="+", default=[], metavar="NAME|ID")
    ap.add_argument("--write", action="store_true", help="also write the stories with Haiku")
    ap.add_argument("--refresh", action="store_true", help="ask Jev and Haiku again, ignoring the saved answers")
    a = ap.parse_args(argv)
    return run(a.player, a.write, a.refresh)

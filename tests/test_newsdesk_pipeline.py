from datetime import datetime, timedelta, timezone

import pytest

from ffl import db, notify
from ffl.newsdesk import pipeline
from ffl.newsdesk.players import Index
from ffl.newsdesk.write import run as write_run  # the real one: the stages fixture stubs write.run
from tests.home import HOME_KEY, home_league

INDEX = Index(by_id={1: {"name": "Jalen Hurts", "pos": "QB", "team": "PHI"},
                     3: {"name": "Tank Bigsby", "pos": "RB", "team": "PHI"}})
AT = datetime.now(timezone.utc).isoformat(timespec="seconds")  # news pushes only while new (NEWS_PUSH_MAX_AGE)
ME = 7


@pytest.fixture
def stages(monkeypatch):
    """Every stage stubbed; `written` is what the write stage reports, `calls` the order they ran in."""
    calls, written = [], []

    def stub(name, value=None):
        def fn(*a, **kw):
            calls.append(name)
            return value() if callable(value) else value
        return fn

    monkeypatch.setattr(pipeline.seed, "ensure", stub("seed", 1))
    monkeypatch.setattr(pipeline.players, "fetch_index", stub("index", INDEX))
    monkeypatch.setattr(pipeline.collect, "run_all", stub("collect", {"new": 0}))
    monkeypatch.setattr(pipeline.collect, "recheck", stub("recheck", {"edited": 0}))
    monkeypatch.setattr(pipeline.media, "run_all", stub("media", {"new": 0}))
    monkeypatch.setattr(pipeline.media, "transcribe_pending", stub("transcribe", {"transcribed": 0}))
    monkeypatch.setattr(pipeline.tag, "run", stub("tag", {"tagged": 0}))
    monkeypatch.setattr(pipeline.write, "run", stub("write", lambda: {"written": len(written), "written_ids": list(written)}))
    monkeypatch.setattr(pipeline, "owners", lambda *, ref: ({1: {"team_id": ME}}, ME))
    return calls, written


def make(players, rev=1, headline="Hurts hurt", status="live"):
    """A written story: players are (pid, effect, kind)."""
    sid = db.add_story({"first_item_id": None, "first_at": AT, "updated_at": AT, "kind": "injury"})
    [i] = db.add_news_items([{"source_id": "pft", "ext_id": f"x{sid}", "url": "u", "published_at": AT, "fetched_at": AT,
                              "title": headline, "text": "", "club": "PHI", "hash": f"h{sid}"}])
    db.tag_item(i, [p for p, _, _ in players], None, sid, AT)
    db.put_story_players(sid, [{"player_id": p, "effect": e, "kind": k, "prob": 0.9, "confidence": 0.5}
                               for p, e, k in players])
    for _ in range(rev - 1):
        db.set_story_status(sid, status, AT, "PFT")
    db.put_story_words(sid, rev, headline, "Overview.", {p: ("A reason.", False) for p, _, _ in players})
    return sid


def keys():
    return [n["key"] for n in db.unsent_notices(ref=home_league())]


def flushed():
    """The queue sent, as the flush marks it."""
    db.mark_notices(keys(), "sent", notify.now(), ref=home_league())


def test_pipeline_stage_failure_reported_rest_run(stages, monkeypatch):
    calls, _ = stages

    def boom(*a, **kw):
        raise RuntimeError("feeds down")

    monkeypatch.setattr(pipeline.collect, "run_all", boom)
    out = pipeline.run()
    assert out["collect"] == {"error": "RuntimeError: feeds down"}
    assert calls == ["seed", "index", "recheck", "media", "tag", "write", "transcribe"]  # transcribe last
    assert out["tag"] == {"tagged": 0} and out["notices"]["leagues"][HOME_KEY] == {"stories": 0, "queued": 0, "superseded": 0, "watch": 0} and out["health"] == 0
    assert db.get_state("news_last_run")["stages"]["collect"] == {"error": "RuntimeError: feeds down"}


def test_no_index_skips_the_stages_that_need_it(stages, monkeypatch):
    calls, _ = stages

    def exits():
        raise SystemExit("no LEAGUE_ID in .env")

    monkeypatch.setattr(pipeline.players, "fetch_index", exits)
    out = pipeline.run()
    assert out["index"] == {"error": "SystemExit: no LEAGUE_ID in .env"}
    assert out["collect"] == out["tag"] == out["write"] == {"skipped": "no player index"}
    assert "recheck" in calls  # recheck reads the DB, not the index


def test_written_story_pushes_once(stages):
    _, written = stages
    sid = make([(1, "down", "injury")])
    written.append((sid, 1))
    out = pipeline.run()
    assert out["notices"]["leagues"][HOME_KEY] == {"stories": 1, "queued": 1, "superseded": 0, "watch": 0}
    [n] = db.unsent_notices(ref=home_league())
    assert (n["key"], n["title"], n["body"], n["url"]) == (f"news:{sid}:1", "Hurts hurt", "▼ Jalen Hurts: A reason.",
                                                          f"/news?story={sid}")
    written.clear()
    assert pipeline.run()["notices"]["leagues"][HOME_KEY] == {"stories": 0, "queued": 0, "superseded": 0, "watch": 0}


def test_dedupe_one_push_per_player_kind_per_day(stages):
    _, written = stages
    a = make([(1, "down", "injury")])
    written[:] = [(a, 1)]
    pipeline.run()
    flushed()
    b = make([(1, "down", "injury")], headline="Hurts still hurt")
    c = make([(1, "down", "trade")], headline="Hurts traded")
    written[:] = [(b, 1), (c, 1)]
    pipeline.run()
    assert keys() == [f"news:{c}:1"]  # same player, same kind inside a day: no second push; another kind pushes


def test_dedupe_counts_a_push_still_queued(stages):
    _, written = stages
    a, b = make([(1, "down", "injury")]), make([(1, "down", "injury")])
    written[:] = [(a, 1), (b, 1)]  # one run, or a held queue in quiet hours: the second never joins the first
    pipeline.run()
    assert keys() == [f"news:{a}:1"]


def test_dedupe_lapses_after_a_day(stages):
    _, written = stages
    a = make([(1, "down", "injury")])
    written[:] = [(a, 1)]
    pipeline.run()
    old = (datetime.now().astimezone() - timedelta(hours=25)).isoformat(timespec="seconds")
    db.mark_notices(keys(), "sent", old, ref=home_league())
    with db._write() as conn:
        conn.execute(db.notices.update().values(created_at=old))
    b = make([(1, "down", "injury")])
    written[:] = [(b, 1)]
    pipeline.run()
    assert keys() == [f"news:{b}:1"]


def test_material_update_pushes_again(stages):
    _, written = stages
    sid = make([(1, "down", "injury")])
    written[:] = [(sid, 1)]
    pipeline.run()
    flushed()
    with db._write() as conn:  # tag.weigh: a material fact bumps the rev
        db._bump_story(conn, sid, db.SEASON)
    db.put_story_words(sid, 2, "Hurts out longer", "Overview.", {1: ("Out four weeks now.", False)})
    written[:] = [(sid, 2)]
    pipeline.run()
    assert keys() == [f"news:{sid}:2"]
    # a rev-2 story whose rev 1 never pushed is not an update: the day's dedupe holds
    other = make([(1, "down", "injury")])
    with db._write() as conn:
        db._bump_story(conn, other, db.SEASON)
    db.put_story_words(other, 2, "Hurts again", "Overview.", {1: ("Again.", False)})
    written[:] = [(other, 2)]
    pipeline.run()
    assert keys() == [f"news:{sid}:2"]


def outcomes():
    return {n["key"]: n["outcome"] for n in db.recent_notices(50, ref=home_league())}


def test_newer_rev_supersedes_unsent(stages):
    _, written = stages
    sid = make([(1, "down", "injury")])
    written[:] = [(sid, 1)]
    pipeline.run()  # queued, held by quiet hours
    with db._write() as conn:
        db._bump_story(conn, sid, db.SEASON)
    db.put_story_words(sid, 2, "Hurts out longer", "Overview.", {1: ("Out four weeks now.", False)})
    written[:] = [(sid, 2)]
    assert pipeline.run()["notices"]["leagues"][HOME_KEY] == {"stories": 1, "queued": 1, "superseded": 1, "watch": 0}
    assert keys() == [f"news:{sid}:2"]
    assert outcomes()[f"news:{sid}:1"] == "superseded"


def queued_then(written, rev2, status=None, flag=False, headline="Correction: Hurts out longer"):
    """Rev 1 queued and held (quiet hours); then rev 2 (a status change, or `changed`) written and run."""
    sid = make([(1, "down", "injury")])
    written[:] = [(sid, 1)]
    pipeline.run()
    if status:
        db.set_story_status(sid, status, AT, "ESPN")
    else:
        with db._write() as conn:
            db._bump_story(conn, sid, db.SEASON)
    db.put_story_words(sid, 2, headline, "Overview.", {1: (rev2, flag)})
    written[:] = [(sid, 2)]
    pipeline.run()
    return sid


def test_corrected_while_queued_qualifying(stages):
    _, written = stages
    sid = queued_then(written, "Out four weeks, not two.", status="corrected")
    [n] = db.unsent_notices(ref=home_league())
    assert (n["key"], n["title"]) == (f"news:{sid}:2", "Correction: Hurts out longer")
    assert outcomes()[f"news:{sid}:1"] == "superseded"


def test_corrected_while_queued_not_qualifying(stages):
    _, written = stages
    sid = queued_then(written, "The sources do not say he is hurt.", status="corrected", flag=True)
    assert keys() == [] and outcomes()[f"news:{sid}:1"] == "superseded"


def test_live_rev2_not_qualifying_supersedes_rev1(stages):
    _, written = stages
    sid = queued_then(written, "Only named in a recap.", flag=True, headline="Hurts limited")
    assert keys() == [] and outcomes()[f"news:{sid}:1"] == "superseded"


def test_no_index_leaves_the_written_revisions_waiting(stages, monkeypatch):
    _, written = stages
    sid = make([(1, "down", "injury")])
    db.put_league_state(pipeline.PENDING, [[sid, 1, notify.now()]], ref=home_league())  # written by a run whose notices stage failed

    def exits(*, ref):
        raise SystemExit("no LEAGUE_ID in .env")

    monkeypatch.setattr(pipeline.players, "fetch_index", exits)
    assert pipeline.run()["notices"] == {"skipped": "no player index"}
    assert keys() == [] and db.get_league_state(pipeline.PENDING, ref=home_league())[0][:2] == [sid, 1]
    monkeypatch.setattr(pipeline.players, "fetch_index", lambda: INDEX)
    pipeline.run()
    assert [n["body"] for n in db.unsent_notices(ref=home_league())] == ["▼ Jalen Hurts: A reason."]


def test_retraction_supersedes_an_unsent_original(stages):
    _, written = stages
    sid = make([(1, "down", "injury")])
    written[:] = [(sid, 1)]
    pipeline.run()  # waiting, never sent: the retraction has nothing to push, and the original must not go out
    db.set_story_status(sid, "retracted", AT, "PFT")
    db.put_story_words(sid, 2, "Report withdrawn: Hurts hurt", "Withdrawn.", {1: ("Withdrawn.", False)})
    written[:] = [(sid, 2)]
    pipeline.run()
    assert keys() == [] and outcomes()[f"news:{sid}:1"] == "superseded"


def test_corrected_story_never_pushed_runs_as_news(stages):
    """"Contrary to reports, X is out": the first report moved nobody, so nothing went out; the correction does."""
    _, written = stages
    sid = make([(1, "none", "injury")])
    written[:] = [(sid, 1)]
    pipeline.run()
    assert keys() == []
    db.set_story_status(sid, "corrected", AT, "PFT")
    db.put_story_players(sid, [{"player_id": 1, "effect": "down", "kind": "injury", "prob": 0.9, "confidence": 0.5}])
    db.put_story_words(sid, 2, "Correction: Hurts out", "Overview.", {1: ("Out after all.", False)})
    written[:] = [(sid, 2)]
    pipeline.run()
    [n] = db.unsent_notices(ref=home_league())
    assert (n["key"], n["title"], n["body"]) == (f"news:{sid}:2", "Correction: Hurts out", "▼ Jalen Hurts: Out after all.")


def test_old_news_does_not_push(stages):
    _, written = stages
    sid = make([(1, "down", "injury")])
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(timespec="seconds")
    with db._write() as conn:  # the first run's backlog: written now, published seven hours ago
        conn.execute(db.news_items_table.update().values(published_at=old))
    written[:] = [(sid, 1)]
    assert pipeline.run()["notices"]["leagues"][HOME_KEY]["queued"] == 0
    assert keys() == []


def test_retraction_pushes_status_only_after_the_original(stages):
    _, written = stages
    db.put_news_sources([{"id": "PFT", "kind": "pft", "club": None, "name": "Pro Football Talk", "url": "u", "active": True}])
    sid = make([(1, "down", "injury")])
    written[:] = [(sid, 1)]
    pipeline.run()
    flushed()
    db.set_story_status(sid, "retracted", AT, "PFT")
    db.put_story_words(sid, 2, "Report withdrawn: Hurts hurt", "Withdrawn.", {1: ("Withdrawn.", False)})
    written[:] = [(sid, 2)]
    pipeline.run()
    [n] = db.unsent_notices(ref=home_league())
    assert (n["key"], n["title"], n["body"]) == (f"news:{sid}:retracted", "Retracted: Report withdrawn: Hurts hurt",
                                                 "Pro Football Talk")  # the source's name, not its id
    never = make([(1, "down", "trade")], rev=2, status="retracted")
    written[:] = [(never, 2)]
    pipeline.run()
    assert len(db.unsent_notices(ref=home_league())) == 1


def test_owners_failing_queues_nothing_and_keeps_the_stories(stages, monkeypatch):
    _, written = stages
    sid = make([(1, "down", "injury")])
    written[:] = [(sid, 1)]

    def exits(*, ref):
        raise SystemExit("no LEAGUE_ID in .env")

    monkeypatch.setattr(pipeline, "owners", exits)
    assert pipeline.run()["notices"]["leagues"][HOME_KEY] == {"error": "SystemExit: no LEAGUE_ID in .env"}
    assert keys() == []
    monkeypatch.setattr(pipeline, "owners", lambda *, ref: ({1: {"team_id": ME}}, ME))
    written.clear()
    pipeline.run()  # ESPN is back: the story written during the outage pushes now
    assert keys() == [f"news:{sid}:1"]


def test_muted_player_never_pushes(stages):
    _, written = stages
    db.put_state("news_mutes", [1], user_id=0)
    sid = make([(1, "down", "injury"), (3, "up", "role")])
    written[:] = [(sid, 1)]
    pipeline.run()
    assert [n["body"] for n in db.unsent_notices(ref=home_league())] == ["▲ Tank Bigsby: A reason."]


def test_stale_headline_rewrites_without_push(stages):
    """The tagger found the headline stale (no material change): Haiku writes it again at the same rev, and the pipeline gets
    no revision to push, so nothing is queued and a push still waiting is not superseded."""
    _, written = stages
    sent, waiting = make([(1, "down", "injury")]), make([(3, "up", "role")], headline="Bigsby up")
    written[:] = [(sent, 1)]
    pipeline.run()
    flushed()
    written[:] = [(waiting, 1)]
    pipeline.run()  # its push waits (quiet hours)
    assert keys() == [f"news:{waiting}:1"]
    for sid in (sent, waiting):
        db.ask_reword(sid)

    def haiku(prompt, **kw):
        return {"stories": [{"id": sid, "headline": f"New headline {sid}", "overview": "Now.",
                             "players": [{"player_id": p, "reason": "Still.", "disagree": False} for p in (1, 3)]}
                            for sid in (sent, waiting)]}

    out = write_run(INDEX, run_cli=haiku)
    assert (out["written"], out["refreshed"], out["written_ids"]) == (2, 2, [])
    assert db.story(sent)["headline"] == f"New headline {sent}" and db.story(sent)["rev"] == 1
    written[:] = out["written_ids"]
    assert pipeline.run()["notices"]["leagues"][HOME_KEY] == {"stories": 0, "queued": 0, "superseded": 0, "watch": 0}
    assert keys() == [f"news:{waiting}:1"] and outcomes()[f"news:{sent}:1"] == "sent"


def _up(pid, prob, owned=False, watched=True):
    sid = make([(pid, "up", "role")])
    db.put_story_players(sid, [{"player_id": pid, "effect": "up", "kind": "role", "prob": prob, "confidence": 0.5}])
    if watched:
        db.put_watch(pid, True, ref=home_league())
    return sid


def test_watched_up_story_signals_and_pushes(stages):
    _, written = stages
    sid = _up(3, 0.6)
    written.append((sid, 1))
    out = pipeline.run()
    assert out["notices"]["leagues"][HOME_KEY]["watch"] == 1
    [s] = _signals()
    assert s["key"] == f"news:3:{sid}:1"
    [n] = db.unsent_notices(ref=home_league())
    assert n["key"] == f"watch:3:{notify.now()[:10]}" and n["kind"] == "watch" and n["title"] == "Tank Bigsby"
    written.clear()
    written.append((sid, 1))
    assert pipeline.run()["notices"]["leagues"][HOME_KEY]["watch"] == 0  # the same revision again: no second push
    assert len(db.unsent_notices(ref=home_league())) == 1


def _signals():
    return db.watch_signals("1970-01-01", ref=home_league())


def test_watched_rostered_elsewhere_no_push(stages, monkeypatch):
    _, written = stages
    monkeypatch.setattr(pipeline, "owners", lambda *, ref: ({3: {"team_id": 2}}, ME))
    sid = _up(3, 0.6)
    written.append((sid, 1))
    assert pipeline.run()["notices"]["leagues"][HOME_KEY]["watch"] == 1
    assert len(_signals()) == 1 and db.unsent_notices(ref=home_league()) == []


def test_watched_already_in_news_push_no_second(stages):
    _, written = stages
    sid = _up(3, 0.9)
    written.append((sid, 1))
    pipeline.run()
    assert keys() == [f"news:{sid}:1"] and len(_signals()) == 1


def test_unwatched_up_story_no_signal(stages):
    _, written = stages
    sid = _up(3, 0.6, watched=False)
    written.append((sid, 1))
    pipeline.run()
    assert _signals() == [] and db.unsent_notices(ref=home_league()) == []


def test_watch_db_errors_leave_the_news_push(stages, monkeypatch):
    _, written = stages
    sid = _up(3, 0.9)
    written.append((sid, 1))

    def boom(*a, **kw):
        raise db.DBError("locked")

    monkeypatch.setattr(db, "watched_ids", boom)
    monkeypatch.setattr(db, "add_watch_signals", boom)
    assert pipeline.run()["notices"]["leagues"][HOME_KEY]["queued"] == 1
    assert keys() == [f"news:{sid}:1"]


def test_watch_signal_wants_news_still_new(stages):
    _, written = stages
    sid = _up(3, 0.6)
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(timespec="seconds")
    with db._write() as c:
        c.execute(db.news_items_table.update().values(published_at=old))
    written.append((sid, 1))
    assert pipeline.run()["notices"]["leagues"][HOME_KEY]["watch"] == 0
    assert _signals() == [] and db.unsent_notices(ref=home_league()) == []


def test_every_league_gets_its_own_news_pushes(stages, monkeypatch):
    from ffl import leagues
    other = leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, None, None), user_id=0)
    _, written = stages
    sid = make([(1, "down", "injury")])
    written[:] = [(sid, 1)]
    owners = {HOME_KEY: ({1: {"team_id": ME}}, ME), "espn:999": ({1: {"team_id": 3}}, 3)}  # mine in both
    monkeypatch.setattr(pipeline, "owners", lambda *, ref: owners[ref.key])
    out = pipeline.run()["notices"]
    assert out["leagues"][HOME_KEY]["queued"] == 1 and out["leagues"]["espn:999"]["queued"] == 1
    assert keys() == [f"news:{sid}:1"] and [n["key"] for n in db.unsent_notices(ref=other)] == [f"news:{sid}:1"]


def test_another_leagues_owners_failing_never_holds_back_home(stages, monkeypatch):
    from ffl import leagues
    other = leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, None, None), user_id=0)
    _, written = stages
    sid = make([(1, "down", "injury")])
    written[:] = [(sid, 1)]

    def owners(*, ref):
        if ref.key != HOME_KEY:
            raise RuntimeError("espn down")
        return {1: {"team_id": ME}}, ME
    monkeypatch.setattr(pipeline, "owners", owners)
    out = pipeline.run()["notices"]
    assert out["leagues"]["espn:999"] == {"error": "RuntimeError: espn down"} and keys() == [f"news:{sid}:1"]
    assert db.get_league_state("news_to_notify", [], ref=other)[0][:2] == [sid, 1], "its revision waits for the next run"


def test_each_member_hears_of_their_own_team_and_mutes(stages, monkeypatch):
    """ffl-soc.6.2.3: one owners read per league; each member's push names their own team's players, on their own
    mutes (A, team 7, holds Hurts; B, team 3, holds Bigsby; C co-manages A's team and mutes Hurts)."""
    from tests import two_users
    two = two_users.make()
    _, written = stages
    reads = []
    monkeypatch.setattr(pipeline, "owners", lambda *, ref: reads.append(ref.key) or
                        ({1: {"team_id": 7}, 3: {"team_id": 3}}, -1))
    db.put_state("news_mutes", [1], user_id=two.c.user_id)
    sid = make([(1, "down", "injury"), (3, "down", "injury")])
    written[:] = [(sid, 1)]
    pipeline.run()
    assert reads.count(HOME_KEY) == 1, "one owners read for every member"
    body = lambda ref: [n["body"] for n in db.unsent_notices(ref=ref)]
    assert body(two.a) == ["▼ Jalen Hurts: A reason."] and body(two.b) == ["▼ Tank Bigsby: A reason."]
    assert body(two.c) == []

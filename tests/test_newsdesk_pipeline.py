from datetime import datetime, timedelta

import pytest

from ffl import db, notify
from ffl.newsdesk import pipeline
from ffl.newsdesk.players import Index

INDEX = Index(by_id={1: {"name": "Jalen Hurts", "pos": "QB", "team": "PHI"},
                     3: {"name": "Tank Bigsby", "pos": "RB", "team": "PHI"}})
AT = "2026-10-19T12:00:00+00:00"
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
    monkeypatch.setattr(pipeline.tag, "run", stub("tag", {"tagged": 0}))
    monkeypatch.setattr(pipeline.write, "run", stub("write", lambda: {"written": len(written), "written_ids": list(written)}))
    monkeypatch.setattr(pipeline, "owners", lambda: ({1: {"team_id": ME}}, ME))
    return calls, written


def make(players, rev=1, headline="Hurts hurt", status="live"):
    """A written story: players are (pid, effect, kind)."""
    sid = db.add_story({"first_item_id": None, "first_at": AT, "updated_at": AT, "kind": "injury"})
    db.put_story_players(sid, [{"player_id": p, "effect": e, "kind": k, "prob": 0.9, "confidence": 0.5}
                               for p, e, k in players])
    for _ in range(rev - 1):
        db.set_story_status(sid, status, AT, "PFT")
    db.put_story_words(sid, rev, headline, "Overview.", {p: ("A reason.", False) for p, _, _ in players})
    return sid


def keys():
    return [n["key"] for n in db.unsent_notices()]


def flushed():
    """The queue sent, as the flush marks it."""
    db.mark_notices(keys(), "sent", notify.now())


def test_pipeline_stage_failure_reported_rest_run(stages, monkeypatch):
    calls, _ = stages

    def boom(*a, **kw):
        raise RuntimeError("feeds down")

    monkeypatch.setattr(pipeline.collect, "run_all", boom)
    out = pipeline.run()
    assert out["collect"] == {"error": "RuntimeError: feeds down"}
    assert calls == ["seed", "index", "recheck", "media", "tag", "write"]
    assert out["tag"] == {"tagged": 0} and out["notices"] == {"stories": 0, "queued": 0} and out["health"] == 0
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
    assert out["notices"] == {"stories": 1, "queued": 1}
    [n] = db.unsent_notices()
    assert (n["key"], n["title"], n["body"], n["url"]) == (f"news:{sid}:1", "Hurts hurt", "▼ Jalen Hurts: A reason.",
                                                          f"/news?story={sid}")
    written.clear()
    assert pipeline.run()["notices"] == {"stories": 0, "queued": 0}


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
    db.mark_notices(keys(), "sent", old)
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
    with db._write() as conn:  # tag.changed: a material fact bumps the rev
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


def test_retraction_pushes_status_only_after_the_original(stages):
    _, written = stages
    sid = make([(1, "down", "injury")])
    written[:] = [(sid, 1)]
    pipeline.run()
    flushed()
    db.set_story_status(sid, "retracted", AT, "PFT")
    db.put_story_words(sid, 2, "Report withdrawn: Hurts hurt", "Withdrawn.", {1: ("Withdrawn.", False)})
    written[:] = [(sid, 2)]
    pipeline.run()
    [n] = db.unsent_notices()
    assert (n["key"], n["title"], n["body"]) == (f"news:{sid}:retracted", "Retracted: Report withdrawn: Hurts hurt", "PFT")
    never = make([(1, "down", "trade")], rev=2, status="retracted")
    written[:] = [(never, 2)]
    pipeline.run()
    assert len(db.unsent_notices()) == 1


def test_owners_failing_queues_nothing_and_keeps_the_stories(stages, monkeypatch):
    _, written = stages
    sid = make([(1, "down", "injury")])
    written[:] = [(sid, 1)]

    def exits():
        raise SystemExit("no LEAGUE_ID in .env")

    monkeypatch.setattr(pipeline, "owners", exits)
    assert pipeline.run()["notices"] == {"error": "SystemExit: no LEAGUE_ID in .env"}
    assert keys() == []
    monkeypatch.setattr(pipeline, "owners", lambda: ({1: {"team_id": ME}}, ME))
    written.clear()
    pipeline.run()  # ESPN is back: the story written during the outage pushes now
    assert keys() == [f"news:{sid}:1"]


def test_muted_player_never_pushes(stages):
    _, written = stages
    db.put_state("news_mutes", [1])
    sid = make([(1, "down", "injury"), (3, "up", "role")])
    written[:] = [(sid, 1)]
    pipeline.run()
    assert [n["body"] for n in db.unsent_notices()] == ["▲ Tank Bigsby: A reason."]

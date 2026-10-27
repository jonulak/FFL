"""box_scores reads and writes the box_scores table (ffl-7sq)."""
import time
from types import SimpleNamespace

import pytest

from ffl import db, espn_season
from tests.home import home_league


@pytest.fixture
def env(monkeypatch):
    calls = {"fetch": 0, "reconcile": 0}
    state = {"doc": {"tag": "espn"}, "raise": None}

    def fetch(lg, week):
        calls["fetch"] += 1
        if state["raise"]:
            raise state["raise"]
        return state["doc"]

    monkeypatch.setattr(espn_season, "_fetch_box_doc", fetch)
    monkeypatch.setattr(espn_season, "_parse_box_doc", lambda lg, doc: [doc["tag"]])
    monkeypatch.setattr(espn_season, "reconcile_log", lambda *a, **k: calls.__setitem__("reconcile", calls["reconcile"] + 1))
    monkeypatch.setattr(espn_season, "owners_in", lambda boxes: {})
    lg = SimpleNamespace(current_week=6, espn_request=object(), teams=[])
    return SimpleNamespace(calls=calls, state=state, lg=lg)


def store(week, tag, age=0.0, final=False):
    doc = {"tag": tag}
    db.put_box_doc(week, doc, espn_season.box_hash(doc), time.time() - age, final=final, ref=home_league())


def test_finished_week_reads_the_final_row(env):
    store(4, "stored", final=True)
    assert espn_season.box_scores(env.lg, 4, ref=home_league()) == ["stored"]
    assert env.calls["fetch"] == 0


def test_finished_week_without_row_fetches_and_stores_final(env):
    assert espn_season.box_scores(env.lg, 4, ref=home_league()) == ["espn"]
    assert env.calls["fetch"] == 1
    row = db.box_doc(4, ref=home_league())
    assert row["hash"] == espn_season.box_hash({"tag": "espn"}) and row["final"] is True


def test_current_week_read_is_stored_not_final(env):
    espn_season.box_scores(env.lg, 6, ref=home_league())
    assert db.box_doc(6, ref=home_league())["final"] is False


def test_last_current_week_read_is_not_the_finished_week(env):
    """The week-6 row read while 6 was current (maybe mid-Monday-night) is not week 6's result once ESPN rolls."""
    env.state["doc"] = {"tag": "mid-game"}
    lg6 = env.lg
    assert espn_season.box_scores(lg6, 6, ref=home_league()) == ["mid-game"]
    env.state["doc"] = {"tag": "final"}
    lg7 = SimpleNamespace(current_week=7, espn_request=object(), teams=[])
    assert espn_season.box_scores(lg7, 6, ref=home_league()) == ["final"]
    assert env.calls["fetch"] == 2
    row = db.box_doc(6, ref=home_league())
    assert row["doc"] == {"tag": "final"} and row["final"] is True


def test_finished_week_non_final_row_serves_on_outage_but_is_not_kept(env):
    store(4, "mid-game", age=10)
    env.state["raise"] = RuntimeError("espn down")
    assert espn_season.box_scores(env.lg, 4, ref=home_league()) == ["mid-game"]
    assert espn_season._BOXES[("espn:100000001", 4)][0] != float("inf")   # served for BOXES_TTL_S, never for the process
    assert db.box_doc(4, ref=home_league())["final"] is False


def test_current_week_fresh_row_is_a_hit(env):
    store(6, "stored", age=10)
    assert espn_season.box_scores(env.lg, 6, ref=home_league()) == ["stored"]
    assert env.calls["fetch"] == 0 and env.calls["reconcile"] == 0


def test_current_week_old_row_refetches_and_reconciles(env):
    store(6, "stored", age=120)
    assert espn_season.box_scores(env.lg, 6, ref=home_league()) == ["espn"]
    assert env.calls["fetch"] == 1 and env.calls["reconcile"] == 1


def test_current_week_outage_serves_stale_row(env):
    env.state["raise"] = RuntimeError("espn down")
    store(6, "stale", age=3600)
    assert espn_season.box_scores(env.lg, 6, ref=home_league()) == ["stale"]


def test_current_week_outage_without_row_raises(env):
    env.state["raise"] = RuntimeError("espn down")
    with pytest.raises(RuntimeError):
        espn_season.box_scores(env.lg, 6, ref=home_league())


def test_unchanged_reread_moves_fetched_at_only(env):
    store(6, "espn", age=120)
    before = db.box_doc(6, ref=home_league())
    espn_season.box_scores(env.lg, 6, ref=home_league())
    after = db.box_doc(6, ref=home_league())
    assert after["hash"] == before["hash"] and after["doc"] == before["doc"]
    assert after["fetched_at"] > before["fetched_at"]


def test_forget_current_boxes_expires_the_stored_row(env):
    store(6, "stored", age=1)
    espn_season.forget_current_boxes(ref=home_league())
    assert espn_season.box_scores(env.lg, 6, ref=home_league()) == ["espn"]
    assert env.calls["fetch"] == 1


def test_unparseable_stored_doc_falls_through_to_espn(env, monkeypatch):
    store(4, "bad")

    def parse(lg, doc):
        if doc["tag"] == "bad":
            raise KeyError("matchups")
        return [doc["tag"]]

    monkeypatch.setattr(espn_season, "_parse_box_doc", parse)
    assert espn_season.box_scores(env.lg, 4, ref=home_league()) == ["espn"]
    assert env.calls["fetch"] == 1


def test_store_failure_still_returns_boxes(env, monkeypatch):
    def boom(*a, **k):
        raise db.DBError("locked")

    monkeypatch.setattr(db, "put_box_doc", boom)
    assert espn_season.box_scores(env.lg, 4, ref=home_league()) == ["espn"]


def test_move_during_fetch_is_not_stored_or_fresh(env, monkeypatch):
    def fetch(lg, week):
        env.calls["fetch"] += 1
        espn_season.forget_current_boxes(ref=home_league())  # a lineup move lands while the read is in flight
        return {"tag": "pre-move"}

    monkeypatch.setattr(espn_season, "_fetch_box_doc", fetch)
    assert espn_season.box_scores(env.lg, 6, ref=home_league()) == ["pre-move"]
    assert db.box_doc(6, ref=home_league()) is None and env.calls["reconcile"] == 0
    assert espn_season.box_scores(env.lg, 6, ref=home_league()) == ["pre-move"]
    assert env.calls["fetch"] == 2


def test_a_fresh_doc_that_does_not_parse_is_never_stored(env, monkeypatch):
    """ffl-soc.4.15: a read ESPN answered without rosters raises, and leaves no row for the next read to trust."""
    def parse(lg, doc):
        raise KeyError("rosterForCurrentScoringPeriod")

    monkeypatch.setattr(espn_season, "_parse_box_doc", parse)
    with pytest.raises(KeyError):
        espn_season.box_scores(env.lg, 7, ref=home_league())
    assert db.box_doc(7, ref=home_league()) is None

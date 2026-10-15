"""box_scores reads and writes the box_scores table (ffl-7sq)."""
import time
from types import SimpleNamespace

import pytest

from ffl import db, espn_season


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


def store(week, tag, age=0.0):
    doc = {"tag": tag}
    db.put_box_doc(week, doc, espn_season.box_hash(doc), time.time() - age)


def test_finished_week_reads_the_row(env):
    store(4, "stored")
    assert espn_season.box_scores(env.lg, 4) == ["stored"]
    assert env.calls["fetch"] == 0


def test_finished_week_without_row_fetches_and_stores(env):
    assert espn_season.box_scores(env.lg, 4) == ["espn"]
    assert env.calls["fetch"] == 1
    assert db.box_doc(4)["hash"] == espn_season.box_hash({"tag": "espn"})


def test_current_week_fresh_row_is_a_hit(env):
    store(6, "stored", age=10)
    assert espn_season.box_scores(env.lg, 6) == ["stored"]
    assert env.calls["fetch"] == 0 and env.calls["reconcile"] == 0


def test_current_week_old_row_refetches_and_reconciles(env):
    store(6, "stored", age=120)
    assert espn_season.box_scores(env.lg, 6) == ["espn"]
    assert env.calls["fetch"] == 1 and env.calls["reconcile"] == 1


def test_current_week_outage_serves_stale_row(env):
    env.state["raise"] = RuntimeError("espn down")
    store(6, "stale", age=3600)
    assert espn_season.box_scores(env.lg, 6) == ["stale"]


def test_current_week_outage_without_row_raises(env):
    env.state["raise"] = RuntimeError("espn down")
    with pytest.raises(RuntimeError):
        espn_season.box_scores(env.lg, 6)


def test_unchanged_reread_moves_fetched_at_only(env):
    store(6, "espn", age=120)
    before = db.box_doc(6)
    espn_season.box_scores(env.lg, 6)
    after = db.box_doc(6)
    assert after["hash"] == before["hash"] and after["doc"] == before["doc"]
    assert after["fetched_at"] > before["fetched_at"]


def test_forget_current_boxes_expires_the_stored_row(env):
    store(6, "stored", age=1)
    espn_season.forget_current_boxes()
    assert espn_season.box_scores(env.lg, 6) == ["espn"]
    assert env.calls["fetch"] == 1


def test_unparseable_stored_doc_falls_through_to_espn(env, monkeypatch):
    store(4, "bad")

    def parse(lg, doc):
        if doc["tag"] == "bad":
            raise KeyError("matchups")
        return [doc["tag"]]

    monkeypatch.setattr(espn_season, "_parse_box_doc", parse)
    assert espn_season.box_scores(env.lg, 4) == ["espn"]
    assert env.calls["fetch"] == 1


def test_store_failure_still_returns_boxes(env, monkeypatch):
    def boom(*a, **k):
        raise db.DBError("locked")

    monkeypatch.setattr(db, "put_box_doc", boom)
    assert espn_season.box_scores(env.lg, 4) == ["espn"]

"""correct_boxes: finished weeks re-read until they settle, ESPN's stat corrections logged (ffl-7sq)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ffl import db, espn_season
from ffl import season as S

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)  # a Thursday
KICK = datetime(2026, 10, 12, 0, 15, tzinfo=timezone.utc)  # Sunday-night-ET kickoff of week 6; settles that Friday


def doc_of(*players):
    """players: (pid, team_id, points, slot)."""
    return {"players": [list(p) for p in players]}


def parse(lg, doc):
    def bp(p):
        pid, _, pts, slot = p
        return SimpleNamespace(playerId=pid, points=pts, slot_position=slot, name=f"P{pid}", position="WR", proTeam="DAL")
    by_team = {}
    for p in doc["players"]:
        by_team.setdefault(p[1], []).append(p)
    teams = sorted(by_team)
    return [SimpleNamespace(home_team=SimpleNamespace(team_id=teams[0]), away_team=SimpleNamespace(team_id=teams[-1]),
                            home_lineup=[bp(p) for p in by_team[teams[0]]],
                            away_lineup=[] if len(teams) == 1 else [bp(p) for p in by_team[teams[-1]]])]


@pytest.fixture
def env(monkeypatch):
    s = SimpleNamespace(docs={}, fetches=[], raises={}, kicks={w: [KICK - timedelta(days=7 * (6 - w))] for w in range(1, 7)},
                        gap={}, gap_fetches=[])

    def fetch(lg, week):
        s.fetches.append(week)
        if week in s.raises:
            raise s.raises[week]
        return s.docs[week]

    def gap(w, ids, week):
        s.gap_fetches.append((w, list(ids)))
        return {i: s.gap[(w, i)] for i in ids}

    monkeypatch.setattr(espn_season, "_fetch_box_doc", fetch)
    monkeypatch.setattr(espn_season, "_parse_box_doc", parse)
    monkeypatch.setattr(espn_season, "kickoffs_by_week", lambda lg: s.kicks)
    monkeypatch.setattr(espn_season, "_fetch_gap_rows", gap)
    espn_season._GAP_GAMES.clear()
    espn_season._BOXES.clear()
    s.lg = SimpleNamespace(current_week=7, espn_request=object(), teams=[])
    return s


def store(week, doc, age=7200, settled=False):
    db.put_box_doc(week, doc, espn_season.box_hash(doc), NOW.timestamp() - age, settled=settled)


def only(week):
    """Make `week` the only one to process: the other finished weeks are settled and stored."""
    for w in range(1, 7):
        if w != week:
            store(w, doc_of((1, 1, 1.0, "WR")), settled=True)


def test_backfill_stores_settled_and_logs_nothing(env):
    only(3)
    env.docs[3] = doc_of((11, 1, 9.0, "WR"))
    out = espn_season.correct_boxes(env.lg, NOW)
    assert db.box_doc(3)["settled"] is True and db.box_corrections() == []
    assert out["plays"] == [] and out["changed"] == [] and out["weeks"][3] == {"changed": 0, "settled": True}


def test_starter_correction_logged_and_played(env):
    only(6)
    store(6, doc_of((11, 1, 10.0, "WR"), (12, 2, 3.0, "RB")))
    env.docs[6] = doc_of((11, 1, 12.5, "WR"), (12, 2, 3.0, "RB"))
    out = espn_season.correct_boxes(env.lg, NOW)
    [c] = db.box_corrections(week=6)
    assert (c["week"], c["player_id"], c["team_id"], c["old"], c["new"]) == (6, 11, 1, 10.0, 12.5)
    [p] = out["plays"]
    assert p["match"] == "correction" and p["delta"] == 2.5 and p["total"] == 12.5 and p["player_id"] == 11
    assert p["team_id"] == 1 and p["name"] == "P11" and p["pos"] == "WR" and p["team"] == "DAL"
    assert p["text"] == "Stat correction: 10 → 12.5" and p["play_id"] is None and p["quarter"] is None
    assert p["clock"] is None and p["game_id"] is None and p["week"] == 6
    assert out["changed"] == [6] and out["weeks"][6] == {"changed": 1, "settled": False}
    assert db.box_doc(6)["settled"] is False and db.box_doc(6)["doc"] == env.docs[6]


def test_bench_correction_logged_without_play(env):
    only(6)
    store(6, doc_of((11, 1, 10.0, "BE")))
    env.docs[6] = doc_of((11, 1, 12.5, "BE"))
    out = espn_season.correct_boxes(env.lg, NOW)
    assert len(db.box_corrections(week=6)) == 1 and out["plays"] == [] and out["changed"] == [6]


def test_float_noise_is_no_change(env):
    only(6)
    store(6, doc_of((11, 1, 10.0, "WR")))
    env.docs[6] = doc_of((11, 1, 10.000000001, "WR"))
    out = espn_season.correct_boxes(env.lg, NOW)
    assert db.box_corrections() == [] and out["changed"] == []


def test_player_in_one_doc_only_is_ignored(env):
    only(6)
    store(6, doc_of((11, 1, 10.0, "WR")))
    env.docs[6] = doc_of((11, 1, 10.0, "WR"), (12, 1, 4.0, "WR"))
    assert espn_season.correct_boxes(env.lg, NOW)["changed"] == []


def test_settles_after_settle_at_then_never_fetched(env):
    only(6)
    store(6, doc_of((11, 1, 10.0, "WR")))
    env.docs[6] = doc_of((11, 1, 10.0, "WR"))
    now = S.settle_at(KICK) + timedelta(minutes=1)
    out = espn_season.correct_boxes(env.lg, now)
    assert out["weeks"][6]["settled"] is True and db.box_doc(6)["settled"] is True
    assert env.fetches == [6]
    espn_season.correct_boxes(env.lg, now + timedelta(hours=2))
    assert env.fetches == [6]


def test_hourly_throttle(env):
    only(6)
    store(6, doc_of((11, 1, 10.0, "WR")), age=600)
    env.docs[6] = doc_of((11, 1, 10.0, "WR"))
    espn_season.correct_boxes(env.lg, NOW)
    assert env.fetches == []


def test_no_kickoffs_never_settles(env):
    only(6)
    env.kicks = {}
    store(6, doc_of((11, 1, 10.0, "WR")))
    env.docs[6] = doc_of((11, 1, 10.0, "WR"))
    out = espn_season.correct_boxes(env.lg, NOW + timedelta(days=60))
    assert out["weeks"][6]["settled"] is False and db.box_doc(6)["settled"] is False


def test_a_failing_week_is_reported_and_the_rest_go_on(env):
    for w in (1, 2, 3, 4):
        store(w, doc_of((1, 1, 1.0, "WR")), settled=True)
    store(5, doc_of((11, 1, 10.0, "WR")))
    store(6, doc_of((11, 1, 10.0, "WR")))
    before = db.box_doc(5)
    env.raises[5] = RuntimeError("espn down")
    env.docs[6] = doc_of((11, 1, 11.0, "WR"))
    out = espn_season.correct_boxes(env.lg, NOW)
    assert out["weeks"][5] == {"error": "espn down"}
    assert out["weeks"][6]["changed"] == 1 and out["changed"] == [6]
    assert db.box_doc(5) == before


def test_gap_rows_reread_and_corrected(env):
    only(6)
    store(6, doc_of((11, 1, 10.0, "WR")))
    env.docs[6] = doc_of((11, 1, 10.0, "WR"))
    db.put_gap_rows(6, {21: [{"pts": 4.0}]}, NOW.timestamp() - 7200)
    env.gap[(6, 21)] = [{"pts": 2.0}, {"pts": 4.0}]
    out = espn_season.correct_boxes(env.lg, NOW)
    [c] = db.box_corrections(week=6)
    assert (c["player_id"], c["team_id"], c["old"], c["new"]) == (21, None, 4.0, 6.0)
    assert db.gap_rows(6, [21])[21] == env.gap[(6, 21)]
    assert espn_season._GAP_GAMES[(21, 6)] == env.gap[(6, 21)]
    assert out["plays"] == [] and out["changed"] == [6]

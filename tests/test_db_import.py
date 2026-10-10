import json

import pytest

from ffl import db, db_import

PICK = ("1,1,1,6,henry's Imposing Team,3918298,Josh Allen,QB,BUF,1.9,4.0,360.8,2026-09-07T19:00:10\n"
        "2,1,2,2,Michael's Magnificent Team,4426348,Jayden Daniels,QB,WSH,,,,\n")


@pytest.fixture
def tree(tmp_path):
    journal, data = tmp_path / "journal", tmp_path / "data"
    for d in (journal / "draft", journal / "results", journal / "waivers", data / "live", data / "outlook",
              data / "calibration"):
        d.mkdir(parents=True)
    (journal / "transactions.csv").write_text(
        "id,date,type,team,player,action,bid,reasoning\n"
        "1788937502756-6-DROPPED-4688380,2026-09-09T03:05,DROPPED,henry's Imposing Team,Cam Ward,DROPPED,0,\n"
        "1788937502756-6-WAIVER ADDED-5083754,2026-09-09T03:05,WAIVER ADDED,henry's Imposing Team,Ryan Flournoy,"
        "WAIVER ADDED,12,because\n")
    (journal / "draft" / "picks.csv").write_text(
        "overall,round,pick,team_id,team,player_id,player,pos,nfl,adp_2qb,board_rank_at_pick,proj,ts\n" + PICK)
    (journal / "results" / "weekly.csv").write_text(
        "week,opponent,my_score,opp_score,result\n1,Michael's Monstrous Team,187.92,168.26,W\n")
    (journal / "waivers" / "week-5.json").write_text(json.dumps(
        {"week": 5, "groups": [], "submits": [], "saved_at": "2026-10-07T12:00:00-04:00"}))
    (data / "live" / "plays-week-1.jsonl").write_text(
        json.dumps({"at": "2026-09-10T20:15:00", "week": 1, "player_id": 11, "team_id": 6, "total": 4.5,
                    "delta": 4.5, "text": "pass"}) + "\n\n"
        + json.dumps({"at": "2026-09-10T20:16:00", "week": 1, "player_id": 12, "team_id": None, "total": 2.0,
                      "delta": 2.0, "text": "run"}) + "\n")
    entry = {"hash": "h", "card_hash": "c", "bullets": ["b"], "generated_at": "2026-10-01T00:00:00"}
    (data / "outlook" / "week-5.json").write_text(json.dumps({"1": entry, "junk": entry}))
    (data / "outlook" / "waivers-week-5.json").write_text(json.dumps({"2": entry}))
    (data / "calibration" / "week-4.json").write_text(json.dumps({"week": 4, "k": 4, "rows": [
        {"player_id": 1, "name": "A", "pos": "QB", "model": 20.0, "l3": 18.0, "weight": 3.0, "games_left": 10},
        {"player_id": 2, "name": "B", "pos": "RB", "model": 10.0, "l3": None, "weight": 0.0, "games_left": 10}]}))
    (data / "calibration" / "sigma.json").write_text(json.dumps(
        {"sigma": {"QB": 7.0}, "n": {"QB": 30}, "weeks": [1, 2, 3, 4]}))
    (journal / "DECISIONS.md").write_text("## 2026-09-22 — Week 3 waiver claims: Boston\n- **Decision:** claim Boston\n")
    return journal, data


def test_import_every_store_and_counts(tree):
    journal, data = tree
    report = {r["table"]: r for r in db_import.run(journal, data, 2026)}
    assert all(r["in_db"] >= r["source"] > 0 for r in report.values()), report
    assert report["outlooks"]["source"] == 2 and report["plays"]["source"] == 2
    assert report["decisions"]["source"] == 1 and db.count("decisions", 2026) == 1
    assert db.transactions()[0]["player_id"] == 4688380
    assert db.transactions()[1]["bid"] == 12
    assert db.picks()[0] == {"overall": 1, "round": 1, "pick": 1, "team_id": 6, "team": "henry's Imposing Team",
                             "player_id": 3918298, "player": "Josh Allen", "pos": "QB", "nfl": "BUF",
                             "adp_2qb": 1.9, "board_rank_at_pick": 4.0, "proj": 360.8, "ts": "2026-09-07T19:00:10"}
    assert db.picks()[1]["ts"] is None and db.picks()[1]["proj"] is None
    assert db.results_rows()[0]["my_score"] == 187.92
    assert db.plays_rows(1)[1]["text"] == "run"
    assert db.outlooks_for(5, "waivers-week") and set(db.outlooks_for(5, "week")) == {1}
    assert db.snapshots()[0]["k"] == 4 and len(db.snapshots()[0]["rows"]) == 2
    assert db.latest_sigma()["weeks"] == [1, 2, 3, 4]
    assert db.claim_plan(5)["week"] == 5


def test_import_twice_is_a_no_op(tree):
    journal, data = tree
    db_import.run(journal, data, 2026)
    again = db_import.run(journal, data, 2026)
    assert all(r["new"] == 0 for r in again), again


def test_import_never_overwrites_newer_db_rows(tree):
    journal, data = tree
    db_import.run(journal, data, 2026)
    db.put_outlooks(5, "week", {1: {"hash": "newer", "bullets": ["fresh"]}})
    db.put_claim_plan({"week": 5, "groups": ["newer"]})
    db_import.run(journal, data, 2026)
    assert db.outlooks_for(5, "week")[1]["hash"] == "newer"
    assert db.claim_plan(5)["groups"] == ["newer"]


def test_missing_sources_are_skipped(tmp_path):
    report = db_import.run(tmp_path / "j", tmp_path / "d", 2026)
    assert all(r["source"] == 0 for r in report)


def test_repeated_play_lines_count_once(tree):
    journal, data = tree
    p = data / "live" / "plays-week-1.jsonl"
    p.write_text(p.read_text() + p.read_text())
    report = {r["table"]: r for r in db_import.run(journal, data, 2026)}
    assert report["plays"]["source"] == report["plays"]["in_db"] == 2


def test_clean_tree_has_no_dupes(tree):
    assert all(r["dupes"] == 0 for r in db_import.run(*tree, 2026))


def test_duplicate_play_lines_are_accepted(tree, capsys):
    journal, data = tree
    p = data / "live" / "plays-week-1.jsonl"
    p.write_text(p.read_text() + p.read_text())
    assert db_import.main(journal, data, 2026) == 0
    assert "plays" in capsys.readouterr().out


def test_shared_key_in_picks_fails(tree, capsys):
    journal, data = tree
    p = journal / "draft" / "picks.csv"
    p.write_text(p.read_text() + PICK.splitlines()[0] + "\n")
    assert db_import.main(journal, data, 2026) == 1
    out = capsys.readouterr().out
    assert "draft_picks" in out.splitlines()[-1]
    assert {r["table"]: r["dupes"] for r in db_import.run(journal, data, 2026)}["draft_picks"] == 1


def test_insert_new_batches_past_the_bind_limit():
    rows = [{"season": 2026, "week": 1, "at": f"t{i}", "player_id": i, "team_id": None, "total": 1.0, "body": {"i": i}}
            for i in range(1700)]
    assert db.insert_new("plays", rows) == 1700
    assert db.insert_new("plays", rows) == 0
    assert db.count("plays") == 1700


def test_main_exit_code_follows_counts(tree, monkeypatch):
    journal, data = tree
    assert db_import.main(journal, data, 2026) == 0
    monkeypatch.setattr(db, "count", lambda table, season=2026: 0)
    assert db_import.main(journal, data, 2026) == 1


def test_decisions_import_is_idempotent(tmp_path):
    from tests.test_decisions import SAMPLE
    (tmp_path / "DECISIONS.md").write_text(SAMPLE)
    first = {r["table"]: r for r in db_import.run(tmp_path, tmp_path, 2026)}["decisions"]
    assert (first["source"], first["new"], first["dupes"]) == (5, 5, 0)
    again = {r["table"]: r for r in db_import.run(tmp_path, tmp_path, 2026)}["decisions"]
    assert again["new"] == 0 and db.count("decisions", 2026) == 5

"""Every NFL play stored once, and the plays called back that cost a starter points (texts from 2026's feed)."""
from datetime import datetime

from ffl import db, playbook, plays

# the league's table for the stats a called-back play is read for (settings.json)
TABLE = {3: 0.04, 4: 4.0, 24: 0.1, 25: 6.0, 42: 0.1, 43: 6.0, 53: 1.0, 80: 3.0, 77: 4.0, 198: 5.0, 201: 6.0, 86: 1.0,
         95: 2.0, 99: 1.0, 103: 6.0, 20: -2.0}
TABLE = {pos: TABLE for pos in ("", "QB", "RB", "WR", "TE", "K", "D/ST")}   # every position on it
ROSTER = {1: {"name": "Brock Purdy", "pos": "QB", "team": "SF", "team_id": 7},
          2: {"name": "Mike Evans", "pos": "WR", "team": "SF", "team_id": 3},
          3: {"name": "Bryce Young", "pos": "QB", "team": "CAR", "team_id": 3},
          4: {"name": "Brandon Aubrey", "pos": "K", "team": "DAL", "team_id": 5},
          -16026: {"name": "Seahawks D/ST", "pos": "D/ST", "team": "SEA", "team_id": 5}}


def play(text, roles, pid="p1", source="espn", offence="SF", defence="ARI", **kw):
    return {"play_id": pid, "game_id": "g", "week": 3, "quarter": 4, "clock": "5:00", "text": text,
            "offence": offence, "defence": defence, "roles": [{"id": i, "type": t} for i, t in roles],
            "at": "2026-09-27T19:06:30", "source": source, **kw}


SCRAMBLE = play("B.Purdy scrambles left end for 45 yards, TOUCHDOWN NULLIFIED by Penalty. PENALTY on SF-G.Kittle, "
                "Offensive Holding, 10 yards, enforced at ARZ 10.", [(1, "rusher")])
NO_PLAY_CATCH = play("B.Young pass deep left to M.Evans to PHI 17 for 23 yards (Q.Mitchell).PENALTY on CAR-R.Hunt, "
                     "Ineligible Downfield Pass, 5 yards, enforced at PHI 40 - No Play.", [(3, "passer"), (2, "receiver")])


def test_called_back_reads_nullified_and_no_play_not_declined_or_reversed():
    assert playbook.called_back(SCRAMBLE["text"]) and playbook.called_back(NO_PLAY_CATCH["text"])
    assert not playbook.called_back("K.Williams up the middle for 9 yards. Penalty on BUF, Holding, declined.")
    assert not playbook.called_back("The play was REVERSED. B.Young pass short left to J.Coker for 5 yards.")


def test_would_be_reads_the_play_before_the_flag():
    assert playbook.would_be(SCRAMBLE, 1, "QB", "SF", TABLE) == 10.5   # 45 yards and the rushing TD
    assert playbook.would_be(NO_PLAY_CATCH, 2, "WR", "SF", TABLE) == 3.3  # the catch and 23 yards
    assert playbook.would_be(NO_PLAY_CATCH, 3, "QB", "CAR", TABLE) == 0.92
    incomplete = play("B.Young pass incomplete short right [Z.Baun].PENALTY on PHI-C.DeJean, Illegal Contact - No Play.",
                      [(3, "passer")])
    assert playbook.would_be(incomplete, 3, "QB", "CAR", TABLE) == 0


def test_a_kick_and_a_defence_are_read_too():
    fg = play("B.Aubrey 51 yard field goal is GOOD, Center-T.Sieg.PENALTY on DAL-T.Guyton, Holding - No Play.",
              [(4, "kicker")])
    assert playbook.would_be(fg, 4, "K", "DAL", TABLE) == 5.0
    sack = play("J.Herbert sacked at LAC 20 for -9 yards (U.Nwosu).PENALTY on SEA-D.Hall, Defensive Holding - No Play.",
                [], offence="LAC", defence="SEA")
    assert playbook.would_be(sack, -16026, "D/ST", "SEA", TABLE) == 1.0
    assert playbook.would_be(sack, -16026, "D/ST", "LAC", TABLE) == 0, "the offence's D/ST gets nothing"


def test_callbacks_take_off_what_the_play_kept_him():
    rows = playbook.callbacks(SCRAMBLE, ROSTER, {1: 3.5}, TABLE)  # the 35 yards to the foul spot counted
    assert [(r["player_id"], r["would_be"], r["kept"], r["match"]) for r in rows] == [(1, 10.5, 3.5, "called_back")]
    assert rows[0]["team_id"] == 7 and rows[0]["delta"] == 0.0 and rows[0]["total"] is None
    assert playbook.callbacks(SCRAMBLE, ROSTER, {1: 10.5}, TABLE) == []


def test_callbacks_skip_a_play_that_names_no_starter_or_still_counts():
    false_start = play("PENALTY on PHI-D.Goedert, False Start, 5 yards, enforced at PHI 47 - No Play.", [(99, "penalized")])
    assert playbook.callbacks(false_start, ROSTER, {}, TABLE) == []
    upheld = {**SCRAMBLE, "text": "B.Purdy scrambles left end for 45 yards, TOUCHDOWN."}
    assert playbook.callbacks(upheld, ROSTER, {}, TABLE) == []


def test_espn_read_wins_over_the_live_feed():
    live = {**SCRAMBLE, "source": "live"}
    assert playbook.changes(None, live) and playbook.changes(live, SCRAMBLE)
    assert not playbook.changes(SCRAMBLE, {**live, "text": "older wording"})
    assert not playbook.changes(SCRAMBLE, dict(SCRAMBLE)) and playbook.changes(SCRAMBLE, {**SCRAMBLE, "text": "x"})


def test_from_fastcast_takes_each_play_once_with_its_clubs():
    doc = {"events": [{"id": "g", "competitors": [{"id": "25", "abbreviation": "SF"}, {"id": "22", "abbreviation": "ARI"}],
                       "lastPlay": {"playId": "9", "text": " B.Purdy kneels. ", "period": 4, "clock": 40.0,
                                    "start": {"team": {"id": "25"}}, "players": [{"playerId": "1", "type": "rusher"}]},
                       "drive": {"plays": [{"playId": "9", "text": "B.Purdy kneels.", "period": 4, "clock": 40.0,
                                            "start": {"team": {"id": "25"}}, "players": []}]}}]}
    [p] = playbook.from_fastcast(doc, 3, datetime(2026, 9, 27, 19, 0))
    assert (p["play_id"], p["offence"], p["defence"], p["clock"], p["source"]) == ("9", "SF", "ARI", "0:40", "live")


def test_from_drives_turns_washington_into_one_spelling():
    drives = [{"plays": [{"id": 5, "quarter": 1, "clock": "9:00", "team": "WAS", "text": "x", "roles": [],
                          "wallclock": "2026-10-18T17:00:00Z"}]}]
    [p] = playbook.from_drives(drives, "g", 6, ("SF", "WSH"))
    assert (p["offence"], p["defence"], p["source"]) == ("WSH", "SF", "espn") and p["at"].startswith("2026-10-18")


def test_store_records_plays_and_keeps_called_back_rows_current():
    store = plays.Store(3)
    store.add([{"at": "2026-09-27T19:06:31", "week": 3, "player_id": 1, "team_id": 7, "delta": 3.5, "total": 26.58,
                "text": "B.Purdy scrambles left end for 45 yards, TOUCHDOWN.", "play_id": "p1", "match": "player"}])
    store.record([{**SCRAMBLE, "source": "live"}], ROSTER, TABLE)
    [row] = store.latest()
    assert row["delta"] == 3.5 and row["called_back"] == {"would_be": 10.5, "kept": 3.5}, \
        "one row: the 3.5 he kept, of the 10.5 called back"
    assert row["text"] == SCRAMBLE["text"], "a row carrying a call-back reads the newest wording"
    reloaded = plays.Store(3)
    assert reloaded.called == store.called and reloaded.book["p1"]["text"] == SCRAMBLE["text"]
    store.record([{**SCRAMBLE, "text": "B.Purdy scrambles left end for 45 yards, TOUCHDOWN."}], ROSTER, TABLE)
    assert store.called == {} and db.called_back_rows(3) == [], "upheld on review: the row goes"


def test_final_plays_reads_a_game_once_final_half_an_hour_even_if_opened_mid_game(monkeypatch):
    from ffl import api, nfl
    game = {"event_id": "e1", "status": "post", "away": {"abbr": "SF", "id": "25"}, "home": {"abbr": "ARI", "id": "22"}}
    monkeypatch.setattr(nfl, "scoreboard", lambda week: ({"week": 6, "games": [game] if week == 6 else []}, None))
    monkeypatch.setattr(nfl, "drives", lambda *a: ([{"plays": [{"id": 9, "team": "SF", "text": "x", "roles": []}]}], None))
    recorded = []
    monkeypatch.setattr(api, "_record_plays", lambda week, entries: recorded.append((week, entries)))
    monkeypatch.setattr(api, "_FINAL_SEEN", {})
    db.put_nfl_plays([{**SCRAMBLE, "game_id": "e1", "week": 6}])  # the NFL tab wrote ESPN's rows in Q1
    clock = [1000.0]
    monkeypatch.setattr(api.time, "time", lambda: clock[0])
    assert api.final_plays() == {} and recorded == [], "just final: ESPN's drives can lag the whistle"
    clock[0] += api.FINAL_PLAYS_AFTER
    assert api.final_plays() == {"6": 1} and recorded[0][1][0]["play_id"] == "9"
    assert db.get_state("final_plays_done") == ["e1"]
    clock[0] += 3600
    assert api.final_plays() == {} and len(recorded) == 1, "read once"


def test_a_row_reads_the_wording_of_its_time_and_a_stat_fix_the_newest():
    catch = "B.Purdy pass deep right to M.Evans pushed ob at WAS 17 for 55 yards (N.Cross)."
    respot = "B.Purdy pass deep right to M.Evans pushed ob at WAS 18 for 54 yards (N.Cross)."
    long = play(catch, [(1, "passer")], pid="460", source="live", week=6)
    store = plays.Store(6)
    store.record([long], ROSTER, TABLE, seen=datetime(2026, 10, 19, 20, 33, 37))
    row = {"week": 6, "player_id": 1, "team_id": 7, "total": 8.24, "play_id": "460", "match": "player"}
    store.add([{**row, "at": "2026-10-19T20:33:37", "delta": 2.2, "text": catch}])
    store.record([{**long, "text": respot}], ROSTER, TABLE, seen=datetime(2026, 10, 19, 20, 33, 47))
    store.add([{**row, "at": "2026-10-19T20:33:47", "delta": -0.04, "total": 8.2, "text": respot, "match": "adjusted"}])
    assert [(r["delta"], r["text"]) for r in store.latest()] == [(-0.04, respot), (2.2, catch)]
    assert all(r["text"] is None for r in db.plays_rows(6)), "a row on a stored play keeps no text of its own"
    assert [t["text"] for t in plays.Store(6).book["460"]["texts"]] == [catch, respot]


def test_a_called_back_row_folds_into_his_newest_row_on_the_play_or_stands_alone():
    rows = [{"at": "t3", "player_id": 1, "play_id": "p", "delta": -0.7}, {"at": "t2", "player_id": 1, "play_id": "p", "delta": 0.7},
            {"at": "t1", "player_id": 2, "play_id": "p", "delta": 1.0}]
    called = {("p", 1): {"at": "t0", "player_id": 1, "play_id": "p", "would_be": 0.7, "kept": 0.0},
              ("q", 1): {"at": "t0", "player_id": 1, "play_id": "q", "would_be": 3.3, "kept": 0.0}}
    out = plays.fold_called_back(rows, called)
    assert [(r["at"], "called_back" in r) for r in out] == [("t3", True), ("t2", False), ("t1", False), ("t0", False)]
    assert out[-1]["play_id"] == "q", "a play that scored him nothing keeps its own row"


def test_versions_add_a_wording_only_when_it_reads_differently():
    v1 = playbook.versioned(None, {"text": "a"}, "t1")
    assert playbook.versioned(v1, {"text": "a"}, "t2")["texts"] == [{"at": "t1", "text": "a"}]
    assert playbook.versioned(v1, {"text": "b"}, "t2")["texts"] == [{"at": "t1", "text": "a"}, {"at": "t2", "text": "b"}]


def test_store_never_drops_rows_without_a_roster():
    store = plays.Store(3)
    store.record([SCRAMBLE], ROSTER, TABLE)
    store.record([{**SCRAMBLE, "text": "upheld"}], {}, TABLE)
    assert len(store.called) == 1

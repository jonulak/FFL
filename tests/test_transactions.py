"""season.group_transactions: journal/transactions.csv rows -> the Transactions page's moves.

A waiver run stamps every claim with one timestamp across teams, so a move is (timestamp, team);
a trade is every TRADE_* row of one timestamp, one side per team. No network here.
"""
from datetime import date

from ffl import season as S

W1 = date(2026, 9, 8)


def row(ms, team, action, pid, name, when="2026-09-16T03:05"):
    return {"id": f"{ms}-{team}-{action}-{pid}", "date": when, "type": action, "team": f"Team {team}",
            "player": name, "action": action, "bid": "0", "reasoning": ""}


INFO = {101: {"pos": "WR", "team": "ATL"}, 102: {"pos": "RB", "team": "TEN"}, -16020: {"pos": "D/ST", "team": "NYJ"}}


def test_an_add_and_its_drop_are_one_move():
    moves = S.group_transactions([row(1, 6, "DROPPED", 102, "Tyjae Spears"),
                                  row(1, 6, "WAIVER ADDED", 101, "Drake London")], INFO, W1)
    assert len(moves) == 1
    m = moves[0]
    assert m["kind"] == "waiver" and m["week"] == 2 and m["id"] == "1-6"
    (side,) = m["sides"]
    assert side["team_id"] == 6 and side["team"] == "Team 6"
    assert side["added"] == [{"player_id": 101, "name": "Drake London", "pos": "WR", "team": "ATL"}]
    assert side["dropped"] == [{"player_id": 102, "name": "Tyjae Spears", "pos": "RB", "team": "TEN"}]


def test_one_waiver_run_splits_by_team_and_reads_a_dst_id():
    moves = S.group_transactions([row(5, 3, "WAIVER ADDED", -16020, "Jets D/ST"),
                                  row(5, 7, "FA ADDED", 101, "Drake London")], INFO, W1)
    assert sorted((m["id"], m["kind"]) for m in moves) == [("5-3", "waiver"), ("5-7", "free_agent")]
    dst = next(m for m in moves if m["id"] == "5-3")["sides"][0]["added"][0]
    assert dst == {"player_id": -16020, "name": "Jets D/ST", "pos": "D/ST", "team": "NYJ"}


def test_a_lone_drop_and_an_unknown_player():
    (m,) = S.group_transactions([row(9, 4, "DROPPED", 999, "Nobody")], INFO, W1)
    assert m["kind"] == "drop"
    assert m["sides"][0]["added"] == [] and m["sides"][0]["dropped"][0]["pos"] is None


def test_a_trade_is_one_move_with_a_side_per_team():
    rows = [row(7, 2, "TRADE_SENT", 101, "Drake London"), row(7, 1, "TRADE_RECEIVED", 101, "Drake London"),
            row(7, 1, "TRADE_SENT", 102, "DeVonta Smith"), row(7, 2, "TRADE_RECEIVED", 102, "DeVonta Smith"),
            row(7, 1, "DROPPED", -16020, "Jets D/ST")]
    (m,) = S.group_transactions(rows, INFO, W1)
    assert m["kind"] == "trade" and m["id"] == "7"
    by_team = {s["team_id"]: s for s in m["sides"]}
    assert [p["name"] for p in by_team[1]["added"]] == ["Drake London"]
    assert [p["name"] for p in by_team[1]["dropped"]] == ["Jets D/ST"]
    assert [p["name"] for p in by_team[2]["added"]] == ["DeVonta Smith"]
    assert by_team[2]["dropped"] == []


def test_newest_first_and_preseason_is_week_zero():
    moves = S.group_transactions([row(1, 6, "FA ADDED", 101, "a", when="2026-09-07T10:00"),
                                  row(2, 6, "FA ADDED", 101, "b", when="2026-09-20T10:00")], INFO, W1)
    assert [(m["id"], m["week"]) for m in moves] == [("2-6", 2), ("1-6", 0)]
    assert moves[0]["at"] == "2026-09-20T10:00"


def test_a_malformed_id_is_skipped_not_fatal():
    assert S.group_transactions([{"id": "junk", "date": "2026-09-16T03:05", "player": "x"}], INFO, W1) == []


def _moves():
    rows = [row(7, 2, "TRADE_SENT", 101, "Drake London"), row(7, 1, "TRADE_RECEIVED", 101, "Drake London"),
            row(7, 1, "TRADE_SENT", 102, "DeVonta Smith"), row(7, 2, "TRADE_RECEIVED", 102, "DeVonta Smith"),
            row(5, 6, "WAIVER ADDED", 101, "Drake London", when="2026-09-10T03:05"),
            row(5, 6, "DROPPED", 103, "Cam Little", when="2026-09-10T03:05"),
            row(6, 6, "DROPPED", 101, "Drake London", when="2026-09-12T03:05"),
            row(6, 6, "FA ADDED", 104, "Jonah Coleman", when="2026-09-12T03:05"),
            row(8, 4, "DROPPED", 105, "Carson Wentz")]
    return S.group_transactions(rows, INFO, W1)


def test_player_history_reads_each_move_from_his_side():
    got = S.player_transactions(_moves(), 101)
    assert [(h["kind"], h["team_id"], h["with"]) for h in got] == [
        ("traded", 1, ["DeVonta Smith"]),  # received by team 1, for what team 2 received
        ("dropped", 6, ["Jonah Coleman"]),  # cut to make room for
        ("waiver", 6, ["Cam Little"]),  # claimed in place of
    ]
    assert got[0]["from"] == {"team_id": 2, "team": "Team 2"}
    assert got[1]["from"] is None and got[1]["week"] == 1


def test_player_history_pairs_a_multi_claim_line_for_line():
    """Three claims and four cuts in one waiver run: each reads only its own pair, as the page lines them up."""
    rows = [row(9, 7, "WAIVER ADDED", 201, "Evan McPherson"), row(9, 7, "DROPPED", 301, "Cam Little"),
            row(9, 7, "WAIVER ADDED", 202, "Vikings D/ST"), row(9, 7, "DROPPED", 302, "Ravens D/ST"),
            row(9, 7, "WAIVER ADDED", 203, "Denzel Boston"), row(9, 7, "DROPPED", 303, "Brian Thomas Jr."),
            row(9, 7, "DROPPED", 304, "Kenny Gainwell")]
    moves = S.group_transactions(rows, INFO, W1)
    assert S.player_transactions(moves, 202)[0]["with"] == ["Ravens D/ST"]
    assert S.player_transactions(moves, 302)[0]["with"] == ["Vikings D/ST"]
    assert S.player_transactions(moves, 304)[0]["with"] == [], "the fourth cut had no claim beside it"


def test_player_history_lone_drop_and_none():
    (h,) = S.player_transactions(_moves(), 105)
    assert h["kind"] == "dropped" and h["with"] == []
    assert S.player_transactions(_moves(), 999) == []


PICKS = [{"overall": 24, "round": 3, "pick": 4, "team_id": 6, "team": "henry's Imposing Team", "player_id": 101,
          "at": "2026-09-07T19:40:00"}]
CLUBS = {6: {"team_id": 6, "team": "Daejon Love Fan Club", "abbrev": "DLFC", "logo": "x.png"}}


def test_draft_entry_is_his_pick_under_the_current_club():
    h = S.draft_entry(PICKS, 101, CLUBS)
    assert h["kind"] == "draft" and (h["round"], h["pick"], h["overall"]) == (3, 4, 24)
    assert h["team"] == "Daejon Love Fan Club" and h["logo"] == "x.png", "a renamed team reads its current name"
    assert h["week"] == 0 and h["with"] == [] and h["from"] is None
    assert S.draft_entry(PICKS, 999, CLUBS) is None


def test_draft_entry_keeps_the_logged_name_for_a_team_gone():
    assert S.draft_entry(PICKS, 101, {})["team"] == "henry's Imposing Team"


def test_draft_picks_read_the_csv(monkeypatch, tmp_path):
    from ffl import espn_season as E
    (tmp_path / "draft").mkdir()
    (tmp_path / "draft" / "picks.csv").write_text(
        "overall,round,pick,team_id,team,player_id,player,pos,nfl,adp_2qb,board_rank_at_pick,proj,ts\n"
        "24,3,4,6,henry's Imposing Team,101,Drake London,WR,ATL,20,21,200,2026-09-07T19:40:00\n")
    monkeypatch.setattr(E, "JOURNAL", tmp_path)
    monkeypatch.setattr(E, "fresh_league", lambda: (_ for _ in ()).throw(AssertionError("no ESPN call")))
    assert E.draft_picks() == PICKS


def test_draft_picks_fall_back_to_espn_when_the_csv_is_lost(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from ffl import espn_season as E
    monkeypatch.setattr(E, "JOURNAL", tmp_path)  # no draft/picks.csv here
    t = SimpleNamespace(team_id=6, team_name="Daejon Love Fan Club")
    draft = [SimpleNamespace(playerId=7, round_num=1, round_pick=1, team=t),
             SimpleNamespace(playerId=101, round_num=1, round_pick=2, team=t)]
    monkeypatch.setattr(E, "fresh_league", lambda: SimpleNamespace(draft=draft))
    got = E.draft_picks()
    assert got[1] == {"overall": 2, "round": 1, "pick": 2, "team_id": 6, "team": "Daejon Love Fan Club",
                      "player_id": 101, "at": None}


def test_draft_picks_fall_back_on_an_empty_csv(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from ffl import espn_season as E
    (tmp_path / "draft").mkdir()
    (tmp_path / "draft" / "picks.csv").write_text("overall,round,pick,team_id,team,player_id,player\n")
    monkeypatch.setattr(E, "JOURNAL", tmp_path)
    t = SimpleNamespace(team_id=6, team_name="T")
    monkeypatch.setattr(E, "fresh_league", lambda: SimpleNamespace(draft=[SimpleNamespace(playerId=5, round_num=1, round_pick=1, team=t)]))
    assert [p["player_id"] for p in E.draft_picks()] == [5]


def test_player_info_reads_kona_and_derives_a_dst(monkeypatch):
    from ffl import espn_season as E
    seen = {}

    def fake_get(view, headers=None, **kw):
        seen["filter"] = headers["x-fantasy-filter"]
        return {"players": [{"player": {"id": 101, "defaultPositionId": 3, "proTeamId": 1}},
                            {"player": {"id": 102, "defaultPositionId": 2, "proTeamId": 0}}]}

    monkeypatch.setattr(E, "raw_league_get", fake_get)
    info = E.tx_player_info({101, 102, -16020})
    assert info[101] == {"pos": "WR", "team": "ATL"}
    assert info[102] == {"pos": "RB", "team": None}, "a player out of the NFL has no club"
    assert info[-16020] == {"pos": "D/ST", "team": "NYJ"}
    assert "16020" not in seen["filter"], "a D/ST is never looked up"
    assert "limit" not in seen["filter"], "ESPN answers 400 to a limit beside filterIds"


def test_player_info_asks_in_chunks(monkeypatch):
    from ffl import espn_season as E
    asked = []
    monkeypatch.setattr(E, "raw_league_get", lambda view, headers=None, **kw: asked.append(headers) or {"players": []})
    E.tx_player_info(set(range(1, E.TX_CHUNK * 2 + 2)))
    assert len(asked) == 3


def test_player_info_failure_keeps_the_dsts(monkeypatch):
    from ffl import espn_season as E

    def boom(*a, **kw):
        raise RuntimeError("espn down")

    monkeypatch.setattr(E, "raw_league_get", boom)
    assert E.tx_player_info({101, -16020}) == {-16020: {"pos": "D/ST", "team": "NYJ"}}


def test_state_dresses_sides_with_the_current_club(monkeypatch):
    from types import SimpleNamespace

    from ffl import espn_season as E
    t6 = SimpleNamespace(team_id=6, team_name="Daejon Love Fan Club", team_abbrev="DLFC", logo_url="x.png",
                         wins=3, losses=1, ties=0)
    monkeypatch.setattr(E, "journal_state", lambda: {"transactions": [
        row(1, 6, "FA ADDED", -16020, "Jets D/ST"), row(1, 9, "DROPPED", -16020, "Jets D/ST")]})
    monkeypatch.setattr(E, "fresh_league", lambda: SimpleNamespace(teams=[t6], current_week=5))
    monkeypatch.setattr(E, "my_team_id", lambda lg: 7)
    monkeypatch.setattr(E, "load", lambda: {"week1_start": "2026-09-08"})
    monkeypatch.setattr("ffl.journal.sync_transactions", lambda lg: None)
    got = E.transactions_state()
    assert got["me"] == 7 and got["current_week"] == 5
    sides = {m["sides"][0]["team_id"]: m["sides"][0] for m in got["moves"]}
    assert sides[6]["team"] == "Daejon Love Fan Club" and sides[6]["logo"] == "x.png"
    assert sides[9]["team"] == "Team 9" and sides[9]["logo"] == "", "a team gone from the league keeps the log's name"


def _state_env(monkeypatch, rows, log):
    from types import SimpleNamespace

    from ffl import espn_season as E
    lg = SimpleNamespace(teams=[], current_week=5)
    monkeypatch.setattr(E, "journal_state", lambda: log.append("read") or {"transactions": rows})
    monkeypatch.setattr(E, "fresh_league", lambda: lg)
    monkeypatch.setattr(E, "my_team_id", lambda lg: 7)
    monkeypatch.setattr(E, "load", lambda: {"week1_start": "2026-09-08"})
    return E, lg


def test_state_syncs_the_feed_before_reading_the_log(monkeypatch):
    """A move made since the last Refresh (an FA add on a Wednesday morning) must reach the page:
    the log is synced from ESPN's activity feed on every build, before it is read."""
    from ffl import journal
    log = []
    E, lg = _state_env(monkeypatch, [], log)
    monkeypatch.setattr(journal, "sync_transactions", lambda league: log.append(("sync", league)))
    E.transactions_state()
    assert log == [("sync", lg), "read"]


def test_state_serves_the_log_when_the_sync_fails(monkeypatch):
    from ffl import journal

    def boom(league):
        raise RuntimeError("espn down")

    E, _ = _state_env(monkeypatch, [row(1, 6, "FA ADDED", -16020, "Jets D/ST")], [])
    monkeypatch.setattr(journal, "sync_transactions", boom)
    monkeypatch.setattr(E, "tx_player_info", lambda ids: INFO)
    assert len(E.transactions_state()["moves"]) == 1


def test_log_owners_replays_the_draft_then_the_log():
    picks = [{"player_id": 101, "team_id": 1}, {"player_id": 102, "team_id": 2}, {"player_id": 103, "team_id": 3}]
    rows = [row(1, 1, "DROPPED", 101, "A"), row(1, 4, "FA ADDED", 104, "D"),
            row(2, 4, "WAIVER ADDED", 101, "A"), row(2, 4, "DROPPED", 104, "D"),
            # a trade is one timestamp; the file may hold a received row before its sent row
            row(3, 3, "TRADE_RECEIVED", 102, "B"), row(3, 2, "TRADE_RECEIVED", 103, "C"),
            row(3, 2, "TRADE_SENT", 102, "B"), row(3, 3, "TRADE_SENT", 103, "C")]
    assert S.log_owners(picks, rows) == {101: 4, 102: 3, 103: 2}


def test_log_owners_reads_the_log_in_time_order():
    """The log is appended per sync, newest first within one; a re-add must beat an older drop."""
    rows = [row(5, 4, "FA ADDED", 101, "A"), row(2, 4, "DROPPED", 101, "A")]
    assert S.log_owners([{"player_id": 101, "team_id": 4}], rows) == {101: 4}


def test_ownership_drift_names_every_player_the_log_has_wrong():
    expected = {101: 4, 102: 3, 103: 2}
    espn = {101: 4, 102: 5, 105: 6}  # 102 moved on, 103 was dropped, 105 was picked up
    assert S.ownership_drift(espn, expected) == {102, 103, 105}
    assert S.ownership_drift(expected, expected) == set()


def test_current_week_box_scores_reconcile_the_log(monkeypatch):
    """Every page's rosters come through box_scores: a fresh current-week fetch checks the record,
    a finished week (or a cached answer) does not."""
    from types import SimpleNamespace

    from ffl import espn_season as E
    t6 = SimpleNamespace(team_id=6, team_name="DLFC", team_abbrev="DL")
    box = SimpleNamespace(home_team=t6, home_lineup=[SimpleNamespace(playerId=4241474)],
                          away_team=0, away_lineup=[])
    seen = []
    monkeypatch.setattr(E, "reconcile_log", lambda lg, owners: seen.append(owners))
    lg = SimpleNamespace(current_week=5, box_scores=lambda week: [box])
    E.box_scores(lg, 5)
    E.box_scores(lg, 5)  # cached
    E.box_scores(lg, 4)  # finished week
    assert seen == [{4241474: 6}]


def test_box_scores_stand_when_the_reconcile_fails(monkeypatch):
    from types import SimpleNamespace

    from ffl import espn_season as E
    monkeypatch.setattr(E, "reconcile_log", lambda lg, owners: (_ for _ in ()).throw(RuntimeError("espn down")))
    lg = SimpleNamespace(current_week=5, box_scores=lambda week: ["box"])
    monkeypatch.setattr(E, "owners_in", lambda boxes: {})
    assert E.box_scores(lg, 5) == ["box"]


def test_reconcile_syncs_once_per_disagreement(monkeypatch):
    """A gap the feed cannot close (a commissioner move it never reports) must not sync on every
    rebuild: one sync per distinct set of disagreeing players."""
    from types import SimpleNamespace

    from ffl import espn_season as E
    from ffl import journal
    synced = []
    monkeypatch.setattr(journal, "sync_transactions", lambda lg: synced.append(1))
    monkeypatch.setattr(E, "journal_state", lambda: {"transactions": []})
    monkeypatch.setattr(E, "draft_picks", lambda: [{"player_id": 101, "team_id": 1}])
    monkeypatch.setattr(E, "_RECONCILED", [None])
    lg = SimpleNamespace()
    assert E.reconcile_log(lg, {101: 1}) is False and synced == []
    assert E.reconcile_log(lg, {101: 1, 105: 6}) is True and synced == [1]
    assert E.reconcile_log(lg, {101: 1, 105: 6}) is False and synced == [1]
    assert E.reconcile_log(lg, {105: 6}) is True and synced == [1, 1]

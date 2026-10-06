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


def test_player_history_lone_drop_and_none():
    (h,) = S.player_transactions(_moves(), 105)
    assert h["kind"] == "dropped" and h["with"] == []
    assert S.player_transactions(_moves(), 999) == []


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
    got = E.transactions_state()
    assert got["me"] == 7 and got["current_week"] == 5
    sides = {m["sides"][0]["team_id"]: m["sides"][0] for m in got["moves"]}
    assert sides[6]["team"] == "Daejon Love Fan Club" and sides[6]["logo"] == "x.png"
    assert sides[9]["team"] == "Team 9" and sides[9]["logo"] == "", "a team gone from the league keeps the log's name"

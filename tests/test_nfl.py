import json
from pathlib import Path

from ffl import nfl

FIX = Path(__file__).parent / "fixtures" / "nfl"


def load(name):
    return json.loads((FIX / name).read_text())


def test_club_aliases():
    assert nfl.club("was") == "WSH" and nfl.club("JAC") == "JAX" and nfl.club("LA") == "LAR"
    assert nfl.club("gb") == "GB"


def test_parse_scoreboard_states_and_byes():
    sb = nfl.parse_scoreboard(load("scoreboard-w5.json"))
    assert sb["week"] == 5
    assert sb["byes"] == ["CAR", "KC"]
    by = {g["event_id"]: g for g in sb["games"]}
    final, live, pre = by["401872981"], by["401872990"], by["401872989"]  # PHI-JAX, CHI @ GB, DEN @ LAC
    assert final["status"] == "post" and final["clock"] is None and final["detail"] == "Final"
    assert final["away"]["score"] is not None and (final["away"]["winner"] or final["home"]["winner"])
    assert live["status"] == "in" and live["quarter"] == 1 and live["clock"]
    assert live["ball"] in ("GB", "CHI") and live["down"]
    assert live["away"]["record"] == "3-1" and live["away"]["name"] == "Bears"
    assert pre["status"] == "pre" and pre["away"]["score"] is None and pre["ball"] is None
    # every club the scoreboard names is one of ESPN's 32
    assert {c for g in sb["games"] for c in (g["away"]["abbr"], g["home"]["abbr"])} <= nfl.CLUBS


def _g(eid, kickoff, status, away, home):
    return {"event_id": eid, "kickoff": kickoff, "status": status, "away": {"abbr": away}, "home": {"abbr": home}}


def test_board_live_first_then_windows_mine_first():
    games = [
        _g("1", "2026-10-11T17:00Z", "pre", "NYG", "WSH"),
        _g("2", "2026-10-11T17:00Z", "pre", "CIN", "MIA"),
        _g("3", "2026-10-11T17:00Z", "pre", "MIN", "NO"),
        _g("4", "2026-10-11T20:25Z", "pre", "SF", "SEA"),
        _g("5", "2026-10-09T00:15Z", "post", "TB", "DAL"),
        _g("6", "2026-10-11T17:00Z", "in", "CHI", "GB"),
    ]
    out = nfl.board(games, mine={"CIN": 2, "SF": 1}, opp={"NO": 1})
    assert out[0]["live"] is True and [g["event_id"] for g in out[0]["games"]] == ["6"]
    assert [s["kickoff"] for s in out[1:]] == ["2026-10-09T00:15Z", "2026-10-11T17:00Z", "2026-10-11T20:25Z"]
    assert [g["event_id"] for g in out[2]["games"]] == ["2", "3", "1"]  # mine, then opp only, then the rest
    assert out[2]["games"][0]["mine"] == 2 and out[2]["games"][1]["opp"] == 1


def test_board_no_live_section_when_nothing_on():
    out = nfl.board([_g("1", "2026-10-11T17:00Z", "pre", "NYG", "WSH")], {}, {})
    assert [s["live"] for s in out] == [False]

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


FINAL_IDS = {"21": "PHI", "30": "JAX"}
LIVE_IDS = {"9": "GB", "3": "CHI"}


def test_parse_drives_final_counts_and_order():
    drives = nfl.parse_drives(load("drives-final-401872981.json"), FINAL_IDS)
    assert len(drives) == 23
    assert sum(len(d["plays"]) for d in drives) == 199
    first_play = drives[-1]["plays"][-1]  # oldest drive, oldest play
    assert first_play["quarter"] == 1
    assert drives[0]["plays"][0]["quarter"] >= 4  # newest first
    assert all(d["team"] in ("PHI", "JAX", None) for d in drives)
    assert any(p["scoring"] for d in drives for p in d["plays"])
    p = next(p for d in drives for p in d["plays"] if p["down"])
    assert p["down"].startswith(("1st", "2nd", "3rd", "4th"))
    assert all(isinstance(a, int) for d in drives for p in d["plays"] for a in p["athletes"])


def test_parse_drives_in_progress_drive_has_no_result():
    drives = nfl.parse_drives(load("drives-live-401872990.json"), LIVE_IDS)
    assert drives[0]["result"] is None and drives[0]["plays"]
    assert drives[0]["description"]


def test_parse_drives_empty_before_kickoff():
    assert nfl.parse_drives({"items": []}, LIVE_IDS) == []


def _play(pid, team, athletes=(), scoring=False, turnover=False, kind="Rush"):
    return {"id": pid, "team": team, "athletes": list(athletes), "scoring": scoring, "turnover": turnover,
            "kind": kind, "mine": [], "opp": []}


def test_mark_by_athlete_and_dst_by_club_on_defence():
    drives = [{"id": "d", "plays": [
        _play("1", "PHI", athletes=[4040715]),                      # Hurts: mine
        _play("2", "JAX", athletes=[3916433]),                      # Meyers: opp
        _play("3", "PHI", kind="Sack"),                             # JAX D/ST on defence: opp's D/ST
        _play("4", "JAX", turnover=True),                           # PHI on defence, no PHI D/ST rostered
        _play("5", "PHI", athletes=[1], kind="Pass Reception"),     # nobody
    ]}]
    out = nfl.mark(drives, {4040715: "Jalen Hurts"}, {3916433: "Jakobi Meyers"}, {}, {"JAX": "Jaguars D/ST"},
                   ("PHI", "JAX"))
    ps = {p["id"]: p for p in out[0]["plays"]}
    assert ps["1"]["mine"] == ["Jalen Hurts"] and ps["1"]["opp"] == []
    assert ps["2"]["opp"] == ["Jakobi Meyers"]
    assert ps["3"]["opp"] == ["Jaguars D/ST"]
    assert ps["4"]["mine"] == [] and ps["4"]["opp"] == []
    assert ps["5"]["mine"] == [] and ps["5"]["opp"] == []
    assert drives[0]["plays"][0]["mine"] == []  # input untouched


def test_scoring_plays_newest_first():
    drives = nfl.parse_drives(load("drives-final-401872981.json"), FINAL_IDS)
    sc = nfl.scoring_plays(drives)
    assert sc and all(p["scoring"] for p in sc)
    seq = [int(p["id"]) for p in sc]
    assert seq == sorted(seq, reverse=True)

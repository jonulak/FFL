import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ffl import nfl

FIX = Path(__file__).parent / "fixtures" / "nfl"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture(autouse=True)
def _nfl_reset():
    nfl.reset()
    yield
    nfl.reset()


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


def test_scoreboard_caches_then_serves_last_good_on_failure(monkeypatch):
    calls = []
    raw = load("scoreboard-w5.json")
    monkeypatch.setattr(nfl, "_get", lambda url, params=None: calls.append(params) or raw)
    first, stale = nfl.scoreboard(5)
    assert stale is None and first["week"] == 5 and calls == [{"week": 5, "seasontype": 2}]
    nfl.scoreboard(5)
    assert len(calls) == 1  # inside the TTL
    nfl.expire_all()
    monkeypatch.setattr(nfl, "_get", lambda url, params=None: (_ for _ in ()).throw(RuntimeError("down")))
    again, stale = nfl.scoreboard(5)
    assert again == first and stale is not None and len(stale) == 5  # "HH:MM"


def test_scoreboard_cold_failure_raises(monkeypatch):
    monkeypatch.setattr(nfl, "_get", lambda url, params=None: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(RuntimeError):
        nfl.scoreboard(None)


def test_drives_refetch_when_the_game_kicks_off(monkeypatch):
    calls = []
    monkeypatch.setattr(nfl, "_get", lambda url, params=None: calls.append(url) or {"items": []})
    assert nfl.drives("401872989", "pre", {})[0] == []
    nfl.drives("401872989", "pre", {})
    assert len(calls) == 1  # pregame: cached
    nfl.drives("401872989", "in", {})
    assert len(calls) == 2  # kickoff: the empty pregame feed is not served for the rest of its 10 min


def test_ttls():
    now = 1_000_000.0
    live = {"games": [{"status": "in", "kickoff": "2026-10-11T17:00Z"}]}
    assert nfl.scoreboard_ttl(live, now) == 20
    later = {"games": [{"status": "post", "kickoff": "x"}]}
    assert nfl.scoreboard_ttl(later, now) == 600
    assert nfl.drives_ttl("in") == 15 and nfl.drives_ttl("pre") == 600 and nfl.drives_ttl("post") == 86400


def test_scoreboard_ttl_wakes_for_next_kickoff():
    now = datetime(2026, 10, 11, 16, 58, tzinfo=timezone.utc).timestamp()
    sb = {"games": [{"status": "pre", "kickoff": "2026-10-11T17:00Z"}]}
    assert nfl.scoreboard_ttl(sb, now) == 125  # two minutes to kickoff, plus 5 s


def test_overlay_takes_feed_clock_and_score():
    g = {"away": {"abbr": "CHI", "score": 0}, "home": {"abbr": "GB", "score": 7}, "status": "in",
         "quarter": 1, "clock": "5:51", "ball": None, "red_zone": False, "down": "1st & 10 at CHI 33"}
    live = {"CHI": {"status": "in", "quarter": 2, "clock": "14:10", "score": {"CHI": 3, "GB": 7}, "ball": "GB", "red_zone": True}}
    out = nfl.overlay([g], live)[0]
    assert out["quarter"] == 2 and out["clock"] == "14:10" and out["away"]["score"] == 3
    assert out["ball"] == "GB" and out["red_zone"] is True
    assert out["down"] == "1st & 10 at CHI 33"  # the feed has no down; the scoreboard's stays
    assert g["quarter"] == 1  # input untouched
    assert nfl.overlay([g], {}) == [g]  # no feed: the scoreboard stands

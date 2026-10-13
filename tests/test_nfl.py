import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from espn_api.football.constant import PRO_TEAM_MAP

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
    # still to come in time order, the finished window last
    assert [(s["kickoff"], s["final"]) for s in out[1:]] == [
        ("2026-10-11T17:00Z", False), ("2026-10-11T20:25Z", False), ("2026-10-09T00:15Z", True)]
    assert [g["event_id"] for g in out[1]["games"]] == ["2", "3", "1"]  # mine, then opp only, then the rest
    assert out[1]["games"][0]["mine"] == 2 and out[1]["games"][1]["opp"] == 1


def test_board_finished_games_below_the_rest_earliest_first():
    games = [
        _g("1", "2026-10-09T00:15Z", "post", "TB", "DAL"),
        _g("2", "2026-10-11T13:30Z", "post", "NYJ", "DEN"),
        _g("3", "2026-10-11T17:00Z", "post", "CIN", "MIA"),
        _g("4", "2026-10-11T17:00Z", "pre", "MIN", "NO"),  # a delayed game stays with the games to come
        _g("5", "2026-10-13T00:15Z", "pre", "KC", "BUF"),
    ]
    out = nfl.board(games, {}, {})
    assert [(s["kickoff"], s["final"]) for s in out] == [
        ("2026-10-11T17:00Z", False), ("2026-10-13T00:15Z", False),
        ("2026-10-09T00:15Z", True), ("2026-10-11T13:30Z", True), ("2026-10-11T17:00Z", True)]


def test_board_no_live_section_when_nothing_on():
    out = nfl.board([_g("1", "2026-10-11T17:00Z", "pre", "NYG", "WSH")], {}, {})
    assert [s["live"] for s in out] == [False]


def test_board_time_tbd_window_sorts_last():
    out = nfl.board([_g("1", None, "pre", "NYG", "WSH"), _g("2", "2026-10-11T17:00Z", "pre", "CIN", "MIA")], {}, {})
    assert [s["kickoff"] for s in out] == ["2026-10-11T17:00Z", None]


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
    assert nfl.drives_ttl("in") == 15 and nfl.drives_ttl("pre") == 600 and nfl.drives_ttl("post") == 600
    assert nfl.drives_ttl("post", seen_final=True) == 86400


def test_scoreboard_ttl_wakes_for_next_kickoff():
    now = datetime(2026, 10, 11, 16, 58, tzinfo=timezone.utc).timestamp()
    sb = {"games": [{"status": "pre", "kickoff": "2026-10-11T17:00Z"}]}
    assert nfl.scoreboard_ttl(sb, now) == 125  # two minutes to kickoff, plus 5 s


def test_scoreboard_ttl_short_when_a_pre_game_is_past_kickoff():
    now = datetime(2026, 10, 11, 17, 1, tzinfo=timezone.utc).timestamp()
    sb = {"games": [{"status": "pre", "kickoff": "2026-10-11T17:00Z"}]}
    assert nfl.scoreboard_ttl(sb, now) == 20  # ESPN lags at kickoff: poll fast until it flips to "in"


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


def test_clubs_are_all_32():
    assert nfl.CLUBS == set(PRO_TEAM_MAP.values()) - {"None"}


def _ttl_left(key):
    return nfl._DRIVES._d[key][0] - time.time()


def test_first_final_fetch_lives_ten_minutes_then_a_day(monkeypatch):
    calls = []
    monkeypatch.setattr(nfl, "_get", lambda url, params=None: calls.append(url) or {"items": []})
    nfl.drives("1", "post", {})
    assert 590 < _ttl_left(("1", "post")) <= 600  # ESPN's drives may lag the flip: do not freeze them
    nfl.expire_all()
    nfl.drives("1", "post", {})
    assert len(calls) == 2 and 86000 < _ttl_left(("1", "post")) <= 86400


def test_final_drops_the_games_pre_and_in_entries(monkeypatch):
    monkeypatch.setattr(nfl, "_get", lambda url, params=None: {"items": []})
    nfl.drives("1", "pre", {})
    nfl.drives("1", "in", {})
    nfl.drives("2", "in", {})
    nfl.drives("1", "post", {})
    assert set(nfl._DRIVES._d) == {("1", "post"), ("2", "in")}


def test_failed_refetch_does_not_overwrite_a_newer_entry():
    c = nfl.TtlCache()
    c.get("k", lambda: {"v": 1}, lambda _: 0)  # expired at once

    def slow_fail():
        c.get("k", lambda: {"v": 2}, lambda _: 100)  # another request lands a newer answer meanwhile
        raise RuntimeError("down")
    data, stale = c.get("k", slow_fail, lambda _: 100)
    assert data == {"v": 2} and stale is None
    assert c.get("k", lambda: {"v": 3}, lambda _: 100) == ({"v": 2}, None)  # still fresh


def test_overlay_keeps_quarter_when_feed_has_none():
    g = {"away": {"abbr": "CHI", "score": 0}, "home": {"abbr": "GB", "score": 7}, "status": "in", "quarter": 3,
         "clock": "5:51", "ball": None, "red_zone": False, "down": None}
    live = {"CHI": {"status": "in", "quarter": None, "clock": "1:00", "score": {}}}
    assert nfl.overlay([g], live)[0]["quarter"] == 3


def test_is_live_from_fifteen_minutes_before_kickoff():
    now = datetime(2026, 10, 11, 16, 50, tzinfo=timezone.utc).timestamp()
    pre = lambda k: {"status": "pre", "kickoff": k}  # noqa: E731
    assert nfl.is_live(pre("2026-10-11T17:00Z"), now)
    assert not nfl.is_live(pre("2026-10-11T17:30Z"), now)
    assert nfl.is_live({"status": "in", "kickoff": None}, now)
    assert not nfl.is_live({"status": "post", "kickoff": "2026-10-11T17:00Z"}, now)
    assert not nfl.is_live(pre(None), now)


def test_shown_week_rolls_once_every_game_is_four_hours_old():
    sb = nfl.parse_scoreboard(load("scoreboard-w5.json"))  # last kickoff Mon 2026-10-13 00:15Z
    assert sb["season_type"] == 2
    assert nfl.shown_week(sb, datetime(2026, 10, 13, 2, 0, tzinfo=timezone.utc)) == 5  # Monday night still on
    assert nfl.shown_week(sb, datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)) == 6  # Tuesday: the league's week
    later = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    assert nfl.shown_week({**sb, "season_type": 3}, later) == 5  # a playoff week is not regular-season week n+1


def test_current_scoreboard_serves_the_rolled_week(monkeypatch):
    calls = []
    raw = load("scoreboard-w5.json")
    six = {**raw, "week": {**raw["week"], "number": 6}}
    monkeypatch.setattr(nfl, "_get", lambda url, params=None: calls.append(params) or (six if params else raw))
    monkeypatch.setattr(nfl, "shown_week", lambda sb, now=None: 6)
    sb, stale = nfl.scoreboard(None)
    assert sb["week"] == 6 and stale is None and calls == [None, {"week": 6, "seasontype": 2}]

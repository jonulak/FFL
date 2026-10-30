"""The /live deltas against the Python overlay, written for web/lib/overlay.test.ts: each case is a cached payload
served at three feed states, the deltas one stream sends between them (live.delta) and what the server's overlay
gives at each state. The TS overlay must turn any served payload plus the deltas since into the same answer.
FFL_WRITE_FIXTURES=1 rewrites the file; otherwise this fails when it is out of date."""
import json
import os
from pathlib import Path

from ffl import live

FIXTURES = Path(__file__).resolve().parents[1] / "web" / "lib" / "overlay.fixtures.json"
SCORING = [{"stat_id": 3, "label": "Passing Yards", "points": 0.04}, {"stat_id": 4, "label": "TD Pass", "points": 4.0},
           {"stat_id": 24, "label": "Rushing Yards", "points": 0.1}, {"stat_id": 42, "label": "Receiving Yards", "points": 0.1},
           {"stat_id": 53, "label": "Each reception", "points": 1.0, "by_pos": {"WR": 1.0, "RB": 1.0, "TE": 1.5}},
           {"stat_id": 83, "label": "FG Made", "points": 3.0}, {"stat_id": 99, "label": "Each Sack", "points": 1.0}]
SIGMA = {"QB": 8.0, "RB": 7.0, "WR": 6.5, "TE": 5.5, "K": 4.0, "D/ST": 5.5}
DST = -16002


def p(pid, pos, team, proj, **kw):
    return {"player_id": pid, "name": f"p{pid}", "slot": pos, "pos": pos, "team": team, "proj": proj, "actual": 0.0,
            "game_played": 0, "bye": False, "injury": "ACTIVE", **kw}


def side(team_id, lineup, bench=(), ir=()):
    return {"team_id": team_id, "lineup": list(lineup), "bench": list(bench), "ir": list(ir), "actual_total": 0.0}


ME = side(1, [p(1, "QB", "KC", 20.0), p(2, "WR", "BUF", 12.0), p(4, "K", "DAL", 8.0, game_played=100)],
          [p(3, "RB", "KC", 9.0)], [p(9, "TE", "NYG", 6.0, injury="INJURY_RESERVE")])
OPP = side(2, [p(5, "QB", "BUF", 18.0), p(6, "TE", "KC", 7.0), p(DST, "D/ST", "BUF", 6.0)],
           [p(7, "WR", "NYG", 5.0, bye=True)])

STATES = [  # (points, games, stats): kickoff in KC; then BUF on in the red zone; then KC over, BUF late
    ({1: 4.0, 4: 7.0},
     {"KC": {"status": "in", "quarter": 1, "clock": "9:12", "score": {"KC": 0, "LV": 0}, "ball": "KC", "red_zone": False},
      "LV": {"status": "in", "quarter": 1, "clock": "9:12", "score": {"KC": 0, "LV": 0}, "ball": "KC", "red_zone": False},
      "BUF": {"status": "pre", "quarter": None, "clock": None, "score": {}},
      "DAL": {"status": "post", "quarter": 4, "clock": "0:00", "score": {"DAL": 20, "NYG": 17}},
      "NYG": {"status": "post", "quarter": 4, "clock": "0:00", "score": {"DAL": 20, "NYG": 17}}},
     {1: {"0": 6.0, "1": 4.0, "3": 100.0}, 4: {"83": 2.0, "84": 2.0}}),
    ({1: 10.5, 4: 7.0, 2: 3.0, 6: 1.5, DST: 1.0},
     {"KC": {"status": "in", "quarter": 2, "clock": "3:40", "score": {"KC": 7, "LV": 3}, "ball": "LV", "red_zone": False},
      "LV": {"status": "in", "quarter": 2, "clock": "3:40", "score": {"KC": 7, "LV": 3}, "ball": "LV", "red_zone": False},
      "BUF": {"status": "in", "quarter": 1, "clock": "12:01", "score": {"BUF": 0, "MIA": 0}, "ball": "BUF", "red_zone": True},
      "MIA": {"status": "in", "quarter": 1, "clock": "12:01", "score": {"BUF": 0, "MIA": 0}, "ball": "BUF", "red_zone": True},
      "DAL": {"status": "post", "quarter": 4, "clock": "0:00", "score": {"DAL": 20, "NYG": 17}},
      "NYG": {"status": "post", "quarter": 4, "clock": "0:00", "score": {"DAL": 20, "NYG": 17}}},
     {1: {"0": 15.0, "1": 11.0, "3": 162.0, "4": 1.0}, 4: {"83": 2.0, "84": 2.0}, 2: {"53": 2.0, "58": 3.0, "42": 10.0},
      6: {"53": 1.0, "58": 1.0}, DST: {"99": 1.0}}),
    ({1: 24.0, 4: 7.0, 2: 9.5, 6: 1.5, DST: 3.0, 3: 6.5, 5: 12.25},
     {"KC": {"status": "post", "quarter": 4, "clock": "0:00", "score": {"KC": 27, "LV": 13}},
      "LV": {"status": "post", "quarter": 4, "clock": "0:00", "score": {"KC": 27, "LV": 13}},
      "BUF": {"status": "in", "quarter": 3, "clock": "1:15", "score": {"BUF": 14, "MIA": 10}, "ball": "MIA", "red_zone": False},
      "MIA": {"status": "in", "quarter": 3, "clock": "1:15", "score": {"BUF": 14, "MIA": 10}, "ball": "MIA", "red_zone": False},
      "DAL": {"status": "post", "quarter": 4, "clock": "0:00", "score": {"DAL": 20, "NYG": 17}},
      "NYG": {"status": "post", "quarter": 4, "clock": "0:00", "score": {"DAL": 20, "NYG": 17}}},
     {1: {"0": 30.0, "1": 22.0, "3": 300.0, "4": 3.0}, 4: {"83": 2.0, "84": 2.0}, 2: {"53": 5.0, "58": 8.0, "42": 45.0},
      6: {"53": 1.0, "58": 1.0}, DST: {"99": 3.0}, 3: {"23": 9.0, "24": 45.0, "53": 2.0, "58": 2.0, "42": 20.0},
      5: {"0": 20.0, "1": 12.0, "3": 155.0, "4": 1.0, "23": 2.0, "24": 14.0}}),
]
POSITIONS = {1: "QB", 2: "WR", 3: "RB", 4: "K", 5: "QB", 6: "TE", DST: "D/ST"}
WEEK = 7


def view(i):
    points, games, stats = STATES[i]
    return {"week": WEEK, "points": points, "games": games, "stats": stats, "positions": POSITIONS}


def tile(ids, lineup):
    starters = [{k: x[k] for k in ("player_id", "pos", "team", "proj", "actual", "game_played", "bye", "injury")}
                | {"kickoff": None} for x in lineup]
    teams = sorted({x["team"] for x in lineup})
    return {"team_id": ids, "score": 0.0, "proj": sum(x["proj"] for x in lineup), "me": ids == 1,
            "starter_ids": [x["player_id"] for x in lineup], "starter_teams": teams, "pending_teams": teams,
            "starters": starters}


RAW = {
    "week": {"week": WEEK, "live": True, "mine": True, "me": ME, "opp": OPP, "plays": [],
             "win_prob": {"me": 0.5, "opp": 0.5, "sigma_source": "calibrated"}},
    "roster": {"week": WEEK, "live": True, "cards": [*ME["lineup"], *ME["bench"], *ME["ir"]]},
    "team": {"week": WEEK, "live": True, "side": OPP},
    "league": {"week": WEEK, "live": True, "scoreboard": [
        {"away": tile(1, ME["lineup"]), "home": tile(2, OPP["lineup"]), "live": False, "final": False,
         "win_prob": {"away": 0.5, "home": 0.5}},
        {"away": tile(3, [p(8, "WR", "DAL", 10.0, game_played=100)]), "home": tile(4, [p(10, "RB", "NYG", 10.0, game_played=100)]),
         "live": False, "final": False, "win_prob": None}]},
}


def serve(kind, i):
    points, games, stats = STATES[i]
    raw = RAW[kind]
    if kind == "week":
        return live.overlay_week(raw, points, games, stats, SCORING, SIGMA)
    if kind == "roster":
        return live.overlay_roster(raw, points, games, stats, SCORING)
    if kind == "team":
        return {**raw, "side": live.overlay_side(raw["side"], points, games, stats, SCORING)}
    return live.overlay_league(raw, points, games, SIGMA)


def cases() -> dict:
    deltas, sent = [], live.opened(view(0))
    for i in (1, 2):
        d, sent = live.delta(sent, view(i), SCORING, SIGMA)
        deltas.append({"v": i, **d})
    out = {"deltas": deltas, "cases": [{"kind": k, "served": [serve(k, i) for i in range(3)]} for k in RAW]}
    return json.loads(json.dumps(out))  # int keys as the wire has them


def test_overlay_fixtures_are_current():
    want = json.dumps(cases(), indent=1, sort_keys=True) + "\n"
    if os.environ.get("FFL_WRITE_FIXTURES"):
        FIXTURES.write_text(want)
    assert FIXTURES.exists() and FIXTURES.read_text() == want, \
        "web/lib/overlay.fixtures.json is stale: FFL_WRITE_FIXTURES=1 uv run pytest tests/test_live_overlay_fixtures.py"


def test_the_first_delta_carries_every_game_and_sigma_but_only_what_moved():
    d, _ = live.delta(live.opened(view(0)), view(1), SCORING, SIGMA)
    assert set(d["games"]) == set(STATES[1][1]) and d["sigma"] == SIGMA
    assert d["points"] == {1: 10.5, 2: 3.0, 6: 1.5, DST: 1.0}, "the kicker's 7 stood when the stream opened"
    assert set(d["players"]) == {1, 2, 6, DST}
    assert d["players"][2]["stat_line"] == "2 rec, 10 yds" and d["players"][2]["volume"] == {"count": 3, "label": "targets"}


def test_a_later_delta_carries_only_the_changes_and_no_sigma():
    _, sent = live.delta(live.opened(view(0)), view(1), SCORING, SIGMA)
    d, _ = live.delta(sent, view(2), SCORING, SIGMA)
    assert "sigma" not in d and "DAL" not in d["games"] and 4 not in d["points"]


def test_a_new_week_sends_the_week_alone_and_none_without_one():
    d, sent = live.delta(live.opened(view(0)), {**view(1), "week": WEEK + 1}, SCORING, SIGMA)
    assert d == {"week": WEEK + 1} and sent["games"] == {} and sent["sigma"] is None
    assert live.delta(sent, {**view(1), "week": None}, SCORING, SIGMA) == (None, sent)


def test_stream_lines_send_a_delta_in_place_of_the_bare_version():
    assert live.stream_lines((3, 1), (4, 2), {"week": 7, "points": {1: 2.0}}) == [
        "data: m2\n\n", 'data: {"v":4,"week":7,"points":{"1":2.0}}\n\n']
    assert live.stream_lines((3, 1), (3, 2), {"week": 7}) == ["data: m2\n\n"], "no league tick, no delta"


def _stream(monkeypatch, state, moves=False):
    """/live's body as the route builds it: the opening chunk, then the chunk after `then` updates `state` (the
    second read blocks until the update has ticked)."""
    from types import SimpleNamespace

    import anyio

    from ffl import api
    monkeypatch.setattr(live, "STATE", state)
    monkeypatch.setattr(api, "open_league", lambda request, league: SimpleNamespace(key="espn:1"))
    monkeypatch.setattr(api, "_scoring", lambda ref: None)
    monkeypatch.setattr(api, "_sigma", lambda: SIGMA)
    body = api.live_stream(None, None, moves).body_iterator

    async def read(then):
        first = await body.__anext__()
        then()
        return first, await body.__anext__()
    return lambda then: anyio.run(read, then)


def test_the_live_route_sends_its_leagues_delta_and_moves_only_streams_none(monkeypatch):
    st = live.State()
    st.update({"espn:1": ({1: 2.0}, WEEK)}, {}, {1: {"53": 1.0}}, positions={1: "WR"})
    tick = lambda: st.update({"espn:1": ({1: 5.0}, WEEK)}, {}, {1: {"53": 2.0}}, positions={1: "WR"})  # noqa: E731
    first, second = _stream(monkeypatch, st)(tick)
    assert first.startswith("retry: 3000\ndata: 1 m0")
    d = json.loads(second.removeprefix("data: "))
    assert d["v"] == 2 and d["points"] == {"1": 5.0} and d["players"]["1"]["stat_line"] == "2 rec" and d["sigma"] == SIGMA

    st = live.State()
    st.update({"espn:1": ({1: 2.0}, WEEK)})
    _, moved = _stream(monkeypatch, st, moves=True)(st.moved)
    assert moved == "data: m1\n\n", "a moves-only stream hears no league's points"

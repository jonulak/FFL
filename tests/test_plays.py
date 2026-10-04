"""Play-feed events derived from a rescoring, and the per-week store."""
import json
from datetime import datetime

from ffl import plays

LEAGUE = {"week": 1, "games": [[{"team_id": 7, "lineup": [{"player_id": 1, "name": "Me QB", "pos": "QB", "team": "SF"}]},
                                {"team_id": 3, "lineup": [{"player_id": 2, "name": "Their WR", "pos": "WR", "team": "CHI"}]}],
                               [{"team_id": 5, "lineup": [{"player_id": 4, "name": "Other RB", "pos": "RB", "team": "DET"}]}]]}
DOC = {"statistics": {"1": {"gameId": "g1", "stats": {}}, "2": {"gameId": "g2", "stats": {}}},
       "events": [{"id": "g1", "period": 1, "clock": "4:12", "lastPlay": {"playId": "p1", "text": " J.Doe pass to X for 12 yards. "}},
                  {"id": "g2", "period": 2, "clock": "0:30", "lastPlay": {}}]}
AT = datetime(2026, 9, 13, 13, 5, 0)


def test_roster_index_covers_every_game_with_the_fantasy_team():
    idx = plays.roster_index(LEAGUE)
    assert set(idx) == {1, 2, 4}
    assert idx[1] == {"name": "Me QB", "pos": "QB", "team": "SF", "team_id": 7}
    assert idx[4]["team_id"] == 5, "another game's starter is followed too"


def test_for_board_sides_rows_by_the_left_team():
    rows = [{"team_id": 7, "side": "opp"}, {"team_id": 3}]
    assert [r["side"] for r in plays.for_board(rows, 7)] == ["me", "opp"]
    assert [r["side"] for r in plays.for_board(rows, 3)] == ["opp", "me"], "another game reads from its own left"


def test_store_latest_filters_to_one_boards_teams(tmp_path):
    st = plays.Store(tmp_path / "p.jsonl")
    st.add([{"at": "a", "team_id": 7}, {"at": "b", "team_id": 5}, {"at": "c", "team_id": 3}, {"at": "d", "team_id": 5}])
    assert [r["at"] for r in st.latest(teams={7, 3})] == ["c", "a"]
    assert [r["at"] for r in st.latest(1, teams={5})] == ["d"]


def test_events_from_reports_each_moved_starter_with_play_text():
    rows = plays.events_from({1: 10.0}, {1: 11.2, 2: 0.5, 9: 4.0}, DOC, plays.roster_index(LEAGUE), AT, 1)
    assert [(r["player_id"], r["delta"], r["total"]) for r in rows] == [(1, 1.2, 11.2), (2, 0.5, 0.5)]
    me = rows[0]
    assert me["text"] == "J.Doe pass to X for 12 yards." and me["quarter"] == 1 and me["clock"] == "4:12"
    assert "side" not in me and me["team_id"] == 7 and me["at"] == "2026-09-13T13:05:00" and me["week"] == 1
    assert rows[1]["text"] is None, "a game with no last play: delta alone"


def test_events_from_skips_unmoved_and_unknown_players():
    rows = plays.events_from({1: 10.0, 2: 3.0}, {1: 10.0, 2: 3.0, 5: 9.0}, DOC, plays.roster_index(LEAGUE), AT, 1)
    assert rows == []


def test_store_appends_and_reloads_newest_first(tmp_path):
    path = tmp_path / "plays-week-1.jsonl"
    st = plays.Store(path)
    st.add([{"at": "a", "delta": 1.0}, {"at": "b", "delta": 2.0}])
    st.add([{"at": "c", "delta": 3.0}])
    assert [r["at"] for r in st.latest(2)] == ["c", "b"]
    again = plays.Store(path)
    assert [r["at"] for r in again.latest()] == ["c", "b", "a"]
    assert len(path.read_text().splitlines()) == 3


def test_store_add_nothing_writes_nothing(tmp_path):
    path = tmp_path / "plays-week-1.jsonl"
    plays.Store(path).add([])
    assert not path.exists()


def test_week_path_is_under_data_live():
    assert plays.week_path(3).name == "plays-week-3.jsonl" and plays.week_path(3).parent.name == "live"


# The stats patch for a play arrives a frame or three after the play text, by which time the
# game's lastPlay is often the *next* play (a timeout after a field goal, say). The doc names
# the players of every play in the current drive and every scoring play, so an event is
# matched to the newest remembered play that names the player, not to whatever is last.
FG = {"playId": "4018729221280", "text": " C.Little 47 yard field goal is GOOD.", "period": 2, "clock": 560.0,
      "scoringPlay": True, "players": [{"playerId": "40", "type": "kicker"}, {"playerId": "40", "type": "scorer"}]}
TIMEOUT = {"playId": "4018729221287", "text": "Official Timeout at 09:09.", "period": 2, "clock": 549.0, "players": []}
KICKOFF = {"playId": "4018729221297", "text": " C.Little kicks 60 yards.", "period": 2, "clock": 549.0,
           "players": [{"playerId": "40", "type": "kicker"}, {"playerId": "41", "type": "returner"}]}
ROSTER = {40: {"name": "Cam Little", "pos": "K", "team": "JAX", "side": "me", "team_id": 7}}


def _doc(last, drive_plays, scoring=()):
    return {"statistics": {"40": {"gameId": "g", "stats": {}}},
            "events": [{"id": "g", "period": 2, "clock": "9:09", "lastPlay": last,
                        "drive": {"plays": list(drive_plays)}, "scoringPlays": list(scoring)}]}


def test_event_matches_the_newest_play_naming_the_player_not_the_last_play():
    cache = plays.PlayCache()
    cache.remember(_doc(FG, [FG]))                      # the field goal is the last play, ends the drive
    cache.remember(_doc(TIMEOUT, [], [FG]))             # new drive: the FG survives only in scoringPlays
    rows = plays.events_from({40: 2.0}, {40: 6.0}, _doc(TIMEOUT, [], [FG]), ROSTER, AT, 1, cache)
    assert rows[0]["text"] == "C.Little 47 yard field goal is GOOD." and rows[0]["play_id"] == FG["playId"]
    assert rows[0]["quarter"] == 2 and rows[0]["clock"] == "9:20" and rows[0]["match"] == "player"


def test_event_falls_back_to_the_last_play_when_no_play_names_the_player():
    cache = plays.PlayCache()
    doc = _doc(TIMEOUT, [TIMEOUT])
    cache.remember(doc)
    rows = plays.events_from({40: 2.0}, {40: 3.0}, doc, ROSTER, AT, 1, cache)
    assert rows[0]["text"] == "Official Timeout at 09:09." and rows[0]["match"] == "last"


def test_cache_orders_by_play_id_across_drive_and_scoring_lists():
    cache = plays.PlayCache()
    cache.remember(_doc(KICKOFF, [KICKOFF], [FG]))
    assert cache.play_for("g", 40)["playId"] == KICKOFF["playId"]
    assert cache.play_for("g", 41)["playId"] == KICKOFF["playId"]
    assert cache.play_for("g", 99) is None and cache.play_for("nope", 40) is None


def test_cache_forgets_old_plays_per_game():
    cache = plays.PlayCache(keep=2)
    cache.remember(_doc(FG, [FG, TIMEOUT, KICKOFF]))
    assert cache.play_for("g", 40)["playId"] == KICKOFF["playId"]
    assert len(cache.plays["g"]) == 2 and FG["playId"] not in cache.plays["g"]

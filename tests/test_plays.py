"""Play-feed events derived from a rescoring, and the per-week store."""
import json
from datetime import datetime

from ffl import db, plays

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


def _row(at, team_id, total=1.0, delta=1.0, week=1):
    return {"at": at, "week": week, "player_id": 1, "team_id": team_id, "total": total, "delta": delta}


def test_store_latest_filters_to_one_boards_teams():
    st = plays.Store(1)
    st.add([_row("a", 7), _row("b", 5), _row("c", 3), _row("d", 5)])
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


def test_store_appends_and_reloads_newest_first():
    st = plays.Store(1)
    st.add([_row("a", 7, 1.0), _row("b", 7, 3.0)])
    st.add([_row("c", 7, 6.0)])
    assert [r["at"] for r in st.latest(2)] == ["c", "b"]
    again = plays.Store(1)  # a fresh boot reads the DB
    assert [r["at"] for r in again.latest()] == ["c", "b", "a"]
    assert again.latest() == st.latest()
    assert plays.Store(2).latest() == [], "another week's feed is separate"


def test_store_add_nothing_writes_nothing():
    plays.Store(1).add([])
    assert db.plays_rows(1) == []


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


# A play names the defenders, never the D/ST, so a D/ST row is matched by which of its stats
# moved: a sack and fumble recovery was once captioned with the incompletion after it.
STRIP = {"playId": "4018729741650", "text": "M.Willis sacked at MIA 30 for -6 yards (D.Turner). FUMBLES (D.Turner), "
         "RECOVERED by MIN-J.Metellus at MIA 28.", "period": 2, "clock": 380.0, "start": {"team": "15"},
         "players": [{"playerId": "777", "type": "sackedBy"}]}
INCOMPLETE = {"playId": "4018729741684", "text": "(Shotgun) M.Willis pass incomplete short middle to M.Washington.",
              "period": 2, "clock": 350.0, "start": {"team": "15"}, "players": []}
OWN_SACK = {"playId": "4018729741700", "text": "S.Darnold sacked at MIN 20 for -5 yards (J.Phillips).", "period": 2,
            "clock": 300.0, "start": {"team": "16"}, "players": []}
TD_ALLOWED = {"playId": "4018729741720", "text": "D.Achane 12 yard run, TOUCHDOWN.", "period": 2, "clock": 200.0,
              "scoringPlay": True, "start": {"team": "15"}, "players": []}
DST = {-16016: {"name": "Vikings D/ST", "pos": "D/ST", "team": "MIN", "team_id": 7}}


def _dst_doc(last, drive_plays, stats, scoring=()):
    return {"statistics": {"-16016": {"gameId": "g", "proTeamId": "16", "stats": stats}},
            "events": [{"id": "g", "period": 2, "clock": "5:50", "lastPlay": last,
                        "drive": {"plays": list(drive_plays)}, "scoringPlays": list(scoring)}]}


def test_dst_event_takes_the_play_its_moved_stats_describe_not_the_last_play():
    cache = plays.PlayCache()
    cache.remember(_dst_doc(STRIP, [STRIP], {}))
    doc = _dst_doc(INCOMPLETE, [INCOMPLETE, OWN_SACK], {"99": 2.0, "96": 1.0})
    cache.remember(doc)
    rows = plays.events_from({-16016: 11.0}, {-16016: 14.0}, doc, DST, AT, 4, cache, {-16016: {"99": 1.0}})
    assert rows[0]["play_id"] == STRIP["playId"] and rows[0]["match"] == "defence", \
        "the strip sack, not the incompletion after it nor the Vikings' own QB sacked"
    assert rows[0]["clock"] == "6:20"


def test_dst_points_allowed_takes_the_opponents_scoring_play():
    cache = plays.PlayCache()
    doc = _dst_doc(INCOMPLETE, [INCOMPLETE], {"120": 7.0}, [TD_ALLOWED])
    cache.remember(doc)
    rows = plays.events_from({-16016: 14.0}, {-16016: 11.0}, doc, DST, AT, 4, cache, {-16016: {"120": 0.0}})
    assert rows[0]["play_id"] == TD_ALLOWED["playId"] and rows[0]["match"] == "defence"


def test_dst_yardage_band_alone_falls_back_to_the_last_play():
    cache = plays.PlayCache()
    doc = _dst_doc(INCOMPLETE, [STRIP, INCOMPLETE], {"127": 101.0, "128": 0, "129": 1})
    cache.remember(doc)
    rows = plays.events_from({-16016: 14.0}, {-16016: 13.0}, doc, DST, AT, 4, cache,
                             {-16016: {"127": 96.0, "128": 1, "129": 0}})
    assert rows[0]["play_id"] == INCOMPLETE["playId"] and rows[0]["match"] == "last"

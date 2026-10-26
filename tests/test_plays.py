"""Play-feed events derived from a rescoring, and the per-week store."""
import json
from datetime import datetime

import pytest

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


def test_store_reads_and_writes_its_own_league():
    from ffl import leagues
    other = leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, False, None, None))
    db.put_called_back([{"play_id": "p1", "player_id": 2, "week": 1, "who": "other"}], [], ref=other)
    st = plays.Store(1, ref=other)
    st.add([_row("a", 3, 1.0)])
    assert [r["at"] for r in db.plays_rows(1, ref=other)] == ["a"] and db.plays_rows(1) == []
    assert plays.Store(1).latest() == [], "home's feed is its own"
    assert plays.Store(1, ref=other).called == {("p1", 2): {"play_id": "p1", "player_id": 2, "week": 1, "who": "other"}}
    assert plays.Store(1).called == {}


def test_store_keeps_rows_in_memory_when_the_db_write_fails(monkeypatch):
    def boom(rows, **k):
        raise RuntimeError("locked")
    monkeypatch.setattr(db, "add_plays", boom)
    st = plays.Store(1)
    with pytest.raises(RuntimeError):
        st.add([_row("a", 7)])
    assert [r["at"] for r in st.latest()] == ["a"]


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


# Points taken back: a play the feed credited is erased (a penalty, a replay reversal, a stat
# correction). Counting stats never fall in a game, so one falling is the tell; so is points
# falling while no stat rose (yards trimmed back to a foul spot). Purdy once lost an 11-yard
# completion captioned with the incompletion after it.
QB = {1: {"name": "Brock Purdy", "pos": "QB", "team": "SF", "team_id": 7}}
CALLED_BACK = {"playId": "4018729583696", "text": "B.Purdy pass short left to D.Samuel for 11 yards. "
               "PENALTY on SF-T.Williams, Offensive Holding, 10 yards, enforced at ARZ 25 - No Play.",
               "period": 4, "clock": 300.0, "players": [{"playerId": "1", "type": "passer"}]}
NEXT = {"playId": "4018729583753", "text": "B.Purdy pass incomplete short left to K.Juszczyk.", "period": 4,
        "clock": 290.0, "players": [{"playerId": "1", "type": "passer"}]}
KNEEL = {"playId": "4018729584716", "text": "B.Purdy kneels to ARZ 46 for -1 yards.", "period": 4, "clock": 40.0,
         "players": [{"playerId": "1", "type": "rusher"}]}


def _qb_doc(last, drive_plays, stats):
    return {"statistics": {"1": {"gameId": "g", "stats": stats}},
            "events": [{"id": "g", "period": 4, "clock": "4:50", "lastPlay": last, "drive": {"plays": list(drive_plays)}}]}


def test_a_counting_stat_falling_is_a_takeback_captioned_with_the_play_called_back():
    cache = plays.PlayCache()
    doc = _qb_doc(NEXT, [CALLED_BACK, NEXT], {"0": 21.0, "1": 14.0, "3": 189.0})
    cache.remember(doc)
    rows = plays.events_from({1: 18.68}, {1: 18.24}, doc, QB, AT, 3, cache, {1: {"0": 21.0, "1": 15.0, "3": 200.0}})
    assert rows[0]["match"] == "reversed" and rows[0]["play_id"] == CALLED_BACK["playId"]
    assert rows[0]["clock"] == "5:00"


def test_a_takeback_with_no_called_back_play_has_no_caption():
    cache = plays.PlayCache()
    doc = _qb_doc(NEXT, [NEXT], {"0": 21.0, "1": 14.0, "3": 189.0})
    cache.remember(doc)
    rows = plays.events_from({1: 18.68}, {1: 18.24}, doc, QB, AT, 3, cache, {1: {"0": 21.0, "1": 15.0, "3": 200.0}})
    assert rows[0]["match"] == "reversed" and rows[0]["text"] is None and rows[0]["play_id"] is None


# Purdy to Evans, week 6: credited at 55 yards, the ball re-spotted ten seconds later at 54.
LONG = {"playId": "401873008460", "text": "(Shotgun) B.Purdy pass deep right to M.Evans pushed ob at WAS 17 for 55 "
        "yards (N.Cross).", "period": 1, "clock": 523.0, "players": [{"playerId": "1", "type": "passer"}]}
TRIMMED = {"playId": "4018729582302", "text": "K.Williams left end pushed ob at PHI 23 for 7 yards (J.Jones).\n"
           "PENALTY on LA-P.Nacua, Illegal Blindside Block, 22 yards, enforced at PHI 23.", "period": 2,
           "clock": 100.0, "players": [{"playerId": "1", "type": "rusher"}]}


def test_yards_moving_with_no_count_is_a_stat_fix_either_way():
    cache = plays.PlayCache()
    doc = _qb_doc(LONG, [LONG], {"0": 21.0, "1": 15.0, "3": 199.0})
    cache.remember(doc)
    rows = plays.events_from({1: 8.24}, {1: 8.2}, doc, QB, AT, 6, cache, {1: {"0": 21.0, "1": 15.0, "3": 200.0}})
    assert rows[0]["match"] == "adjusted" and rows[0]["play_id"] == LONG["playId"]
    rows = plays.events_from({1: 8.2}, {1: 8.24}, doc, QB, AT, 6, cache, {1: {"0": 21.0, "1": 15.0, "3": 199.0}})
    assert rows[0]["match"] == "adjusted", "a re-spot can add a yard too"


def test_yards_trimmed_on_a_flagged_play_are_taken_back_with_that_play():
    cache = plays.PlayCache()
    doc = _qb_doc(TRIMMED, [TRIMMED], {"23": 9.0, "24": 40.0})
    cache.remember(doc)
    rows = plays.events_from({1: 12.0}, {1: 11.3}, doc, QB, AT, 4, cache, {1: {"23": 9.0, "24": 47.0}})
    assert rows[0]["match"] == "reversed" and rows[0]["play_id"] == TRIMMED["playId"]


def test_a_new_play_is_never_a_revision():
    cache = plays.PlayCache()
    doc = _qb_doc(LONG, [LONG], {"0": 22.0, "1": 16.0, "3": 255.0})
    cache.remember(doc)
    rows = plays.events_from({1: 6.04}, {1: 8.24}, doc, QB, AT, 6, cache, {1: {"0": 21.0, "1": 15.0, "3": 200.0}})
    assert rows[0]["match"] == "player"


# Smith-Njigba, week 7: "pass short left to J.Smith-Njigba ... for 8 yards", re-worded 20 s later "J.Smith-Njigba
# left end ... for 8 yards". The catch's point went, the yards stayed as a run: a stat fix, not a takeback.
RERULED = {"playId": "4018730221420", "text": "(Shotgun) J.Smith-Njigba left end pushed ob at SEA 48 for 8 yards "
           "(J.Hicks).", "period": 2, "clock": 323.0, "players": [{"playerId": "1", "type": "rusher"}]}


def test_a_catch_re_ruled_a_run_is_a_stat_fix_either_way():
    cache = plays.PlayCache()
    doc = _qb_doc(RERULED, [RERULED], {"53": 4.0, "42": 87.0, "23": 1.0, "24": 8.0})
    cache.remember(doc)
    rows = plays.events_from({1: 10.5}, {1: 9.5}, doc, QB, AT, 7, cache, {1: {"53": 5.0, "42": 95.0}})
    assert rows[0]["match"] == "adjusted"
    rows = plays.events_from({1: 9.5}, {1: 10.5}, doc, QB, AT, 7, cache,
                             {1: {"53": 3.0, "42": 79.0, "23": 1.0, "24": 16.0}})
    assert rows[0]["match"] == "adjusted", "a run ruled a catch (a forward pitch) is one too"
    rows = plays.events_from({1: 10.5}, {1: 9.5}, doc, QB, AT, 7, cache, {1: {"53": 5.0, "42": 87.0, "23": 0.0, "24": 8.0}})
    assert rows[0]["match"] == "adjusted", "a no-gain catch re-ruled moves the carry, not yards"


# Brissett to Knight, week 7: a catch for -1 yards re-ruled a run. Knight is not a starter here; his row in the
# doc is what tells Brissett's lost completion from one overturned to an incompletion.
KNIGHT = {"playId": "4018730191235", "text": "(Shotgun) Z.Knight right end to DEN 11 for -1 yards (D.Tillman, Z.Allen).",
          "period": 2, "clock": 900.0, "players": [{"playerId": "2", "type": "rusher"}]}


def _pair_doc(qb, rec, team=22):
    return {"statistics": {"1": {"gameId": "g", "proTeamId": team, "stats": qb},
                           "2": {"gameId": "g", "proTeamId": 22, "stats": rec}},
            "events": [{"id": "g", "period": 1, "clock": "14:24", "lastPlay": NEXT, "drive": {"plays": [KNIGHT, NEXT]}}]}


def test_a_pass_re_ruled_a_run_is_a_stat_fix_for_the_passer_too():
    cache = plays.PlayCache()
    before = {1: {"0": 5.0, "1": 4.0, "3": 30.0}, 2: {"53": 1.0, "42": -1.0}}
    doc = _pair_doc({"0": 4.0, "1": 3.0, "3": 31.0}, {"53": 0.0, "42": 0.0, "23": 1.0, "24": -1.0})
    cache.remember(doc)
    rows = plays.events_from({1: 1.2}, {1: 1.24}, doc, QB, AT, 7, cache, before)
    assert rows[0]["match"] == "adjusted" and rows[0]["play_id"] == KNIGHT["playId"], "captioned with the catch re-ruled"
    doc = _pair_doc({"0": 4.0, "1": 3.0, "3": 31.0}, {"53": 0.0, "42": 0.0, "23": 1.0, "24": -1.0}, team=9)
    rows = plays.events_from({1: 1.2}, {1: 1.24}, doc, QB, AT, 7, cache, before)
    assert rows[0]["match"] != "adjusted", "the other club's catch re-ruled is not his"


def test_a_completion_overturned_stays_called_back_beside_a_plain_catch_erased():
    cache = plays.PlayCache()
    before = {1: {"0": 5.0, "1": 4.0, "3": 46.0}, 2: {"53": 1.0, "42": 16.0}}
    doc = _pair_doc({"0": 5.0, "1": 3.0, "3": 30.0}, {"53": 0.0, "42": 0.0})  # McBride's catch ruled incomplete
    cache.remember(doc)
    rows = plays.events_from({1: 1.84}, {1: 1.2}, doc, QB, AT, 7, cache, before)
    assert rows[0]["match"] == "reversed"


def test_a_catch_erased_with_its_yards_is_still_a_takeback():
    cache = plays.PlayCache()
    doc = _qb_doc(NEXT, [NEXT], {"53": 4.0, "42": 79.0})
    cache.remember(doc)
    rows = plays.events_from({1: 10.5}, {1: 8.7}, doc, QB, AT, 7, cache, {1: {"53": 5.0, "42": 87.0}})
    assert rows[0]["match"] == "reversed"
    rows = plays.events_from({1: 9.9}, {1: 8.9}, doc, QB, AT, 7, cache, {1: {"53": 5.0, "42": 79.0}})
    assert rows[0]["match"] == "reversed", "a no-gain catch erased: no carry came"


def test_a_play_for_lost_yards_is_a_loss_not_a_takeback():
    cache = plays.PlayCache()
    doc = _qb_doc(KNEEL, [KNEEL], {"23": 3.0, "24": 10.0})
    cache.remember(doc)
    rows = plays.events_from({1: 31.38}, {1: 31.28}, doc, QB, AT, 3, cache, {1: {"23": 2.0, "24": 11.0}})
    assert rows[0]["match"] == "player" and rows[0]["play_id"] == KNEEL["playId"]


def test_a_loss_whose_yards_land_before_its_carry_is_a_loss_not_a_takeback():
    cache = plays.PlayCache()
    doc = _qb_doc(KNEEL, [KNEEL], {"23": 2.0, "24": 10.0})  # the yards frame; the carry follows
    cache.remember(doc)
    rows = plays.events_from({1: 31.38}, {1: 31.28}, doc, QB, AT, 3, cache, {1: {"23": 2.0, "24": 11.0}})
    assert rows[0]["match"] == "player"


# Shough, week 7: sacked and stripped, the fumble lost (-2) overturned on review ten seconds later. The
# REVERSED wording lands some 40 s after the stats, so the caption is the play naming him, by its id.
OVERTURNED = {"playId": "4018730111090", "text": "(Shotgun) T.Shough sacked at PIT 26 for -9 yards (T.Watt). FUMBLES "
              "(T.Watt) [T.Watt], RECOVERED by PIT-T.Watt at PIT 26.", "period": 2, "clock": 673.0,
              "players": [{"playerId": "1", "type": "passer"}]}


def test_a_turnover_overturned_gives_points_back_as_a_takeback():
    cache = plays.PlayCache()
    doc = _qb_doc(OVERTURNED, [OVERTURNED], {"0": 21.0, "72": 0.0})
    cache.remember(doc)
    rows = plays.events_from({1: 1.96}, {1: 3.96}, doc, QB, AT, 7, cache, {1: {"0": 21.0, "72": 1.0}})
    assert rows[0]["match"] == "reversed" and rows[0]["play_id"] == OVERTURNED["playId"]
    doc = _qb_doc(NEXT, [NEXT], {"0": 21.0, "20": 0.0})  # an interception overturned to an incompletion
    cache.remember(doc)
    rows = plays.events_from({1: 16.0}, {1: 18.0}, doc, QB, AT, 3, cache, {1: {"0": 21.0, "20": 1.0}})
    assert rows[0]["match"] == "reversed"


def test_a_gain_with_a_count_rising_is_a_new_play_even_when_another_falls():
    cache = plays.PlayCache()
    doc = _qb_doc(LONG, [LONG], {"0": 22.0, "1": 16.0, "20": 0.0})  # one frame: an INT erased and a catch
    cache.remember(doc)
    rows = plays.events_from({1: 16.0}, {1: 20.0}, doc, QB, AT, 3, cache, {1: {"0": 21.0, "1": 15.0, "20": 1.0}})
    assert rows[0]["match"] == "player"


def test_a_dst_sack_taken_back_is_a_takeback():
    cache = plays.PlayCache()
    doc = _dst_doc(INCOMPLETE, [INCOMPLETE], {"99": 1.0})
    rows = plays.events_from({-16016: 12.0}, {-16016: 11.0}, doc, DST, AT, 4, cache, {-16016: {"99": 2.0}})
    assert rows[0]["match"] == "reversed"


def test_points_allowed_is_a_loss_not_a_takeback():
    cache = plays.PlayCache()
    doc = _dst_doc(INCOMPLETE, [INCOMPLETE], {"120": 7.0}, [TD_ALLOWED])
    cache.remember(doc)
    rows = plays.events_from({-16016: 14.0}, {-16016: 11.0}, doc, DST, AT, 4, cache, {-16016: {"120": 0.0}})
    assert rows[0]["match"] == "defence"


def _old(pid, pos, delta, text, play_id, at="2026-10-19T20:33:37"):
    return {"player_id": pid, "pos": pos, "delta": delta, "text": text, "play_id": play_id, "at": at,
            "match": "player", "week": 6, "total": 1.0}


def test_old_rows_are_retagged_only_where_the_text_is_plain():
    catch = "B.Purdy pass deep right to M.Evans pushed ob at WAS 17 for 55 yards (N.Cross)."
    flagged = "K.Williams left end to PHI 23 for 7 yards. PENALTY on LA-P.Nacua, Illegal Blindside Block."
    rows = [_old(1, "QB", 2.2, catch, "c"), _old(2, "WR", 6.5, catch, "c"),
            _old(1, "QB", -0.04, catch, "c", "2026-10-19T20:33:47"), _old(2, "WR", -0.1, catch, "c", "2026-10-19T20:33:47"),
            _old(3, "K", 6.0, "J.Williams up the middle for 1 yard, TOUCHDOWN.", "td"),
            _old(3, "K", 1.0, "J.Williams up the middle for 1 yard, TOUCHDOWN.", "td"),       # the extra point
            _old(4, "QB", 0.44, "B.Purdy pass short left to D.Samuel for 11 yards.", "inc"),
            _old(4, "QB", -0.44, "B.Purdy pass incomplete short left to K.Juszczyk.", "inc"),  # no yards in the text
            _old(5, "RB", 2.0, flagged, "blk"), _old(5, "RB", -0.4, flagged, "blk"),
            _old(6, "WR", 1.5, "J.Love pass to T.Kraft for 5 yards.", "big"),
            _old(6, "WR", -1.5, "J.Love pass to T.Kraft for 5 yards.", "big"),                # too big to call
            _old(7, "RB", 0.3, "C.Brown left guard to MIA 42 for 3 yards.", "two"),
            _old(7, "RB", 0.3, "C.Brown left guard to MIA 42 for 3 yards.", "two", "2026-10-19T20:33:38"),  # a second carry
            _old(8, "RB", 0.4, "T.Pollard up the middle for 4 yards.", "late"),
            _old(8, "RB", -0.1, "T.Pollard up the middle for 4 yards.", "late", "2026-10-19T20:34:37")]  # a minute on
    out = plays.old_revisions(rows)
    assert [(r["player_id"], r["match"]) for r in out] == [(1, "adjusted"), (2, "adjusted"), (5, "reversed")]

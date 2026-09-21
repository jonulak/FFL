"""STRATEGY.md "Recent form": the last three games he played, as facts beside `add`."""
from ffl import season


def G(week, pts, proj, opp=10.0, tgt=0.0, rec_yd=0.0, snaps=None, team_snaps=None, final=True, active=None):
    return {"week": week, "pts": pts, "proj": proj, "opp": opp, "tgt": tgt, "rec_yd": rec_yd,
            "snaps": snaps, "team_snaps": team_snaps, "final": final,
            "active": (opp > 0) if active is None else active}


def test_window_is_the_last_three_games_played():
    games = [G(1, 10, 8), G(2, 12, 9), G(3, 0, 9, opp=0), G(4, 20, 10), G(5, 14, 11)]
    f = season.recent_form(games, "RB", ros=100.0, games_left=13)
    assert f["games"] == 3 and f["weeks"] == [2, 4, 5], "week 3 (no opportunity: bye or out) is not a game"
    assert f["l3"] == round((12 + 20 + 14) / 3, 1)
    assert f["beat"] == round(((12 - 9) + (20 - 10) + (14 - 11)) / 3, 1)


def test_short_window_carries_its_count_and_none_is_none():
    f = season.recent_form([G(1, 2.8, 12.0), G(2, 4.5, 9.6)], "RB", ros=132.4, games_left=16)
    assert f["games"] == 2 and f["l3"] == round((2.8 + 4.5) / 2, 1) and f["beat"] == round(((2.8 - 12) + (4.5 - 9.6)) / 2, 1)
    assert season.recent_form([], "RB", ros=50.0, games_left=16) is None
    assert season.recent_form([G(1, 0, 8, opp=0)], "WR", ros=50.0, games_left=16) is None, "never played: nothing"


def test_a_game_in_progress_is_not_in_the_window():
    games = [G(1, 10, 8), G(2, 6, 9, final=False)]
    f = season.recent_form(games, "RB", ros=100.0, games_left=16)
    assert f["weeks"] == [1], "partial stats would read as a bad game"


def test_returning_player_window_is_his_games_since_he_came_back():
    games = [G(1, 15, 12), G(2, 0, 0, opp=0), G(3, 0, 0, opp=0), G(4, 0, 0, opp=0), G(5, 9, 7)]
    f = season.recent_form(games, "WR", ros=80.0, games_left=13)
    assert f["weeks"] == [1, 5] and f["games"] == 2, "the weeks he was out are not games; week 1 still counts"


def test_opportunity_and_its_trend():
    games = [G(1, 5, 5, opp=4), G(2, 6, 5, opp=6), G(3, 12, 5, opp=14)]
    f = season.recent_form(games, "RB", ros=100.0, games_left=15)
    assert f["opp"] == 8.0 and f["opp_trend"] == 14 - 5.0
    one = season.recent_form([G(1, 5, 5, opp=4)], "RB", ros=100.0, games_left=15)
    assert one["opp"] == 4.0 and one["opp_trend"] is None, "no earlier games to compare with"


def test_snaps_only_over_the_games_that_have_them():
    games = [G(1, 5, 5, opp=6, tgt=2, rec_yd=10, snaps=20, team_snaps=60),
             G(2, 8, 5, opp=8, tgt=4, rec_yd=30, snaps=30, team_snaps=60),
             G(3, 9, 5, opp=9, tgt=3, rec_yd=20)]  # snaps not published yet
    f = season.recent_form(games, "RB", ros=100.0, games_left=15)
    assert f["snap_pct"] == round(100 * 50 / 120) and f["snap_games"] == 2
    assert f["tgt_snap"] == round(6 / 50, 2), "targets over the snaps he was on the field for, same games"
    assert f["ypt"] == round(60 / 9, 1), "yards per target over the whole window"
    none = season.recent_form([G(1, 5, 5, opp=6, tgt=3)], "RB", ros=100.0, games_left=15)
    assert none["snap_pct"] is None and none["snap_games"] == 0 and none["tgt_snap"] is None


def test_qb_has_snaps_but_no_target_columns_and_kicker_neither():
    qb = season.recent_form([G(1, 20, 18, opp=35, snaps=60, team_snaps=60)], "QB", ros=300.0, games_left=16)
    assert qb["snap_pct"] == 100 and qb["tgt_snap"] is None and qb["ypt"] is None
    k = season.recent_form([G(1, 9, 8, opp=5, snaps=None, team_snaps=64)], "K", ros=120.0, games_left=16)
    assert k["snap_pct"] is None and k["opp"] == 5.0 and k["tgt_snap"] is None


def test_dst_counts_any_stat_line_as_a_game_and_has_no_opportunity():
    games = [G(1, 8, 7, opp=None, active=True), G(2, 0, 7, opp=None, active=False)]
    f = season.recent_form(games, "D/ST", ros=110.0, games_left=16)
    assert f["weeks"] == [1] and f["opp"] is None and f["opp_trend"] is None and f["snap_pct"] is None


def test_pace_gap_is_the_window_pace_over_the_games_left_against_ros():
    f = season.recent_form([G(1, 10, 8), G(2, 14, 8)], "WR", ros=150.0, games_left=15)
    assert f["pace_gap"] == round(12.0 * 15 - 150.0, 1)


def test_games_left_takes_his_bye_out_only_when_it_is_still_ahead():
    assert season.games_left(3, None) == 16
    assert season.games_left(3, 7) == 15
    assert season.games_left(3, 3) == 15, "on his bye week now: that week is not a game"
    assert season.games_left(8, 7) == 11, "bye already taken"
    assert season.games_left(18, 7) == 1 and season.games_left(19, None) == 0

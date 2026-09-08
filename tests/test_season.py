"""Pure in-season maths: lineup-optimal, waiver deltas, the decisions append."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from ffl import season

TWO_QB = {"team_count": 10,
          "starters": {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1, "RB/WR/TE": 2}}


def P(pid, name, pos, proj, slot="BE", injury="ACTIVE", bye=False, team="T", season_proj=None):
    return {"player_id": pid, "name": name, "pos": pos, "team": team, "slot": slot, "bye": bye,
            "injury": injury, "proj_espn": proj, "proj_sleeper": proj, "proj": proj, "spread": 0.0,
            "espn_only": False, "actual": 0.0, "game_played": 0,
            "season_proj": season_proj if season_proj is not None else proj * 17,
            "season_proj_sleeper": None, "return_week": None, "injury_note": None}


def roster():
    return [
        P(1, "QB A", "QB", 20, "QB"), P(2, "QB B", "QB", 18, "QB"), P(3, "QB C", "QB", 22, "BE"),
        P(4, "RB A", "RB", 15, "RB"), P(5, "RB B", "RB", 14, "RB"), P(6, "RB C", "RB", 16, "BE"),
        P(7, "WR A", "WR", 13, "WR"), P(8, "WR B", "WR", 12, "WR"), P(9, "WR C", "WR", 9, "RB/WR/TE"),
        P(10, "TE A", "TE", 8, "TE"), P(11, "TE B", "TE", 7, "BE"),
        P(12, "RB D", "RB", 10, "RB/WR/TE"),
        P(13, "DST", "D/ST", 6, "D/ST"), P(14, "K", "K", 7, "K"),
        P(15, "IR guy", "RB", 30, "IR", injury="INJURY_RESERVE"),
    ]


def by_slot(lineup):
    return {(e["slot"], i): e["name"] for i, e in enumerate(lineup)}


def test_optimal_lineup_fills_fixed_slots_then_flex_and_ignores_ir():
    lu = season.optimal_lineup(roster(), TWO_QB)
    assert [e["slot"] for e in lu] == ["QB", "QB", "RB", "RB", "WR", "WR", "TE", "RB/WR/TE", "RB/WR/TE", "D/ST", "K"]
    names = [e["name"] for e in lu]
    assert names[:2] == ["QB C", "QB A"], "best two QBs start; the 22-pt bench QB is in"
    assert names[2:4] == ["RB C", "RB A"]
    assert set(names[7:9]) == {"RB B", "RB D"}, "flex takes the best remaining RB/WR/TE: RB B 14, RB D 10"


def test_flex_takes_the_best_remaining_eligible():
    lu = season.optimal_lineup(roster(), TWO_QB)
    flex = {e["name"] for e in lu if e["slot"] == "RB/WR/TE"}
    assert flex == {"RB B", "RB D"}, "RB B 14 and RB D 10 beat WR C 9 and TE B 7"
    assert "IR guy" not in [e["name"] for e in lu]


def test_out_and_bye_players_are_not_started():
    r = roster()
    r[5]["injury"] = "OUT"          # RB C
    r[3]["bye"] = True              # RB A
    lu = season.optimal_lineup(r, TWO_QB)
    starters = [e["name"] for e in lu]
    assert "RB C" not in starters and "RB A" not in starters
    assert season.playable_proj(r[5]) == 0.0


def test_empty_slot_when_nobody_is_eligible():
    r = [p for p in roster() if p["pos"] != "K"]
    lu = season.optimal_lineup(r, TWO_QB)
    k = next(e for e in lu if e["slot"] == "K")
    assert k["player_id"] is None and k["proj"] == 0.0


def test_lineup_diff_lists_swaps_with_gain():
    cur = season.current_lineup(roster())
    opt = season.optimal_lineup(roster(), TWO_QB)
    diff = season.lineup_diff(cur, opt)
    assert {(d["out"], d["in"]) for d in diff} >= {("QB B", "QB C")}
    qb = next(d for d in diff if d["in"] == "QB C")
    assert qb["gain"] == pytest.approx(4.0) and qb["slot"] == "QB"
    assert diff == sorted(diff, key=lambda d: -d["gain"])
    assert season.lineup_total(opt) >= season.lineup_total(cur)


def test_lineup_diff_is_empty_when_already_optimal():
    opt = season.optimal_lineup(roster(), TWO_QB)
    assert season.lineup_diff(opt, opt) == []


def test_ros_fraction_is_one_in_week_one_and_zero_after_the_season():
    assert season.ros_fraction(1) == 1.0
    assert season.ros_fraction(18) == 0.0
    assert season.ros_fraction(10) == pytest.approx(8 / 17)
    assert season.ros_proj(170.0, 10) == pytest.approx(80.0)


def test_injury_factors():
    assert season.injury_terms("ACTIVE", 1) == (1.0, 1.0)
    assert season.injury_terms("QUESTIONABLE", 1) == (0.85, 1.0)
    assert season.injury_terms("DOUBTFUL", 1) == (0.7, 1.0)
    f, avail = season.injury_terms("OUT", 1)
    assert f == 1.0 and avail == pytest.approx(16 / 17)
    f, avail = season.injury_terms("INJURY_RESERVE", 1)
    assert avail == pytest.approx(13 / 17)
    f, avail = season.injury_terms("INJURY_RESERVE", 17)
    assert avail == 0.0, "no games left after return"


def test_add_score_formula():
    # healthy, would start: (ros + week) × 1
    assert season.add_score(30.0, 4.0, "ACTIVE", 1) == pytest.approx(34.0)
    # questionable: × 0.85
    assert season.add_score(30.0, 4.0, "QUESTIONABLE", 1) == pytest.approx(28.9)
    # out: no week term, ros scaled to the games after return
    assert season.add_score(34.0, 4.0, "OUT", 1) == pytest.approx(34.0 * 16 / 17)


def test_week_of_counts_seven_day_blocks_from_week_one():
    w1 = date(2026, 9, 8)
    assert season.week_of(w1, w1) == 1
    assert season.week_of(date(2026, 9, 14), w1) == 1, "the seventh day is still week 1"
    assert season.week_of(date(2026, 9, 15), w1) == 2
    assert season.week_of(date(2026, 10, 11), w1) == 5, "a return date lands in the week it falls in"


def test_games_out_is_measured_from_the_current_week():
    assert season.games_out(None, 3) is None
    assert season.games_out(5, 1) == 4
    assert season.games_out(5, 3) == 2
    assert season.games_out(5, 7) is None, "a return already past is a stale date: fall back to the constant"
    assert season.games_out(5, 5) is None, "back this week, still OUT: the date is stale too"


def test_injury_terms_prefers_a_known_return_over_the_constant():
    _, avail = season.injury_terms("INJURY_RESERVE", 1, games_out=2)
    assert avail == pytest.approx(15 / 17)
    _, avail = season.injury_terms("INJURY_RESERVE", 1, games_out=None)
    assert avail == pytest.approx(13 / 17), "falls back to the IR constant"
    _, avail = season.injury_terms("INJURY_RESERVE", 1, games_out=0)
    assert avail == 1.0, "back this week: every remaining game counts"


def test_add_score_takes_games_out_but_only_for_the_out_statuses():
    assert season.add_score(34.0, 4.0, "INJURY_RESERVE", 1, games_out=2) == pytest.approx(34.0 * 15 / 17)
    assert season.add_score(34.0, 4.0, "INJURY_RESERVE", 1) == pytest.approx(34.0 * 13 / 17)
    assert season.add_score(34.0, 4.0, "INJURY_RESERVE", 1, games_out=0) == pytest.approx(34.0), "no week term even so"
    q = season.add_score(30.0, 4.0, "QUESTIONABLE", 1)
    assert season.add_score(30.0, 4.0, "QUESTIONABLE", 1, games_out=6) == pytest.approx(q)


def fa(pid, name, pos, week_proj, season_proj, injury="ACTIVE"):
    return P(pid, name, pos, week_proj, "FA", injury=injury, season_proj=season_proj)


def test_waiver_deltas_against_roster_and_pool():
    mine = roster()
    frees = [fa(100, "FA RB hot", "RB", 17, 200), fa(101, "FA RB cold", "RB", 5, 60),
             fa(102, "FA QB", "QB", 19, 300)]
    # a big free pool so replacement is well below my players
    filler = [fa(200 + i, f"fill RB{i}", "RB", 3, 40 - i) for i in range(40)]
    pool = mine + frees + filler
    rows = season.waiver_deltas(frees, mine, pool, TWO_QB, week=1)
    hot = next(r for r in rows if r["name"] == "FA RB hot")
    cold = next(r for r in rows if r["name"] == "FA RB cold")
    # my weakest non-IR RB by ROS is RB D (10*17 = 170); hot gains 30 vs roster
    assert hot["delta_roster"] == pytest.approx(200 - 170)
    assert hot["would_start"] is True and hot["week_delta"] == pytest.approx(17 - 10), "displaces RB D (10) in flex"
    assert hot["add"] == pytest.approx(30 + 7)
    assert cold["would_start"] is False and cold["week_delta"] == 0.0
    assert cold["delta_roster"] < 0
    assert rows[0]["name"] == "FA RB hot" or rows[0]["name"] == "FA QB"
    assert [r["add"] for r in rows] == sorted((r["add"] for r in rows), reverse=True)
    assert set(rows[0]) >= {"ros_proj", "week_proj", "delta_roster", "delta_pool", "would_start", "week_delta", "add"}


def test_add_uses_the_roster_arm_when_my_weakest_is_the_higher_replacement():
    mine = roster()
    frees = [fa(100, "FA RB", "RB", 12, 150)]
    # Pool is only my roster + this FA: 6 RBs, so the replacement index (demand 38) clamps to the
    # last one — the FA himself, 150 — while my weakest non-IR RB is RB D (10*17 = 170). 170 is the
    # higher replacement, so the roster arm binds: delta_roster -20 < delta_pool 0.
    rows = season.waiver_deltas(frees, mine, mine + frees, TWO_QB, week=1)
    r = rows[0]
    assert r["delta_pool"] == pytest.approx(0.0)
    assert r["delta_roster"] == pytest.approx(-20.0)
    assert r["add"] == pytest.approx(min(r["delta_roster"], r["delta_pool"]) + r["week_delta"])


def test_add_uses_the_pool_arm_when_my_roster_is_thin():
    # My weakest RB is a scrub (season 5), so the roster arm would say the FA is a huge gain; the
    # league-wide pool says otherwise and must bind.
    mine = roster() + [fa(50, "RB scrub", "RB", 1, 5)]
    mine[-1]["slot"] = "BE"
    frees = [fa(100, "FA RB", "RB", 12, 150)]
    # 40 filler RBs at season 100 make the pool deep enough to reach the replacement index.
    filler = [fa(200 + i, f"fill RB{i}", "RB", 1, 100) for i in range(40)]
    pool = mine + frees + filler
    rows = season.waiver_deltas(frees, mine, pool, TWO_QB, week=1)
    r = rows[0]
    # RB demand = starters (2*10 + flex 2*10*0.5 = 30) + bench (0.8*10 = 8) = 38 -> index 37.
    # Pool RBs sorted desc: 510, 272, 255, 238, 170, 150(FA), then 40x100, then 5 -> index 37 is 100.
    assert season.pool_replacement(pool, TWO_QB, "RB", 1) == pytest.approx(100.0)
    assert season.weakest_rostered(mine, "RB", 1) == pytest.approx(5.0)
    assert r["delta_pool"] == pytest.approx(150 - 100)
    assert r["delta_roster"] == pytest.approx(150 - 5)
    assert r["delta_pool"] < r["delta_roster"], "the pool arm is the binding one here"
    # He is the better flex than RB D (10), so week_delta = 12 - 10 = 2; add = 50 + 2.
    assert r["week_delta"] == pytest.approx(2.0)
    assert r["add"] == pytest.approx(52.0)


def test_waiver_deltas_price_an_ir_player_off_his_return_week():
    mine = roster()
    known = {**fa(100, "IR back soon", "RB", 0, 200, injury="INJURY_RESERVE"),
             "return_week": 4, "injury_note": "Expected back Week 4"}
    unknown = {**fa(101, "IR no date", "RB", 0, 200, injury="INJURY_RESERVE"),
               "return_week": None, "injury_note": None}
    filler = [fa(200 + i, f"fill RB{i}", "RB", 3, 40 - i) for i in range(40)]
    rows = season.waiver_deltas([known, unknown], mine, mine + [known, unknown] + filler, TWO_QB, week=3)
    by_name = {r["name"]: r for r in rows}
    left = season.NFL_WEEKS - 3
    r = by_name["IR back soon"]
    ros_delta = min(r["delta_roster"], r["delta_pool"])  # the two of them differ only in return_week
    assert by_name["IR back soon"]["add"] == pytest.approx(round(ros_delta * (left - 1) / left, 1))
    assert by_name["IR no date"]["add"] == pytest.approx(round(ros_delta * (left - 4) / left, 1))
    assert by_name["IR back soon"]["add"] > by_name["IR no date"]["add"]
    assert by_name["IR back soon"]["return_week"] == 4
    assert by_name["IR back soon"]["injury_note"] == "Expected back Week 4"
    assert by_name["IR no date"]["return_week"] is None and by_name["IR no date"]["injury_note"] is None


def test_append_decision_writes_the_journal_block(tmp_path):
    p = tmp_path / "DECISIONS.md"
    p.write_text("# Decisions log\n\n---\n")
    text = season.append_decision(p, "Claim X", "Claim X, drop Y", "Y is done", "Z was worse",
                                  today=date(2026, 9, 10))
    assert text == p.read_text()
    assert text.endswith(
        "\n## 2026-09-10 — Claim X\n- **Decision:** Claim X, drop Y\n- **Reasoning:** Y is done\n"
        "- **Alternatives rejected:** Z was worse\n- **Outcome (fill in later):**\n")


def test_append_decision_rejects_blank_fields_and_missing_file(tmp_path):
    p = tmp_path / "DECISIONS.md"
    p.write_text("x\n")
    with pytest.raises(ValueError):
        season.append_decision(p, " ", "d", "r", "")
    with pytest.raises(FileNotFoundError):
        season.append_decision(tmp_path / "nope.md", "t", "d", "r", "")
    assert p.read_text() == "x\n"


def test_is_live_window():
    ny = ZoneInfo("America/New_York")
    assert season.is_live(datetime(2026, 9, 13, 13, 0, tzinfo=ny))     # Sunday
    assert season.is_live(datetime(2026, 9, 10, 20, 30, tzinfo=ny))    # Thursday night
    assert not season.is_live(datetime(2026, 9, 10, 19, 0, tzinfo=ny))
    assert season.is_live(datetime(2026, 9, 15, 1, 0, tzinfo=ny))      # Monday night spills past midnight
    assert not season.is_live(datetime(2026, 9, 15, 9, 0, tzinfo=ny))  # Tuesday morning
    assert not season.is_live(datetime(2026, 9, 16, 12, 0, tzinfo=ny))  # Wednesday

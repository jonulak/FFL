"""Pure in-season maths: lineup-optimal, waiver deltas."""
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
            "season_proj_sleeper": None, "ros_proj": None, "ros_sleeper": None, "ros_cbs": None,
            "ros_spread": None, "return_week": None, "injury_note": None, "exp": None, "ahead": [],
            "opponent": None, "kickoff": None, "home": None}


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
    assert season.ros_proj({"season_proj": 170.0, "ros_proj": None}, 10) == pytest.approx(80.0)


def test_ros_proj_prefers_the_sources_number_over_the_season_formula():
    assert season.ros_proj({"season_proj": 170.0, "ros_proj": 123.4}, 10) == 123.4
    assert season.ros_proj({"season_proj": 170.0}, 10) == pytest.approx(80.0), "a dict without the key falls back"
    assert season.ros_from_sources({"ros_proj": 123.4}) is True
    assert season.ros_from_sources({"ros_proj": None}) is False and season.ros_from_sources({}) is False


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


def test_add_score_skips_the_availability_scaling_when_the_sources_priced_the_injury():
    # Sleeper publishes no row for the weeks he misses and CBS counts only the games left, so a
    # ROS from them is already net of the absence; scaling it again would count the injury twice.
    scaled = season.add_score(100.0, 5.0, "INJURY_RESERVE", 3, games_out=4)
    assert scaled == pytest.approx(round(100.0 * 11 / 15, 1))
    assert season.add_score(100.0, 5.0, "INJURY_RESERVE", 3, games_out=4, injury_priced=True) == 100.0
    assert season.add_score(100.0, 5.0, "QUESTIONABLE", 3, injury_priced=True) == pytest.approx(round(105.0 * 0.85, 1)), \
        "the probability factors are about this week, not about games left"


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


def test_waiver_deltas_use_each_players_own_ros_and_rank_the_pool_by_it():
    mine = roster()
    for p in mine:
        p["ros_proj"] = p["season_proj"] / 2  # the sources say everyone on my roster is half what draft day did
    hot = {**fa(100, "FA RB hot", "RB", 17, 200), "ros_proj": 150.0}
    nosrc = fa(101, "FA RB deep", "RB", 5, 60)  # no source has him: season formula, week 1 -> 60
    filler = [{**fa(200 + i, f"fill RB{i}", "RB", 3, 40 - i), "ros_proj": 20.0} for i in range(40)]
    rows = season.waiver_deltas([hot, nosrc], mine, mine + [hot, nosrc] + filler, TWO_QB, week=1)
    by = {r["name"]: r for r in rows}
    assert by["FA RB hot"]["ros_proj"] == 150.0
    assert season.weakest_rostered(mine, "RB", 1) == pytest.approx(85.0), "RB D at 170/2"
    assert by["FA RB hot"]["delta_roster"] == pytest.approx(150 - 85)
    assert season.pool_replacement(mine + [hot, nosrc] + filler, TWO_QB, "RB", 1) == pytest.approx(20.0), \
        "ranked by ROS, not by season total"
    assert by["FA RB deep"]["ros_proj"] == pytest.approx(60.0)


def test_waiver_deltas_do_not_scale_a_sourced_ros_for_an_ir_player():
    mine = roster()
    ir = {**fa(100, "IR priced", "RB", 0, 200, injury="INJURY_RESERVE"), "return_week": 4, "ros_proj": 120.0}
    filler = [fa(200 + i, f"fill RB{i}", "RB", 3, 40 - i) for i in range(40)]
    r = season.waiver_deltas([ir], mine, mine + [ir] + filler, TWO_QB, week=3)[0]
    assert r["add"] == pytest.approx(min(r["delta_roster"], r["delta_pool"])), "no availability haircut, no week term"


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


def test_is_live_window():
    ny = ZoneInfo("America/New_York")
    assert season.is_live(datetime(2026, 9, 13, 13, 0, tzinfo=ny))     # Sunday
    assert season.is_live(datetime(2026, 9, 10, 20, 30, tzinfo=ny))    # Thursday night
    assert not season.is_live(datetime(2026, 9, 10, 19, 0, tzinfo=ny))
    assert season.is_live(datetime(2026, 9, 15, 1, 0, tzinfo=ny))      # Monday night spills past midnight
    assert not season.is_live(datetime(2026, 9, 15, 9, 0, tzinfo=ny))  # Tuesday morning
    assert not season.is_live(datetime(2026, 9, 16, 12, 0, tzinfo=ny))  # Wednesday


def test_roster_cards_groups_calls_and_orders_by_position_then_projection():
    rs = roster()
    cards = season.roster_cards(rs, TWO_QB, 2)
    by_name = {c["name"]: c for c in cards}
    assert by_name["QB C"]["group"] == "bench" and by_name["QB C"]["call"] == "START", "ESPN benches him"
    assert by_name["QB B"]["group"] == "starter" and by_name["QB B"]["call"] == "BENCH"
    assert by_name["IR guy"]["group"] == "ir" and by_name["IR guy"]["call"] == "IR"
    assert by_name["IR guy"]["reason"] == "IR slot" and by_name["IR guy"]["gap"] is None
    groups = [c["group"] for c in cards]
    assert groups == sorted(groups, key=["starter", "bench", "ir"].index)
    starters = [(c["slot"], c["proj"]) for c in cards if c["group"] == "starter"]
    order = season.SLOT_ORDER
    assert starters == sorted(starters, key=lambda t: (order.index(t[0]), -t[1])), "ESPN slot order, then projection"
    assert [c["name"] for c in cards if c["group"] == "starter"][:2] == ["QB A", "QB B"], "20 before 18 at QB"


def test_roster_cards_starter_gap_is_against_the_best_bench_alternative():
    cards = {c["name"]: c for c in season.roster_cards(roster(), TWO_QB, 2)}
    # QB A (20) starts at QB; the best bench QB is QB B (18)
    assert cards["QB A"]["gap"] == 2.0
    assert cards["QB A"]["reason"] == "starts at QB: 20.0 vs 18.0 for QB B"
    assert cards["QB A"]["close_call"] is False, "a 2.0 gap is not under CLOSE_GAP"
    # TE A (8) starts at TE; TE B (7) is the alternative: under 2 points is a close call
    assert cards["TE A"]["gap"] == 1.0 and cards["TE A"]["close_call"] is True
    # DST has no bench alternative
    assert cards["DST"]["gap"] is None
    assert cards["DST"]["reason"] == "starts at D/ST: no bench alternative"


def test_roster_cards_bench_gap_is_against_the_weakest_starter_he_could_replace():
    cards = {c["name"]: c for c in season.roster_cards(roster(), TWO_QB, 2)}
    # WR C (9) sits; the weakest starter in a slot that takes a WR is RB D (10) at flex
    assert cards["WR C"]["call"] == "BENCH" and cards["WR C"]["gap"] == -1.0
    assert cards["WR C"]["reason"] == "bench: 1.0 behind RB D at FLEX", "the flex slot is named FLEX, not RB/WR/TE"
    assert cards["WR C"]["close_call"] is True


def test_roster_cards_spread_makes_a_close_call_and_questionable_adds_the_suffix():
    rs = roster()
    rs[0]["spread"] = 3.0                # QB A: sources disagree by 3
    rs[6]["injury"] = "QUESTIONABLE"     # WR A
    cards = {c["name"]: c for c in season.roster_cards(rs, TWO_QB, 2)}
    assert cards["QB A"]["close_call"] is True
    assert cards["WR A"]["reason"].endswith(" — his status is your call")
    assert cards["WR A"]["call"] == "START"


def test_roster_cards_bye_and_out_have_no_gap():
    rs = roster()
    rs[7]["bye"] = True                  # WR B
    rs[8]["injury"] = "OUT"              # WR C
    cards = {c["name"]: c for c in season.roster_cards(rs, TWO_QB, 2)}
    assert cards["WR B"] == {**cards["WR B"], "group": "starter", "call": "BYE", "gap": None,
                             "close_call": False, "reason": "bye week"}
    assert cards["WR C"]["call"] == "OUT" and cards["WR C"]["reason"] == "out: OUT"
    assert cards["WR C"]["close_call"] is False


def test_roster_cards_spread_is_a_close_call_with_no_alternative_but_never_for_out():
    """CLOSE_SPREAD stands on its own: an unreliable projection is a close call even where
    there is no bench alternative to compare against — but a call of OUT is never close."""
    rs = roster()
    rs[12]["spread"] = 3.0               # DST: the only D/ST, so gap is None
    rs[8]["spread"] = 5.0                # WR C
    rs[8]["injury"] = "OUT"
    cards = {c["name"]: c for c in season.roster_cards(rs, TWO_QB, 2)}
    assert cards["DST"]["gap"] is None and cards["DST"]["close_call"] is True
    assert cards["WR C"]["call"] == "OUT" and cards["WR C"]["close_call"] is False


def test_roster_rows_resolve_ros_and_the_playable_week_and_sort_by_ros():
    rows = season.roster_rows([P(1, "A", "RB", 10, "RB", season_proj=170.0),
                               P(2, "B", "RB", 12, "BE", injury="OUT", season_proj=340.0),
                               P(3, "C", "RB", 8, "IR", injury="INJURY_RESERVE", season_proj=85.0)], week=1)
    assert [r["name"] for r in rows] == ["B", "A", "C"]
    assert rows[0]["week_proj"] == 0.0 and rows[1]["week_proj"] == 10.0, "OUT scores nothing this week"
    assert rows[1]["ros_proj"] == season.ros_proj(rows[1], 1)


# --- STRATEGY.md "Usage-adjusted ROS" -----------------------------------------------------------

def form(l3, weight, games_left=15):
    return {"games": 2, "weeks": [1, 2], "l3": l3, "beat": 0.0, "opp": None, "opp_trend": None,
            "snap_pct": None, "snap_games": 0, "tgt_snap": None, "ypt": None, "pace_gap": 0.0,
            "weight": weight, "games_left": games_left}


def test_ros_adj_is_the_model_when_he_has_no_games():
    p = {**P(1, "X", "WR", 10), "ros_proj": 150.0, "form": None}
    assert season.ros_adj(p, week=3) == 150.0
    q = {**P(1, "X", "WR", 10), "ros_proj": 150.0, "form": form(20.0, 0.0)}
    assert season.ros_adj(q, week=3) == 150.0, "zero weight: nothing to shrink toward"


def test_ros_adj_shrinks_the_model_rate_toward_the_window_by_its_weight():
    p = {**P(1, "Boston", "WR", 10), "ros_proj": 160.0, "form": form(17.2, 2.0, games_left=15)}
    pg = 160.0 / 15
    assert season.K_PRIOR == 4
    assert season.ros_adj(p, week=3) == round(15 * (2.0 * 17.2 + 4 * pg) / 6.0, 1)
    one = {**P(2, "Coleman", "RB", 5), "ros_proj": 79.0, "form": form(14.8, 0.65, games_left=15)}
    assert season.ros_adj(one, week=3) == round(15 * (0.65 * 14.8 + 4 * 79.0 / 15) / 4.65, 1)


def test_ros_adj_starts_from_the_season_formula_when_no_source_has_him():
    p = {**P(1, "X", "RB", 10, season_proj=170.0), "form": form(10.0, 1.0, games_left=15)}
    model = season.ros_proj(p, week=3)
    assert season.ros_adj(p, week=3) == round(15 * (10.0 + 4 * model / 15) / 5, 1)


def test_ros_adj_blends_only_the_games_an_out_player_can_play():
    # OUT until week 7 from week 3: four games missed, which the model already leaves out
    p = {**P(1, "X", "WR", 10, injury="OUT"), "ros_proj": 110.0, "return_week": 7,
         "form": form(12.0, 2.0, games_left=15)}
    assert season.ros_adj(p, week=3) == round(11 * (2.0 * 12.0 + 4 * 110.0 / 11) / 6, 1)
    ir = {**p, "injury": "INJURY_RESERVE", "return_week": None}  # no date: the four-game constant
    assert season.ros_adj(ir, week=3) == round(11 * (2.0 * 12.0 + 4 * 110.0 / 11) / 6, 1)
    done = {**p, "return_week": 19}
    assert season.ros_adj(done, week=3) == 110.0, "nothing left to play: the model as it stands"


def test_ros_week_rolls_once_every_kickoff_is_four_hours_old():
    ny = ZoneInfo("America/New_York")
    kicks = [datetime(2026, 9, 17, 20, 15, tzinfo=ny), datetime(2026, 9, 20, 13, 0, tzinfo=ny),
             datetime(2026, 9, 21, 20, 15, tzinfo=ny)]
    assert season.ros_week(2, kicks, now=datetime(2026, 9, 21, 23, 0, tzinfo=ny)) == 2, "Monday night still on"
    assert season.ros_week(2, kicks, now=datetime(2026, 9, 22, 0, 30, tzinfo=ny)) == 3, "all played"
    assert season.ros_week(2, [], now=datetime(2026, 9, 22, 0, 30, tzinfo=ny)) == 2, "no schedule: ESPN's week"
    assert season.ros_week(18, kicks, now=datetime(2027, 1, 12, tzinfo=ny)) == 18, "never past the last week"


def test_waiver_deltas_run_on_the_adjusted_ros_and_drop_the_week_term_once_the_week_is_over():
    mine = roster()
    hot = {**fa(100, "FA RB hot", "RB", 17, 200), "form": form(20.0, 2.0, games_left=15)}
    filler = [fa(200 + i, f"fill RB{i}", "RB", 3, 40 - i) for i in range(40)]
    rows = season.waiver_deltas([hot], mine, mine + [hot] + filler, TWO_QB, week=3)
    row = rows[0]
    adj = season.ros_adj(hot, 3)
    model = season.ros_proj(hot, 3)  # no source: the season total scaled by the weeks left
    assert adj > model and row["ros_adj"] == adj and row["ros_proj"] == model, "the model stays visible beside"
    weakest = min(season.ros_adj(p, 3) for p in mine if p["pos"] == "RB" and p["slot"] != "IR")
    assert row["delta_roster"] == round(adj - weakest, 1)
    assert row["would_start"] is True and row["week_delta"] > 0
    over = season.waiver_deltas([hot], mine, mine + [hot] + filler, TWO_QB, week=3, week_over=True)[0]
    assert over["would_start"] is False and over["week_delta"] == 0.0
    assert over["add"] == round(min(over["delta_roster"], over["delta_pool"]), 1)


def test_roster_rows_carry_the_adjusted_ros_and_sort_by_it():
    mine = roster()
    mine[3] = {**mine[3], "ros_proj": 100.0, "form": form(30.0, 3.0, games_left=15)}  # RB A, hot
    rows = season.roster_rows(mine, week=3)
    a = next(r for r in rows if r["name"] == "RB A")
    assert a["ros_adj"] == season.ros_adj(mine[3], 3) and a["ros_proj"] == 100.0
    assert [r["ros_adj"] for r in rows] == sorted((r["ros_adj"] for r in rows), reverse=True)


def test_a_matchup_is_final_once_every_starters_game_is_over():
    side = lambda pending: {"starter_ids": [1], "pending_teams": pending}  # noqa: E731
    m = {"home": side([]), "away": side(["ATL", "NO"])}
    assert season.matchup_final({"home": side([]), "away": side([])}), "ESPN calls every game played"
    assert not season.matchup_final(m), "two games still to finish"
    assert not season.matchup_final(m, {"ATL": {"status": "post"}, "NO": {"status": "in"}})
    assert season.matchup_final(m, {"ATL": {"status": "post"}, "NO": {"status": "post"}}), "the feed's whistle, before ESPN"
    assert not season.matchup_final({"home": {"starter_ids": []}, "away": {"starter_ids": []}}), "a future week"
    late = {"home": {**side([]), "starter_teams": ["NO"]}, "away": side([])}
    assert not season.matchup_final(late, {"NO": {"status": "in"}}), "ESPN calls it played at three hours; the feed says on"


@pytest.mark.parametrize("q,name,team,hit", [
    ("mcc", "Christian McCaffrey", "SF", True),
    ("MCCAFF", "Christian McCaffrey", "SF", True),
    ("jose", "José Ramírez", "SEA", True),
    ("ramírez", "Jose Ramirez", "SEA", True),
    ("jr", "Marvin Harrison Jr.", "ARI", True),
    ("st brown", "Amon-Ra St. Brown", "DET", True),
    ("ja'marr", "Ja'Marr Chase", "CIN", True),
    ("jamarr", "Ja'Marr Chase", "CIN", True),
    ("det", "Jahmyr Gibbs", "DET", True),
    ("de", "Jahmyr Gibbs", "DET", False),
    ("lions", "Lions D/ST", "DET", True),
    ("zz", "Jahmyr Gibbs", "DET", False),
    ("  ", "Jahmyr Gibbs", "DET", False),
])
def test_name_matches(q, name, team, hit):
    assert season.name_matches({"name": name, "team": team}, q) is hit


# --- win probability ---------------------------------------------------------------------------

def wp_player(pos="WR", proj=10.0, actual=0.0, game=None, game_played=0, bye=False, injury="ACTIVE"):
    p = {"pos": pos, "proj": proj, "actual": actual, "game_played": game_played, "bye": bye, "injury": injury}
    if game is not None:
        p["game"] = game
    return p


def test_game_left_before_during_and_after():
    assert season.game_left(wp_player()) == 1.0
    assert season.game_left(wp_player(game={"status": "pre"})) == 1.0
    assert season.game_left(wp_player(game={"status": "in", "quarter": 1, "clock": "15:00"})) == 1.0
    assert season.game_left(wp_player(game={"status": "in", "quarter": 3, "clock": "7:30"})) == 0.375
    assert season.game_left(wp_player(game={"status": "in", "quarter": 2, "clock": "0:00"})) == 0.5  # half
    assert season.game_left(wp_player(game={"status": "in", "quarter": 5, "clock": "8:00"})) == 0.0  # OT
    assert season.game_left(wp_player(game={"status": "post"})) == 0.0
    assert season.game_left(wp_player(game_played=100)) == 0.0


def test_feed_in_progress_beats_espn_game_played():
    # ESPN flips game_played three hours after kickoff; a Monday game can still be on
    p = wp_player(game={"status": "in", "quarter": 4, "clock": "3:00"}, game_played=100)
    assert season.game_left(p) == pytest.approx(0.05)


def test_game_left_without_quarter_counts_the_whole_game():
    assert season.game_left(wp_player(game={"status": "in", "quarter": None, "clock": None})) == 1.0


def test_identical_sides_are_a_coin_flip():
    side = [wp_player("QB", 20), wp_player("WR", 12)]
    assert season.win_prob(side, [dict(p) for p in side], season.SIGMA_FALLBACK) == 0.5


def test_favourite_by_one_sigma():
    import math
    a, b = [wp_player("TE", 15.5)], [wp_player("TE", 10.0)]   # margin 5.5, sd sqrt(2)*5.5
    assert season.win_prob(a, b, season.SIGMA_FALLBACK) == pytest.approx(0.5 * (1 + math.erf(0.5)))


def test_all_final_is_decided_by_the_score():
    def done(pts):
        return [wp_player(actual=pts, game={"status": "post"})]
    assert season.win_prob(done(100), done(90), season.SIGMA_FALLBACK) == 1.0
    assert season.win_prob(done(90), done(100), season.SIGMA_FALLBACK) == 0.0
    assert season.win_prob(done(95), done(95), season.SIGMA_FALLBACK) == 0.5


def test_a_final_tie_is_a_tie_whatever_the_float_noise():
    # 10.1 + 20.2 sums to 30.299999999999997, under a single 30.3
    a = [wp_player(actual=10.1, game={"status": "post"}), wp_player(actual=20.2, game={"status": "post"})]
    b = [wp_player(actual=30.3, game={"status": "post"})]
    assert season.win_prob(a, b, season.SIGMA_FALLBACK) == 0.5


def test_out_and_bye_starters_add_nothing():
    assert season.side_outlook([wp_player(injury="OUT"), wp_player(bye=True)], season.SIGMA_FALLBACK) == (0.0, 0.0)


def test_a_side_of_nobody_still_gets_finite_odds():
    p = season.win_prob([wp_player(injury="OUT")], [wp_player("QB", 18)], season.SIGMA_FALLBACK)
    assert 0.0 < p < 0.5


def test_mid_game_counts_posted_points_plus_the_share_left():
    p = wp_player("RB", proj=16.0, actual=6.0, game={"status": "in", "quarter": 3, "clock": "15:00"})
    assert season.side_outlook([p], {"RB": 8.0}) == (6.0 + 16.0 * 0.5, 64.0 * 0.5)


def test_posted_points_without_a_clock_carry_the_rest_of_the_projection():
    p = wp_player("RB", proj=16.0, actual=6.0)          # feed down mid-game
    assert season.side_outlook([p], {"RB": 8.0}) == (16.0, 64.0 * 10 / 16)
    beat = wp_player("RB", proj=16.0, actual=20.0)     # already past his projection
    assert season.side_outlook([beat], {"RB": 8.0}) == (20.0, 0.0)


def test_missing_position_sigma_falls_back():
    _, var = season.side_outlook([wp_player("K", 8.0)], {})
    assert var == season.SIGMA_FALLBACK["K"] ** 2
    _, var = season.side_outlook([wp_player("LB", 8.0)], {})
    assert var == season.SIGMA_DEFAULT ** 2


def test_rank_players_one_row_per_player_rostered_wins_add_desc_none_last():
    fa = {"player_id": 1, "add": 5.0, "owner": None}
    stale_fa = {"player_id": 2, "add": 30.0, "owner": None}
    theirs = {"player_id": 2, "add": 9.0, "owner": {"team_id": 2}}
    mine = {"player_id": 3, "add": None, "owner": {"team_id": 7}}
    out = season.rank_players([mine, fa, stale_fa, theirs])
    assert [(r["player_id"], r["add"]) for r in out] == [(2, 9.0), (1, 5.0), (3, None)]


def test_cards_group_by_espn_slot_and_keep_the_call_as_the_chip():
    settings = {"starters": {"RB": 1}}
    ps = [{"player_id": 1, "name": "A", "pos": "RB", "proj": 10.0, "slot": "BE", "injury": "ACTIVE"},
          {"player_id": 2, "name": "B", "pos": "RB", "proj": 5.0, "slot": "RB", "injury": "ACTIVE"},
          {"player_id": 3, "name": "C", "pos": "RB", "proj": 0.0, "slot": "IR", "injury": "INJURY_RESERVE"}]
    cards = {c["player_id"]: c for c in season.roster_cards(ps, settings, week=5)}
    assert (cards[1]["group"], cards[1]["call"]) == ("bench", "START"), "ESPN benches him; the call says start"
    assert (cards[2]["group"], cards[2]["call"]) == ("starter", "BENCH")
    assert cards[3]["group"] == "ir"
    assert cards[1]["play_proj"] == 10.0 and isinstance(cards[1]["ros_adj"], float)


def test_starters_sort_in_espn_slot_order_whatever_the_position():
    settings = {"starters": {"QB": 1, "RB/WR/TE": 1, "K": 1}}
    ps = [{"player_id": 1, "name": "K", "pos": "K", "proj": 8.0, "slot": "K", "injury": "ACTIVE"},
          {"player_id": 2, "name": "Flex", "pos": "WR", "proj": 12.0, "slot": "RB/WR/TE", "injury": "ACTIVE"},
          {"player_id": 3, "name": "Q", "pos": "QB", "proj": 18.0, "slot": "QB", "injury": "ACTIVE"}]
    assert [c["slot"] for c in season.roster_cards(ps, settings, week=5)] == ["QB", "RB/WR/TE", "K"]


def test_empty_slots_lists_each_unfilled_starter_spot():
    settings = {"starters": {"RB": 2, "RB/WR/TE": 2}}
    ps = [{"player_id": 1, "pos": "RB", "slot": "RB"}, {"player_id": 2, "pos": "WR", "slot": "RB/WR/TE"},
          {"player_id": 3, "pos": "WR", "slot": "BE"}]
    assert season.empty_slots(ps, settings) == ["RB", "RB/WR/TE"]

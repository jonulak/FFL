"""One player's two-season game log off recorded kona_playercard rows and the 2025 pro schedule
(no network). Players: Josh Allen (QB, BUF, 2025 bye week 7), Bills D/ST, Jakobi Meyers (LV to JAX
after 2025 week 8), Jeremiyah Love (2026 rookie), Brandon Aubrey (K)."""
import json
from pathlib import Path

from ffl import player_card as pc

FX = Path(__file__).parent / "fixtures"
CARD = json.loads((FX / "espn_playercard.json").read_text())
SCHED25 = json.loads((FX / "pro_schedule_2025.json").read_text())
SCORING = json.loads((FX / "settings_scoring.json").read_text())["scoring"]


def player(pid):
    return next(p["player"] for p in CARD["players"] if p["player"]["id"] == pid)


def log25(pid, pos, club):
    return pc.season_log(player(pid)["stats"], 2025, pos, SCORING, pc.parse_games(SCHED25), pc.parse_byes(SCHED25), 18, club)


def test_qb_week_one_2025():
    s = log25(3918298, "QB", "BUF")
    w1 = s["weeks"][0]
    assert (w1["week"], w1["opp"], w1["home"], w1["status"]) == (1, "BAL", True, "played")
    assert [k for k, _ in w1["summary"]] == ["C/A", "YDS", "TD", "INT"]
    assert w1["pts"] == round(sum(b["points"] for b in w1["box"]), 2)
    assert w1["volume"]["label"] == "pass attempts" and w1["volume"]["count"] > 0


def test_gaps_bye_and_out():
    s = log25(3918298, "QB", "BUF")
    by_week = {w["week"]: w for w in s["weeks"]}
    assert by_week[7]["status"] == "bye" and by_week[7]["box"] == []
    assert len(s["weeks"]) == 18


def test_traded_club():
    s = log25(3916433, "WR", "JAX")
    by_week = {w["week"]: w for w in s["weeks"]}
    assert (by_week[6]["opp"], by_week[6]["home"]) == ("TEN", True)  # TEN@LV, still a Raider
    assert (by_week[9]["opp"], by_week[9]["home"]) == ("LV", False)  # JAX@LV, first game as a Jaguar
    assert by_week[8]["status"] == "bye"  # the Raiders' bye


def test_empty_row_is_a_game_missed():
    """ESPN writes a row for a game he sat out, with no stats (no 210, games played): Meyers
    missed 2025 week 7 at Kansas City. It reads as did-not-play and is not counted as a game."""
    s = log25(3916433, "WR", "JAX")
    w7 = next(w for w in s["weeks"] if w["week"] == 7)
    assert (w7["status"], w7["pts"], w7["box"]) == ("out", None, [])
    assert s["totals"]["games"] == 16


def test_rookie_has_no_2025():
    assert log25(4870808, "RB", "PIT") is None


def test_dst_summary_and_totals_sum():
    s = log25(-16002, "D/ST", "BUF")
    assert [k for k, _ in s["weeks"][0]["summary"]] == ["SCK", "INT", "PA"]
    t = s["totals"]
    assert t["games"] == sum(w["status"] == "played" for w in s["weeks"])
    assert t["pts"] == round(sum(w["pts"] for w in s["weeks"] if w["status"] == "played"), 2)
    assert round(sum(b["points"] for b in t["box"]), 2) == t["pts"]
    assert t["ppg"] == round(t["pts"] / t["games"], 1)


def test_summary_shapes():
    assert pc.summary("QB", {"0": 31, "1": 22, "3": 268, "4": 2}) == [["C/A", "22/31"], ["YDS", "268"], ["TD", "2"], ["INT", "0"]]
    assert pc.summary("RB", {"23": 18, "24": 97.0, "25": 1}) == [["CAR", "18"], ["YDS", "97"], ["TD", "1"]]
    assert pc.summary("WR", {"53": 6, "58": 9, "42": 72.0}) == [["REC/TGT", "6/9"], ["YDS", "72"], ["TD", "0"]]
    assert pc.summary("TE", {}) == [["REC/TGT", "0/0"], ["YDS", "0"], ["TD", "0"]]
    assert pc.summary("K", {"83": 2, "84": 3, "86": 4, "87": 4}) == [["FG", "2/3"], ["XP", "4/4"]]


def test_current_week_not_played_yet_is_omitted():
    rows = [r for r in player(3918298)["stats"] if r["seasonId"] == 2026 and r["scoringPeriodId"] <= 3]
    s = pc.season_log(rows, 2026, "QB", SCORING, {}, {"BUF": 99}, 4, "BUF")
    assert [w["week"] for w in s["weeks"]] == [1, 2, 3]


def test_player_log_both_seasons():
    card = {"players": [{"player": player(3916433)}]}
    log = pc.player_log(card, SCORING, {2025: SCHED25}, 4, 2026)
    assert (log["pos"], log["team"], log["current_week"], log["season"]) == ("WR", "JAX", 4, 2026)
    assert [s["season"] for s in log["seasons"]] == [2026, 2025]


def test_player_log_jersey():
    """His number rides along as ESPN writes it; a D/ST's missing or empty one reads None."""
    log = lambda p: pc.player_log({"players": [{"player": p}]}, SCORING, {}, 4, 2026)["jersey"]
    base = player(3916433)
    assert log({**base, "jersey": "41"}) == "41"
    assert log({**base, "jersey": ""}) is None
    assert log({k: v for k, v in base.items() if k != "jersey"}) is None


def test_player_log_history():
    """Past seasons' rows come from other calls, merged into the one stats list; each season
    keeps to its own rows, newest first, and a season he did not play is left out."""
    rows24 = json.loads((FX / "espn_playercard_2024.json").read_text())["players"][0]["player"]["stats"]
    allen = player(3918298)
    card = {"players": [{"player": {**allen, "stats": allen["stats"] + rows24}}]}
    log = pc.player_log(card, SCORING, {2025: SCHED25}, 4, 2026, history=3)
    assert [s["season"] for s in log["seasons"]] == [2026, 2025, 2024]
    s24 = log["seasons"][2]
    assert s24["totals"]["games"] == sum(w["status"] == "played" for w in s24["weeks"]) > 0
    assert all(w["opp"] is None for w in s24["weeks"])  # no 2024 schedule given: opponents unknown


def test_with_live_inserts_week():
    log = {"pos": "QB", "season": 2026, "seasons": [{"season": 2026, "weeks": [], "totals": None}], "current_week": 4, "team": "BUF"}
    out = pc.with_live(log, 4, {"0": 10, "1": 7, "3": 80}, SCORING, {"status": "in"})
    w = out["seasons"][0]["weeks"][-1]
    assert (w["week"], w["status"]) == (4, "live") and w["pts"] > 0
    assert log["seasons"][0]["weeks"] == []  # the cached payload is not touched


def test_with_live_first_game_of_the_season():
    """Back from a season-long absence: the live week opens this season, not last season's log."""
    last = {"season": 2025, "weeks": [], "totals": None}
    log = {"pos": "QB", "season": 2026, "seasons": [last], "current_week": 4, "team": "BUF"}
    out = pc.with_live(log, 4, {"0": 10, "1": 7, "3": 80}, SCORING, {"status": "in"})
    assert [s["season"] for s in out["seasons"]] == [2026, 2025]
    assert [w["week"] for w in out["seasons"][0]["weeks"]] == [4]


RANKS_DOC = {"positionAgainstOpponent": {"positionalRatings": {
    "1": {"average": 16.7, "ratingsByOpponent": {"2": {"average": 19.66, "rank": 25}, "12": {"average": 10.53, "rank": 2}}},
    "7": {"average": 1.0, "ratingsByOpponent": {"2": {"average": 1.0, "rank": 1}}},  # not a position we show
}}}


def test_parse_ranks():
    assert pc.parse_ranks(RANKS_DOC) == {"QB": {"BUF": {"rank": 25, "of": 2, "allowed": 19.7},
                                                "KC": {"rank": 2, "of": 2, "allowed": 10.5}}}
    assert pc.parse_ranks({}) == {}


def test_schedule_ahead_byes_and_ranks():
    ranks = pc.parse_ranks(RANKS_DOC)["QB"]
    ahead = pc.schedule_ahead(SCHED25, "BUF", 4, ranks, now=0)
    assert [w["week"] for w in ahead] == list(range(5, 19))
    by_week = {w["week"]: w for w in ahead}
    assert by_week[7]["status"] == "bye" and "opp_rank" not in by_week[7]
    assert all(w["status"] == "upcoming" and w["opp"] and w["pts"] is None for w in ahead if w["week"] != 7)
    kc = next(w for w in ahead if w["opp"] == "KC")
    assert kc["opp_rank"] == {"rank": 2, "of": 2, "allowed": 10.5}
    assert all(w["opp_rank"] is None for w in ahead if w["status"] == "upcoming" and w["opp"] not in ranks)


def test_schedule_ahead_drops_a_game_past():
    """Four hours after kickoff a game is no longer ahead, though ESPN has not written its row."""
    every = pc.schedule_ahead(SCHED25, "BUF", 0, {}, now=0)
    kick = next(g["date"] for t in SCHED25["settings"]["proTeams"] if t["abbrev"].upper() == "BUF"
                for g in t["proGamesByScoringPeriod"]["1"]) / 1000
    later = pc.schedule_ahead(SCHED25, "BUF", 0, {}, now=kick + pc.GAME_S + 1)
    assert [w["week"] for w in later] == [w["week"] for w in every][1:]
    assert pc.schedule_ahead(SCHED25, None, 0, {}, now=0) == []


def test_player_log_weeks_ahead():
    """The current week not yet played opens the weeks ahead; the totals count only games played."""
    allen = player(3918298)
    rows = [r for r in allen["stats"] if not (r.get("seasonId") == 2026 and (r.get("scoringPeriodId") or 0) >= 4)]
    card = {"players": [{"player": {**allen, "stats": rows}}]}
    log = pc.player_log(card, SCORING, {2026: SCHED25}, 4, 2026, ranks=pc.parse_ranks(RANKS_DOC), now=0)
    now = log["seasons"][0]
    assert [w["week"] for w in now["weeks"]] == list(range(1, 19))
    assert [w["status"] for w in now["weeks"][3:]] == ["upcoming"] * 3 + ["bye"] + ["upcoming"] * 11
    assert now["totals"]["games"] == sum(w["status"] == "played" for w in now["weeks"])


def test_week_projections_mean_this_week_sleeper_after():
    """ESPN projects only the current week, so it is averaged with Sleeper there and the weeks
    after are Sleeper's alone; a week Sleeper has no row for him has no projection."""
    p = {"fullName": "Josh Allen", "stats": [
        {"seasonId": 2026, "statSourceId": 1, "statSplitTypeId": 1, "scoringPeriodId": 5, "appliedTotal": 22.24},
        {"seasonId": 2026, "statSourceId": 1, "statSplitTypeId": 0, "scoringPeriodId": 0, "appliedTotal": 285.5}]}
    sleeper = {5: {("josh allen", "QB"): 20.0}, 6: {("josh allen", "QB"): 19.46}, 8: {("someone else", "QB"): 9.0}}
    out = pc.week_projections(p, "QB", "BUF", 2026, 5, sleeper)
    assert out[5] == {"proj": 21.1}
    assert out[6] == {"proj": 19.5}
    assert out[8] == pc.NO_PROJ
    dst = pc.week_projections({"fullName": "Commanders D/ST", "stats": []}, "D/ST", "WSH", 2026, 5,
                              {5: {("WAS", "D/ST"): 6.0}})  # ESPN's WSH is Sleeper's WAS
    assert dst[5]["proj"] == 6.0


def test_week_projections_current_week_is_the_consensus():
    p = {"fullName": "Jahmyr Gibbs", "stats": [{"seasonId": 2026, "statSourceId": 1, "statSplitTypeId": 1,
                                                "scoringPeriodId": 5, "appliedTotal": 22.2}]}
    cur = {"sleeper": {("jahmyr gibbs", "RB"): 20.0}, "cbs": {("jahmyr gibbs", "RB"): 30.0}}
    out = pc.week_projections(p, "RB", "DET", 2026, 5, {6: {("jahmyr gibbs", "RB"): 19.5}}, current=cur)
    assert out[5] == {"proj": 23.6}, "Walsh averages of 20, 22.2, 30: 20, 21.1, 22.2, 25, 26.1, 30 -> (22.2 + 25) / 2"
    assert out[6] == {"proj": 19.5}, "later weeks: Sleeper's"


def test_player_log_ranks_and_projections():
    """Ranks on the games played this season as well as ahead; projections on the games ahead."""
    allen = player(3918298)
    rows = [r for r in allen["stats"] if not (r.get("seasonId") == 2026 and (r.get("scoringPeriodId") or 0) >= 4)]
    games = pc.parse_games(SCHED25)
    played = [{**r, "externalId": next(g for g, (wk, a, h) in games.items() if wk == r["scoringPeriodId"] and "BUF" in (a, h))}
              for r in pc._game_rows(rows, 2026)]  # the 2026 games as if on the 2025 schedule, so they have opponents
    card = {"players": [{"player": {**allen, "stats": [r for r in rows if r not in pc._game_rows(rows, 2026)] + played}}]}
    ranks = {"QB": {"BAL": {"rank": 3, "of": 32, "allowed": 12.0}, "KC": {"rank": 2, "of": 32, "allowed": 10.5}}}
    sleeper = {wk: {("josh allen", "QB"): 20.0} for wk in range(4, 19)}
    log = pc.player_log(card, SCORING, {2026: SCHED25}, 4, 2026, ranks=ranks, now=0, sleeper=sleeper)
    weeks = {w["week"]: w for w in log["seasons"][0]["weeks"]}
    assert weeks[1]["opp"] == "BAL" and weeks[1]["opp_rank"] == ranks["QB"]["BAL"] and "proj" not in weeks[1]
    assert all("opp_rank" in w for w in weeks.values() if w["opp"])
    assert weeks[4]["status"] == "upcoming" and weeks[4]["proj"] == 20.0


def test_with_live_keeps_the_week_ahead_opponent():
    log = {"pos": "QB", "season": 2026, "current_week": 4, "team": "BUF", "seasons": [{"season": 2026, "totals": None, "weeks": [
        {**pc._blank(4, "upcoming"), "opp": "KC", "home": True, "opp_rank": {"rank": 2, "of": 32, "allowed": 10.5}}, pc._blank(5, "bye")]}]}
    out = pc.with_live(log, 4, {"0": 10, "1": 7, "3": 80}, SCORING, {"status": "in"})
    w4, w5 = out["seasons"][0]["weeks"]
    assert (w4["status"], w4["opp"], w4["home"], w4["opp_rank"]["rank"], w5["status"]) == ("live", "KC", True, 2, "bye")


def _listed(pid, pos_id, weeks):
    return {"player": {"id": pid, "defaultPositionId": pos_id, "stats": [
        {"seasonId": 2026, "statSourceId": 0, "statSplitTypeId": 1, "scoringPeriodId": wk,
         "appliedTotal": pts, "stats": {"210": 1} if pts is not None else {}} for wk, pts in weeks.items()]}}


def test_parse_pos_ranks():
    """Season rank by points to date, each week's among those who played it; a tie shares the
    higher rank, a game sat out (no stat 210) is not ranked, another position is left out."""
    doc = {"players": [_listed(1, 3, {1: 20.0, 2: 5.0}), _listed(2, 3, {1: 20.0, 2: 10.0}),
                       _listed(3, 3, {1: 32.0, 2: None}), _listed(4, 2, {1: 99.0})]}
    r = pc.parse_pos_ranks(doc, "WR", 2026)
    assert r["season"] == {3: 1, 2: 2, 1: 3}
    assert r["weeks"] == {1: {3: 1, 1: 2, 2: 2}, 2: {2: 1, 1: 2}}
    tied = pc.parse_pos_ranks({"players": [_listed(1, 3, {1: 10.0}), _listed(2, 3, {1: 10.0}), _listed(3, 3, {1: 4.0})]}, "WR", 2026)
    assert tied["season"] == {1: 1, 2: 1, 3: 3}
    assert pc.parse_pos_ranks({}, "WR", 2026) == {"season": {}, "weeks": {}}


def test_player_log_pos_ranks():
    """His rank on the season's totals and on every week played before the current one; the
    current week, still being played, carries none."""
    allen = player(3918298)
    played = [r["scoringPeriodId"] for r in pc._game_rows(allen["stats"], 2026)]
    pos_ranks = {"season": {3918298: 4}, "weeks": {wk: {3918298: wk} for wk in played}}
    current = max(played)
    card = {"players": [{"player": allen}]}
    now = pc.player_log(card, SCORING, {2026: SCHED25}, current, 2026, now=0, pos_ranks=pos_ranks)["seasons"][0]
    assert now["totals"]["pos_rank"] == 4
    by_week = {w["week"]: w for w in now["weeks"]}
    assert all(by_week[wk]["pos_rank"] == wk for wk in played if wk < current)
    assert "pos_rank" not in by_week[current]
    bare = pc.player_log(card, SCORING, {2026: SCHED25}, current, 2026, now=0)
    assert bare["seasons"][0]["totals"]["pos_rank"] is None


def test_player_log_snaps():
    """This season's games played carry his share of the offensive snaps (Sleeper's, a day behind:
    a game without them yet reads None), and the season's totals the share over every game with
    them; past seasons carry none."""
    allen = player(3918298)
    played = [r["scoringPeriodId"] for r in pc._game_rows(allen["stats"], 2026)]
    first, second, *rest = played
    snaps = {first: (60.0, 64.0), second: (55.0, 70.0)}
    card = {"players": [{"player": allen}]}
    log = pc.player_log(card, SCORING, {2025: SCHED25, 2026: SCHED25}, max(played), 2026, now=0, snaps=snaps)
    now, past = log["seasons"][0], log["seasons"][1]
    by_week = {w["week"]: w for w in now["weeks"]}
    assert by_week[first]["snap_pct"] == 94  # 60 of 64
    assert by_week[second]["snap_pct"] == 79  # 55 of 70
    assert all(by_week[wk]["snap_pct"] is None for wk in rest)
    assert (now["totals"]["snap_pct"], now["totals"]["snap_games"]) == (86, 2)  # 115 of 134
    assert all("snap_pct" not in w for w in now["weeks"] if w["status"] not in ("played", "live"))
    assert "snap_pct" not in past["totals"] and all("snap_pct" not in w for w in past["weeks"])
    bare = pc.player_log(card, SCORING, {2026: SCHED25}, max(played), 2026, now=0)["seasons"][0]
    assert bare["totals"]["snap_pct"] is None and all(w.get("snap_pct") is None for w in bare["weeks"])


def test_with_live_keeps_the_weeks_snaps():
    log = {"pos": "QB", "season": 2026, "current_week": 4, "team": "BUF", "seasons": [{"season": 2026, "totals": None, "weeks": [
        {**pc._blank(4, "played"), "opp": "KC", "home": True, "snap_pct": 97}]}]}
    out = pc.with_live(log, 4, {"0": 10, "1": 7, "3": 80}, SCORING, {"status": "post"})
    assert out["seasons"][0]["weeks"][0]["snap_pct"] == 97

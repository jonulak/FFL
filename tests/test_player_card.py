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
    assert pc.summary("TE", {}) == [["REC", "0"], ["YDS", "0"], ["TD", "0"]]
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

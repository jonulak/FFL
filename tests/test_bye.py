"""Bye-week risk in ffl.draft_watch.recommend."""
import pandas as pd

from ffl import board as board_mod
from ffl import draft_watch as dw

from ffl.adp import norm_name

from .test_board import make_players

TWO_QB = {
    "team_count": 10,
    "starters": {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1, "RB/WR/TE": 2},
    "roster_size": 17,
    "name": "2qb",
    "season": 2026,
}


def adp_with_byes(players: pd.DataFrame, byes: dict[int, int]) -> pd.DataFrame:
    """Bye weeks reach the board through the ADP feed, so put them where they really come from."""
    df = players.copy()
    df["key"] = df.name.map(norm_name)
    df["bye"] = df.player_id.map(byes).fillna(1).astype(float)
    for c in ["adp", "adp_rank", "adp_pos_rank", "adp_1qb", "adp_1qb_rank"]:
        df[c] = float("nan")
    return df[["key", "pos", "team", "bye", "adp", "adp_rank", "adp_pos_rank", "adp_1qb", "adp_1qb_rank"]]


def qb_ids(players, n):
    return [int(p) for p in players[players.pos == "QB"].sort_values("proj", ascending=False).head(n).player_id]


def test_second_qb_sharing_a_bye_is_penalised_in_a_2qb_league():
    """The case that matters tomorrow: both starting QBs off in the same week.

    All 20+ usable QBs are rostered in a 2QB league, so there is no waiver replacement —
    the fallback is whatever is left on the board, which is much worse.
    """
    players = make_players()
    qbs = qb_ids(players, 6)
    adp = adp_with_byes(players, {qbs[0]: 13, qbs[1]: 13, qbs[2]: 9})
    bd = board_mod.build(TWO_QB, players, adp, drafted_ids={qbs[0]})
    my_roster = bd[bd.player_id == qbs[0]]  # my QB1, bye 13

    rec = dw.recommend(bd, TWO_QB, my_roster, picks_until_me=1)
    by_id = {r["name"]: r for r in rec["by_pos"]["QB"]}
    names = {int(p): n for p, n in zip(players.player_id, players.name)}
    clash, clear = by_id[names[qbs[1]]], by_id[names[qbs[2]]]

    # Every QB costs something in his own bye week — that is unavoidable and uniform. What has
    # to hold is that stacking onto QB1's bye costs strictly more than spreading.
    assert clash["bye_risk"] > clear["bye_risk"] > 0, (
        f"stacked {clash['bye_risk']} vs clean {clear['bye_risk']}")
    # the clashing QB projects higher, so only the penalty can reorder them
    assert clash["proj"] > clear["proj"]
    assert clear["score"] > clash["score"], "a clean bye should win when projections are close"


def test_first_qb_is_never_penalised():
    """With no QB yet, every QB would 'clash' with an empty roster — must not fire."""
    players = make_players()
    bd = board_mod.build(TWO_QB, players, adp_with_byes(players, {}), drafted_ids=set())
    rec = dw.recommend(bd, TWO_QB, bd.head(0), picks_until_me=1)
    assert all(r["bye_risk"] == 0 for r in rec["by_pos"]["QB"])


def test_stacked_kicker_bye_is_nearly_free():
    """Same collision at K should cost ~nothing: the replacement is just as good."""
    players = make_players()
    ks = [int(p) for p in players[players.pos == "K"].sort_values("proj", ascending=False).head(3).player_id]
    adp = adp_with_byes(players, {ks[0]: 7, ks[1]: 7})
    bd = board_mod.build(TWO_QB, players, adp, drafted_ids={ks[0]})
    my_roster = bd[bd.player_id == ks[0]]

    penalties = [
        dw.bye_penalty("K", 7, my_roster, TWO_QB, vorp=float(bd[bd.player_id == ks[1]].vorp.iloc[0]))
    ]
    k_penalty = penalties[0]
    # same collision shape at QB in a 2QB league, where the fallback is genuinely bad
    qb_penalty = dw.bye_penalty("QB", 13, my_roster.assign(pos="QB"), TWO_QB, vorp=60.0)
    assert k_penalty < 1.0, f"stacked K bye priced at {k_penalty}, should be near zero"
    assert qb_penalty > 5 * k_penalty, (
        f"penalty must scale with position scarcity: QB {qb_penalty} vs K {k_penalty}")

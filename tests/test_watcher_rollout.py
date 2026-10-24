"""The watcher prints the rollout under the table when asked, and not otherwise."""
import threading
import time

import numpy as np
import pandas as pd

from ffl import board as board_mod
from ffl import draft_watch as dw

from .test_board import make_players
from .test_rollout import SETTINGS_2QB


def _inputs():
    players = make_players(60)
    adp = players.sort_values("proj", ascending=False).reset_index(drop=True)
    adp = pd.DataFrame({"key": adp.name.map(board_mod.norm_name), "pos": adp.pos, "team": adp.team,
                        "adp": np.arange(1, len(adp) + 1, dtype=float),
                        "adp_rank": np.arange(1, len(adp) + 1), "adp_pos_rank": 1,
                        "adp_1qb": np.nan, "adp_1qb_rank": np.nan, "bye": 7.0, "adp_stdev": 6.0})
    settings = dict(SETTINGS_2QB)
    settings["teams"] = [{"id": t, "name": f"Team {t}", "abbrev": f"T{t}"} for t in range(1, 11)]
    settings["draft"] = {"pick_order": list(range(1, 11)), "order_type": "DRAFT_START"}
    settings["league_id"] = 0
    return settings, players, adp


def _watcher(monkeypatch, tmp_path, flag: str | None, ref=None):
    if flag is None:
        monkeypatch.delenv("FFL_ROLLOUT", raising=False)
    else:
        monkeypatch.setenv("FFL_ROLLOUT", flag)
    monkeypatch.setenv("FFL_ROLLOUT_SIMS", "20")
    settings, players, adp = _inputs()
    monkeypatch.setattr(board_mod, "load_inputs", lambda ref=None: (settings, players, adp))
    monkeypatch.setattr(dw, "my_team_id", lambda s, ref=None: 3)
    return dw.Watcher(ref), players


def _wait_for_rollout_threads():
    for _ in range(600):
        if not any(t.name.startswith("rollout-") for t in threading.enumerate()):
            return
        time.sleep(0.05)


def test_rollout_line_printed_on_the_clock_when_enabled(monkeypatch, tmp_path, capsys):
    w, players = _watcher(monkeypatch, tmp_path, "1")
    picks = [{"teamId": t, "playerId": int(players.iloc[i].player_id), "overallPickNumber": i + 1,
              "roundId": 1, "roundPickNumber": i + 1} for i, t in enumerate([1, 2])]
    state = w.tick(picks, on_clock=3)
    assert state["on_clock_is_me"]
    w.print_board(state)
    _wait_for_rollout_threads()
    out = capsys.readouterr().out
    assert "rollout    " in out and "for pick #3" in out
    assert "failed" not in out


def test_no_rollout_line_without_the_flag(monkeypatch, tmp_path, capsys):
    w, players = _watcher(monkeypatch, tmp_path, None)
    picks = [{"teamId": t, "playerId": int(players.iloc[i].player_id), "overallPickNumber": i + 1,
              "roundId": 1, "roundPickNumber": i + 1} for i, t in enumerate([1, 2])]
    state = w.tick(picks, on_clock=3)
    w.print_board(state)
    _wait_for_rollout_threads()
    out = capsys.readouterr().out
    assert "rollout" not in out
    assert w.rollout_mod is None


def test_rollout_not_run_far_from_my_pick(monkeypatch, tmp_path, capsys):
    w, players = _watcher(monkeypatch, tmp_path, "1")
    state = w.tick([], on_clock=1)   # pick 1: I am two picks away, that is the heads-up
    assert state["picks_until_me"] == 2
    w.print_board(state)
    _wait_for_rollout_threads()
    assert "rollout    " in capsys.readouterr().out
    # advance to a state five picks from mine: no rollout
    w2, players = _watcher(monkeypatch, tmp_path, "1")
    monkeypatch.setattr(dw, "my_team_id", lambda s, ref=None: 8)
    w2 = dw.Watcher()
    state = w2.tick([], on_clock=1)
    assert state["picks_until_me"] > 2
    w2.print_board(state)
    _wait_for_rollout_threads()
    assert "rollout    " not in capsys.readouterr().out

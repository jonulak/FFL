"""Replay a finished draft through the recommender and the rollout, from one seat.

    uv run python -m ffl.replay journal/draft/practice-8-slot4.csv            # my seat (team id from settings)
    uv run python -m ffl.replay journal/draft/practice-9-slot1.csv --team 7
    uv run python -m ffl.replay data/ffc/18509038.csv --team 3 --sims 100      # an FFC log, seat 3
    uv run python -m ffl.replay data/ffc/*.csv --human                         # every human seat in every log

For every pick the chosen seat made, rebuild the board as it stood, run `recommend()` and the
rollout, and report what the seat took, what each would have taken, and how the rollout scores
the three against each other. The rollout's own number is the yardstick (season lineup points
of the roster each choice leads to, opponents drafting to ADP), so "the human lost 12" means
12 points of that — a model's opinion of a human, not the truth. The point is to see where
the two rankings part from each other and from people, and by how much.

Log format: the watcher's pick CSV (`overall, round, pick, team_id, player_id, pos, ...`).
FFC logs come out of `ffl.ffc` in the same shape, with `human` marking the seats people sat in.
"""
from __future__ import annotations

import argparse
import glob
import sys

import pandas as pd

from . import board as board_mod
from . import rollout as ro
from .draft_watch import (my_team_id, opponent_demand, recommend, roster_counts, snake_order,
                          turn_math)


def load_log(path: str) -> pd.DataFrame:
    log = pd.read_csv(path)
    need = {"overall", "round", "team_id", "player_id"}
    missing = need - set(log.columns)
    if missing:
        raise SystemExit(f"{path}: missing columns {sorted(missing)}")
    return log.sort_values("overall").reset_index(drop=True)


def seats(log: pd.DataFrame, human_only: bool) -> list[int]:
    if human_only and "human" in log.columns:
        return sorted(int(t) for t in log[log.human.astype(bool)].team_id.unique())
    return sorted(int(t) for t in log.team_id.unique())


def replay(log: pd.DataFrame, me: int, settings: dict, players: pd.DataFrame, adp: pd.DataFrame,
           n_sims: int, verbose: bool = True, sims_budget_s: float = 8.0) -> dict:
    round1 = log[log["round"] == 1].sort_values("overall")
    order = [int(t) for t in round1.team_id]
    rounds = int(log["round"].max())
    n = len(order)
    if me not in order:
        raise SystemExit(f"team {me} did not pick in round 1 of this log")
    # The log decides the shape of the draft: its teams, its round count.
    st = dict(settings)
    st["teams"] = [{"id": t, "name": f"Team {t}", "abbrev": f"T{t}"} for t in order]
    st["roster_size"] = rounds
    full = snake_order(order, rounds)
    id_to_row = players.set_index("player_id")
    picks: list[dict] = []
    rows = []
    for r in log.itertuples():
        p = int(r.overall)
        if int(r.team_id) == me and int(r.player_id) in id_to_row.index:
            drafted = {q["playerId"] for q in picks}
            bd = board_mod.build(st, players, adp, drafted_ids=drafted)
            roster = bd[bd.player_id.isin({q["playerId"] for q in picks if q["teamId"] == me})]
            tm = turn_math(full, me, p)
            by_team = roster_counts(picks, id_to_row)
            opp = opponent_demand(st, [by_team.get(t, {}) for t in tm["window"]])
            rec = recommend(bd, st, roster, 0, tm["my_picks_left"], horizon=tm["horizon"], opp_demand=opp)
            score_top = rec["top"][0] if rec["top"] else None
            human_id = int(r.player_id)
            # the human's pick is always scored, whether or not the table had him as a candidate
            cand_ids = [int(x["player_id"]) for x in rec["top"][:8]]
            pool = ro.Pool.from_board(bd)
            extra = ro.candidate_indices(pool, rec, by_team.get(me, {}), st)
            cand_ids += [int(pool.ids[i]) for i in extra if int(pool.ids[i]) not in cand_ids]
            if human_id not in cand_ids and not bd[bd.player_id == human_id].drafted.iloc[0]:
                cand_ids.append(human_id)
            res = ro.rollout(bd, st, full, me, p, by_team, roster, rec, n_sims=n_sims,
                             time_budget_s=sims_budget_s, seed=p, candidates=cand_ids)
            gaps = ro.roll_by_id(res)
            lead = res["rows"][0] if res["rows"] else None
            human_row = id_to_row.loc[human_id]
            row = {"pick": p, "round": int(r.round), "human": human_row["name"], "human_pos": human_row["pos"],
                   "human_gap": gaps.get(human_id), "score_pick": score_top["name"] if score_top else None,
                   "score_gap": gaps.get(int(score_top["player_id"])) if score_top else None,
                   "roll_pick": lead["name"] if lead else None, "roll_pos": lead["pos"] if lead else None,
                   "n": res["n_sims"]}
            rows.append(row)
            if verbose:
                hg = f"{row['human_gap']:+.1f}" if row["human_gap"] is not None else "  n/a"
                sg = f"{row['score_gap']:+.1f}" if row["score_gap"] is not None else "  n/a"
                print(f"#{p:>3} R{row['round']:<2} took {row['human']:<22} {row['human_pos']:<4} {hg:>6} | "
                      f"score {row['score_pick']:<22} {sg:>6} | rollout {row['roll_pick']} ({row['roll_pos']})"
                      f"{'   <- rollout disagrees with score' if lead and score_top and int(lead['player_id']) != int(score_top['player_id']) else ''}",
                      flush=True)
        picks.append({"teamId": int(r.team_id), "playerId": int(r.player_id), "overallPickNumber": p})
    df = pd.DataFrame(rows)
    return {"team": me, "rows": df,
            "human_loss": float(-df.human_gap.dropna().sum()) if len(df) else 0.0,
            "score_loss": float(-df.score_gap.dropna().sum()) if len(df) else 0.0,
            "score_agrees": int(((df.score_gap == 0) if len(df) else pd.Series(dtype=bool)).sum()),
            "n_picks": len(df)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--team", type=int, default=None, help="seat (team id) to replay; default my team")
    ap.add_argument("--human", action="store_true", help="every seat marked human in the log")
    ap.add_argument("--sims", type=int, default=100)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    settings, players, adp = board_mod.load_inputs()
    paths = [p for g in a.logs for p in sorted(glob.glob(g))]
    summary = []
    for path in paths:
        log = load_log(path)
        if a.human:
            team_ids = seats(log, human_only=True)
        else:
            team_ids = [a.team if a.team is not None else my_team_id(settings)]
        for me in team_ids:
            print(f"\n=== {path}  seat {me} ===")
            res = replay(log, me, settings, players, adp, a.sims, verbose=not a.quiet)
            print(f"  over {res['n_picks']} picks: the seat's choices cost {res['human_loss']:.0f} vs the rollout's; "
                  f"score's would have cost {res['score_loss']:.0f}; score and rollout agreed on {res['score_agrees']}")
            summary.append({"log": path, "seat": me, **{k: v for k, v in res.items() if k not in ("rows", "team")}})
    if len(summary) > 1:
        df = pd.DataFrame(summary)
        print("\n=== SUMMARY ===")
        print(df.to_string(index=False))
        print(f"\nmean per draft: seat cost {df.human_loss.mean():.0f}, score cost {df.score_loss.mean():.0f}, "
              f"agreement {df.score_agrees.sum()}/{df.n_picks.sum()}")


if __name__ == "__main__":
    main()

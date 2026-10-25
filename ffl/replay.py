"""Replay a finished draft through the recommender and the rollout, from one seat.

    uv run python -m ffl.replay practice-8-slot4               # a stored log, my seat (team id from settings)
    uv run python -m ffl.replay practice:practice-9-slot1 --team 7
    uv run python -m ffl.replay ffc:18509038 --team 3 --sims 100  # an FFC log, seat 3
    uv run python -m ffl.replay --human                          # every human seat in every stored FFC log
    uv run python -m ffl.replay --league espn:100000001         # a league's draft, off the DB's draft_picks
    FFL_DATA_DIR=data-mock uv run python -m ffl.replay --league espn:<mock id>  # a practice draft (its own ffl.db)
    uv run python -m ffl.replay --keep practice-14-slot2 --from data-mock/ffl.db --league espn:<mock id>  # keep it

For every pick the chosen seat made, rebuild the board as it stood, run `recommend()` and the
rollout, and report what the seat took, what each would have taken, and how the rollout scores
the three against each other. The rollout's own number is the yardstick (season lineup points
of the roster each choice leads to, opponents drafting to ADP), so "the human lost 12" means
12 points of that — a model's opinion of a human, not the truth. The point is to see where
the two rankings part from each other and from people, and by how much.

Log format: the watcher's pick log (`overall, round, pick, team_id, player_id, pos, ...`): the DB's
draft_picks for `--league`, or a stored log in the `draft_logs` table, named `<source>:<id>` (a bare id
when only one source has it; a bare source, `ffc` or `practice`, is every log in it). Source `practice`
holds the practice and mock drafts once kept as journal/draft/*.csv (their file stems), `ffc` what
`ffl.ffc fetch` stored, with `human` marking the seats people sat in. The board is the league's
(`--league`, else FFL_LEAGUE, else the home league).
"""
from __future__ import annotations

import argparse

import pandas as pd

from . import board as board_mod
from . import adp as adp_mod
from . import db, leagues, strategy
from . import rollout as ro
from .draft_watch import (my_team_id, opponent_demand, recommend, roster_counts, snake_order,
                          turn_math)


SOURCES = ("ffc", "practice")


def resolve_logs(names: list[str]) -> list[tuple[str, str]]:
    """Stored-log names -> (source, log_id): `source:id`, a bare source (every log in it), or a bare id that
    only one source has."""
    out = []
    for name in names:
        source, _, log_id = name.partition(":") if ":" in name else (None, "", name)
        if source is None and log_id in SOURCES:
            got = [(r["source"], r["log_id"]) for r in db.draft_logs_rows(log_id)]
            if not got:
                raise SystemExit(f"no stored log in source {log_id!r} (`uv run python -m ffl.ffc fetch` for FFC's)")
            out += got
            continue
        hits = [(r["source"], r["log_id"]) for r in db.draft_logs_rows(source, log_id)]
        if not hits:
            raise SystemExit(f"{name}: no stored log by that name (`uv run python -m ffl.ffc list` shows FFC's)")
        if len(hits) > 1:
            raise SystemExit(f"{name}: more than one stored log has that id: "
                             + ", ".join(f"{s}:{i}" for s, i in hits))
        out += hits
    return out


def keep(name: str, path, league: str) -> None:
    """Store `league`'s draft from another ffl.db (a practice run's `data-mock/ffl.db`) as the practice log `name`."""
    rows = db.picks_in(path, league)
    if not rows:
        raise SystemExit(f"{path}: no picks for {league}")
    if not db.put_draft_log("practice", name, rows, {"from": str(path), "league": league}):
        raise SystemExit(f"practice:{name} is already stored; pick another name")
    print(f"kept {len(rows)} picks as practice:{name} (uv run python -m ffl.replay practice:{name})")


def load_log(name: str | None = None, ref=None) -> pd.DataFrame:
    """A pick log: the stored log `name` (see resolve_logs; one log), or `ref`'s draft_picks. A stored log
    reads back as the frame pd.read_csv gave from the file it once was."""
    if name is not None:
        [(source, log_id)] = resolve_logs([name])
        rows = db.draft_logs_rows(source, log_id)[0]["rows"]
        log = adp_mod.csv_frame(pd.DataFrame(rows)) if rows else pd.DataFrame()
        name = f"{source}:{log_id}"
    else:
        log = pd.DataFrame(db.picks(ref))
        name = f"{ref.key} draft_picks"
    if not len(log):
        raise SystemExit(f"{name}: no picks")
    need = {"overall", "round", "team_id", "player_id"}
    missing = need - set(log.columns)
    if missing:
        raise SystemExit(f"{name}: missing columns {sorted(missing)}")
    return log.sort_values("overall").reset_index(drop=True)


def seats(log: pd.DataFrame, human_only: bool) -> list[int]:
    if human_only and "human" in log.columns:
        return sorted(int(t) for t in log[log.human.astype(bool)].team_id.unique())
    return sorted(int(t) for t in log.team_id.unique())


def replay(log: pd.DataFrame, me: int, settings: dict, players: pd.DataFrame, adp: pd.DataFrame,
           n_sims: int, verbose: bool = True, sims_budget_s: float = 8.0, caps: dict[str, int] | None = None,
           qb_pace: float | None = None) -> dict:
    """caps / qb_pace: the league's (None reads the current league's); main reads them once per run."""
    if caps is None:
        caps = strategy.caps()
    if qb_pace is None:
        qb_pace = strategy.qb_pace()
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
            rec = recommend(bd, st, roster, 0, tm["my_picks_left"], horizon=tm["horizon"], opp_demand=opp,
                            caps=caps)
            score_top = rec["top"][0] if rec["top"] else None
            human_id = int(r.player_id)
            # the human's pick is always scored, whether or not the table had him as a candidate
            cand_ids = [int(x["player_id"]) for x in rec["top"][:8]]
            pool = ro.Pool.from_board(bd, qb_pace)
            extra = ro.candidate_indices(pool, rec, by_team.get(me, {}), st, caps=caps)
            cand_ids += [int(pool.ids[i]) for i in extra if int(pool.ids[i]) not in cand_ids]
            if human_id not in cand_ids and not bd[bd.player_id == human_id].drafted.iloc[0]:
                cand_ids.append(human_id)
            res = ro.rollout(bd, st, full, me, p, by_team, roster, rec, n_sims=n_sims,
                             time_budget_s=sims_budget_s, seed=p, candidates=cand_ids, caps=caps, qb_pace=qb_pace)
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
    ap.add_argument("logs", nargs="*", help="stored logs: source:id, a bare id, or a bare source (ffc, practice)")
    ap.add_argument("--league", default=None, help="replay this league's draft off the DB's draft_picks (a key)")
    ap.add_argument("--team", type=int, default=None, help="seat (team id) to replay; default my team")
    ap.add_argument("--human", action="store_true", help="every seat marked human in the log (no logs named: every FFC log)")
    ap.add_argument("--sims", type=int, default=100)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--keep", metavar="NAME", help="store --league's draft from the ffl.db at --from as practice:NAME")
    ap.add_argument("--from", dest="src", metavar="DB", help="with --keep: the other ffl.db (e.g. data-mock/ffl.db)")
    a = ap.parse_args()
    if a.keep:
        if not (a.src and a.league):
            ap.error("--keep NAME needs --from DB and --league KEY")
        keep(a.keep, a.src, a.league)
        return
    if not a.logs and not a.league and not a.human:
        ap.error("name stored logs, or --league KEY, or --human for every FFC log")
    ref = leagues.get(a.league) if a.league else leagues.current()
    settings, players, adp = board_mod.load_inputs(ref)
    caps, qb_pace = strategy.caps(ref), strategy.qb_pace(ref)
    names = [f"{s}:{i}" for s, i in resolve_logs(a.logs or (["ffc"] if a.human and not a.league else []))]
    logs = [(name, load_log(name)) for name in names]
    if a.league:
        logs.append((f"{ref.key} draft_picks", load_log(ref=ref)))
    summary = []
    for path, log in logs:
        if a.human:
            team_ids = seats(log, human_only=True)
        else:
            team_ids = [a.team if a.team is not None else my_team_id(settings, ref)]
        for me in team_ids:
            print(f"\n=== {path}  seat {me} ===")
            res = replay(log, me, settings, players, adp, a.sims, verbose=not a.quiet, caps=caps, qb_pace=qb_pace)
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

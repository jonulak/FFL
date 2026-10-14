---
name: pace-report
description: Use when Jonathan asks who is beating projections, outperforming, breaking out, or worth sniping or trading for league-wide (all rosters, not just the wire), or asks for the weekly pace / overperformer scan. Not for lineup calls or the waiver table for one position.
---

# Pace report

League-wide scan for players outscoring their rest-of-season model, with the facts that explain it
beside each name. The code makes the table; the call on *why* is made by hand from its columns.

## Run

```
uv run python -m ffl.pace                     # top 14 per position, two-game windows
uv run python -m ffl.pace --min-games 1       # include one-game lines (say so in the write-up)
uv run python -m ffl.pace --json data/pace.json
```

About a minute (box scores for every form week, four free-agent pools, the public scoreboard).
From a worktree there is no `.env` or `data/`, and the API on :8300 cannot do this: run it from the
main checkout or hand Jonathan the command. My roster is printed under the tables, every position,
so drops and the flex rule are judged from the same output.

## Read the columns

| column | meaning |
|---|---|
| where | `ME` + slot; owner's team + slot; `FA`. Slot `BE` is benched production, `IR` is the IR slot, `FLEX` is the flex (ESPN's `RB/WR/TE`) |
| ppg / beat | points a game over the window; over ESPN's weekly projection for those games. `beat` can be negative on a row the model has low: "on projection, model behind him", not a breakout |
| pace | ppg × games left − model ROS. Two hot games times fifteen is a big number: rank by it, judge by the rest |
| ros → adj | model ROS (consensus of Sleeper, CBS, FantasySharks) → usage-adjusted ROS (STRATEGY.md) |
| snap | offensive snap share over the window |
| opp / tr | touches + targets a game (pass attempts for a QB); `tr` is the last game's opp minus the mean of the earlier games, a difference in touches |
| tsn / ypt | targets per snap and yards a target. Lead WRs and TEs run about .20 tsn; .10 is a third option. ypt under 8 with a high beat is touchdowns, not usage |
| exp | years in the league; 0 is a rookie |
| ahead | hurt teammates at his position with return week. Return week = the current week means "listed back this week", so the fill-in window may already be closing |
| opponents | `TEAM#rank`, defensive rank by points allowed over the played weeks, 1 = stingiest. Two games: a hint, not a fact |
| note | the box-score sentence. When it and the column recipe disagree on why, the note wins: it has the catches, yards and TDs |

## Write-up

One table per position: Player | Where | ppg | vs proj | ROS → adj | Why. Then **What to act on**,
five items: snipe now (FA, with the drop from my roster), snipe if a spot opens, trade targets
(benched production on other rosters), sell-high on my roster, and anyone on my roster whose
touches are falling (`tr` well negative). Write "none" for an empty item.

The *Why* is one line built only from the columns and the `note`/`ahead` text: workload (snap,
opp), injury ahead, rookie, soft or tough opponents, TD-driven (note, or ypt low with beat high).
Do not name a cause the table does not show. Do not name a player who is not on the output
(see memory: verify before naming). One-game windows go in a sentence under the table, not in it.

Rules that come up every week: in a 2-QB league a bench QB is normal, so a benched QB is not
cheap; a second TE is a flex only if his adjusted ROS beats my WR3's on the roster block;
STRATEGY.md's QB4 and TE3 blacklists apply to every snipe.

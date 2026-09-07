# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is
Tooling and season record for a private ESPN fantasy football league (2026, **2-QB starting lineup**). Two jobs:
pre-draft board/strategy and a live draft assistant; then in-season tracking of every decision and result.

## Commands (always via `uv run`)
```
uv run python -m ffl.settings          # league settings -> data/settings.json + settings.md (run first)
uv run python -m ffl.players           # ESPN player pool w/ league-scored projections -> data/espn_players.csv
uv run python -m ffl.sleeper           # Sleeper projections rescored to league -> data/sleeper_players.csv
uv run python -m ffl.adp               # FFC 2QB + 1QB ADP -> data/adp.csv (re-run morning of draft)
uv run python -m ffl.board             # merge + VORP + tiers -> data/board.csv, data/board.md
uv run python -m ffl.browser           # headed Chrome on the draft room (cookies injected, CDP :9222)
uv run python -m ffl.browser shot x.png  # screenshot the running Chrome window
uv run python -m ffl.draft_watch       # LIVE: picks off the draft room socket; writes journal/draft/picks.csv
uv run python -m ffl.advise            # LIVE: the candidate table, recomputed from the raw pick feed
uv run python -m ffl.avail WR 8        # LIVE: who is left at a position, by projection (the table hides replacement-level players)
uv run python -m ffl.simulate 7        # dry-run the draft from a slot (opponents pick to 2QB ADP); `all` for every slot
uv run python -m ffl.draft_watch once  # one-shot REST snapshot (pre-draft sanity check only)
uv run python -m ffl.journal sync      # in-season: transactions + weekly results + standings into journal/
```
Pipeline order: settings -> players -> sleeper -> adp -> board -> (browser + draft_watch).

## Config
`.env` (gitignored): `LEAGUE_ID`, `ESPN_S2`, `SWID`, `SEASON`, optional `MY_TEAM_ID` (auto-detected from SWID).
Mock/practice runs: `LEAGUE_ID=<mock id> FFL_DATA_DIR=data-mock uv run python -m ffl.<mod>` keeps the
real board and pick log untouched.
`data/` is regenerable and gitignored. `journal/` and `STRATEGY.md` are the durable record and are committed.

## Architecture notes
- `ffl/config.py` owns env + the `League` singleton; `raw_league_get(view, ...)` hits ESPN views directly.
- `ffl/draft_watch.py` gets live picks from the draft room's **websocket**, read passively over CDP
  from the open Chrome tab (`ffl/draft_socket.py`). ESPN's REST `mDraftDetail` view is useless during
  a live draft — every slot stays at `playerId: -1` until the draft finishes — so REST mode
  (`draft_watch once` / `rest`) is only for pre-draft checks and post-draft recaps.
- **Never open a second draft-room connection.** ESPN allows one per team and kicks the earlier
  session out: a duplicate tab, or a standalone websocket client, will boot you out of your own
  draft mid-pick. The watcher only listens to the tab you are already drafting in.
- `ffl/board.py::build()` is pure over (settings, players, adp, drafted_ids) so the watcher rebuilds it every poll;
  replacement level is computed from the still-available pool. Starter counts come from `settings["starters"]`
  with FLEX/OP slots split across positions (see `FLEX_SPLIT`).
- `ffl/draft_watch.py::recommend()` returns a **candidate table, not a verdict**: `score` is the default
  ordering; the decision is made on the columns beside it (`survive_prob`, `opp_demand`, `urgency`,
  `bye_risk`, `spread`, `injury`) against STRATEGY.md's hard rules. Do not encode new judgment as weights —
  seven practice drafts of that is how the board came to disagree with STRATEGY.md. Roster rules that are
  genuinely hard (QB4, K2, TE3) are blacklists; everything else is a rule in STRATEGY.md.
- **The draft order is drawn at draft start.** `settings.json` holds ESPN's placeholder (teams in id order)
  until then; `FFL_PICK_ORDER` overrides it and `learn_order()` rebuilds it from round 1's picks. Never
  hard-code a slot. `turn_math()` owns all pick-number maths and is shared by the watcher, `advise` and
  `simulate` so they cannot drift.
- Projections: `proj` is the mean of ESPN (`proj_espn`, league-scored by ESPN) and Sleeper (`proj_sleeper`, raw stats rescored
  in `ffl/sleeper.py::score`); K and D/ST use Sleeper's standard points. Merge is on normalized name + position (D/ST on team).
- ADP source is FantasyFootballCalculator's public API (FantasyPros gates its table behind login). The 2QB feed
  is primary; `adp_1qb` is kept for comparison.

## Rules for working here
- Every roster decision made in a session is appended to `journal/DECISIONS.md` (decision, reasoning, alternatives)
  before committing. Draft picks are logged automatically to `journal/draft/picks.csv`; add the reasoning by hand.
- Commit and push (`git push`) at the end of every session; the GitHub repo is the backup.
- Never commit `.env` or anything containing `espn_s2`/`SWID`.

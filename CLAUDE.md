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
uv run python -m ffl.draft_watch       # poll picks every 5s; recommendations; writes journal/draft/picks.csv
uv run python -m ffl.journal sync      # in-season: transactions + weekly results + standings into journal/
```
Pipeline order: settings -> players -> sleeper -> adp -> board -> (browser + draft_watch).

## Config
`.env` (gitignored): `LEAGUE_ID`, `ESPN_S2`, `SWID`, `SEASON`, optional `MY_TEAM_ID` (auto-detected from SWID).
`data/` is regenerable and gitignored. `journal/` and `STRATEGY.md` are the durable record and are committed.

## Architecture notes
- `ffl/config.py` owns env + the `League` singleton; `raw_league_get(view, ...)` hits ESPN views directly.
- `ffl/draft_watch.py` reads the raw `mDraftDetail` view, not `League.draft`, because espn-api leaves
  `League.draft` empty until the draft is *finished*.
- `ffl/board.py::build()` is pure over (settings, players, adp, drafted_ids) so the watcher rebuilds it every poll;
  replacement level is computed from the still-available pool. Starter counts come from `settings["starters"]`
  with FLEX/OP slots split across positions (see `FLEX_SPLIT`).
- Projections: `proj` is the mean of ESPN (`proj_espn`, league-scored by ESPN) and Sleeper (`proj_sleeper`, raw stats rescored
  in `ffl/sleeper.py::score`); K and D/ST use Sleeper's standard points. Merge is on normalized name + position (D/ST on team).
- ADP source is FantasyFootballCalculator's public API (FantasyPros gates its table behind login). The 2QB feed
  is primary; `adp_1qb` is kept for comparison.

## Rules for working here
- Every roster decision made in a session is appended to `journal/DECISIONS.md` (decision, reasoning, alternatives)
  before committing. Draft picks are logged automatically to `journal/draft/picks.csv`; add the reasoning by hand.
- Commit and push (`git push`) at the end of every session; the GitHub repo is the backup.
- Never commit `.env` or anything containing `espn_s2`/`SWID`.

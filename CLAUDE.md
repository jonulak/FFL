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
FFL_ROLLOUT=1 uv run python -m ffl.<mod>  # draft_watch / advise / simulate: add the Monte Carlo rollout (second opinion)
uv run python -m ffl.replay journal/draft/practice-8-slot4.csv   # a finished draft through score + rollout, from my seat
uv run python -m ffl.ffc fetch         # FantasyFootballCalculator 10-team 2QB draft logs -> data/ffc/ (replay --human)
uv run python -m ffl.practice 4        # start a League Specific Practice Draft from slot 4 (see journal/PRACTICE_DRAFT.md)
uv run python -m ffl.autodraft q.txt   # draft from a written queue (practice runs; --live to arm it for real)
uv run python -m ffl.draft_watch once  # one-shot REST snapshot (pre-draft sanity check only)
uv run python -m ffl.journal sync      # in-season: transactions + weekly results + standings into journal/
uv run python -m ffl.calibrate         # in-season: score STRATEGY.md's K (usage-adjusted ROS) against the weekly snapshots in data/calibration/
uv run python -m ffl.pace              # in-season: who is beating his projection, every roster + the wire (write-up rules: .claude/skills/pace-report)
uv run python -m ffl.props score       # props trial (weeks 4-6): ESPN+Sleeper vs +props MAE and the verdict (launchd pulls Thu/Sun)
uv run uvicorn ffl.api:app --host 127.0.0.1 --port 8300   # season API (launchd runs this; dev only)
curl -X POST 127.0.0.1:8300/outlook/refresh   # regenerate Roster-page outlooks whose facts changed (launchd does this hourly)
curl 127.0.0.1:8300/live/status        # the live-scoring feed: connected?, version, players scored (FFL_LIVE=0 keeps a dev API off it)
cd web && pnpm dev                     # season UI on :3301 against the API above
scripts/deploy.sh                      # rebuild + restart the launchd agents (post-merge hook runs it)
scripts/install.sh                     # one-time: launchd agents, hooks path, tailscale serve :3300
```
Pipeline order: settings -> players -> sleeper -> adp -> board -> (browser + draft_watch).

## Config
`.env` (gitignored): `LEAGUE_ID`, `ESPN_S2`, `SWID`, `SEASON`, optional `MY_TEAM_ID` (auto-detected from SWID).
Mock/practice runs: `LEAGUE_ID=<mock id> FFL_DATA_DIR=data-mock uv run python -m ffl.<mod>` keeps the
real board and pick log untouched.
Full practice-draft procedure (both modes, and what a practice draft cannot test): `journal/PRACTICE_DRAFT.md`.
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
- `ffl/rollout.py` is the Monte Carlo second opinion, off unless `FFL_ROLLOUT=1`. It is pure over (board,
  order, rosters, candidate table) and printed *under* the table, never merged into `score`. Opponents draft
  to ADP + spread under roster constraints; my later picks are planned by assignment of open slots to remaining
  picks (`plan_position`); candidates are scored by `season_points` (best lineup, byes out, per week).
  Judge it with `ffl.replay` on real logs, not by tuning it until a simulation looks right.
- Projections: `proj` is the mean of ESPN (`proj_espn`, league-scored by ESPN) and Sleeper (`proj_sleeper`, raw stats rescored
  in `ffl/sleeper.py::score`); K and D/ST use Sleeper's standard points. Merge is on normalized name + position (D/ST on team).
- Rest-of-season projections (the Waivers `add` column) are `ffl/espn_season.py::season_lookup(settings, week)`:
  Sleeper's weekly projections for weeks w..18 summed (`sleeper_ros`, through the per-week `_sleeper_for` cache;
  any failed week drops Sleeper's ROS for that build rather than undercount) averaged with CBS's public
  rest-of-season pages (`ffl/cbs.py`, QB/RB/WR/TE only, rescored, 30-min cache, last-good on failure). Every
  player dict carries `ros_proj`/`ros_sleeper`/`ros_cbs`/`ros_spread`; `season.ros_proj(p, week)` falls back to
  season total × weeks left when neither source has him, and only then does `add_score` haircut an OUT/IR
  player for games missed (both sources already leave those games out). `free_agent_pool` reports
  `ros_source` so the page can say when the whole pool fell back. Sleeper is Rotowire's single model.
  `w` is the **ROS week** (`espn_season.ros_week_for` → `season.ros_week`): ESPN's scoring period, or the
  next one once every kickoff in it is four hours old (off the pro-schedule document) — ESPN rolls its week
  some time Tuesday, and until then the sum would count a game already played. The week term is zero then.
- The ROS that `add` and the deltas run on is **usage-adjusted** (`season.ros_adj`, STRATEGY.md "Usage-adjusted
  ROS"): the model's per-game rate shrunk toward his window's `l3` by the window's snap-weighted game count
  (`form["weight"]`, each game = snap share ÷ that week's `season.snap_reference` median for the position's lead
  players, capped at one), the prior worth `season.K_PRIOR` (4) games. `ros_proj` stays the model mean beside
  it. K is checked, not tuned: every pool build snapshots what the blend read (`ffl/calibrate.py`,
  `data/calibration/week-<w>.json`, once per ROS week) and `ffl.calibrate` scores the Ks against what happened.
- Recent form (STRATEGY.md "Recent form"): `season.recent_form` is pure over a player's weekly game rows
  (`espn_season.game_from_stats` off espn-api's per-week `stats`; every rostered player's earlier weeks come from
  `lg.teams[..].roster` (`_roster_games`, so the replacement line runs on adjusted ROS too), free agents' from
  one `free_agents(week=w)` call per position and form week) with Sleeper's snaps (`SLEEPER_STATS`,
  `off_snp`/`tm_off_snp`, a day behind the games) joined per week. Every Roster card and Waiver row carries
  `form` (None before his first game); the columns are shown raw, and only the snap-weighted `l3` reaches `add`,
  through `ros_adj`. The same Sleeper rows give `exp` (years in the league) and the injuries feed gives `ahead`
  (hurt teammates at his position, with return week): the rookie and the fill-in are told apart by hand.
  The bye still ahead comes from the same pro-schedule document as the venue (`bye_by_team`).
- Injury return dates come from ESPN's public NFL injuries feed (`ffl/espn_season.py::fetch_injuries`, no auth,
  30-min cache, last-good on failure). `ffl.settings` writes `week1_start`, which `season.week_of` needs to turn a
  return date into a week — re-run `uv run python -m ffl.settings` on the main checkout after deploying this, or the
  feed degrades to notes only and the OUT/IR constants keep pricing.
- ADP source is FantasyFootballCalculator's public API (FantasyPros gates its table behind login). The 2QB feed
  is primary; `adp_1qb` is kept for comparison.
- **Season UI** (`web/`, Next 16 + shadcn; `ffl/api.py`, FastAPI on 127.0.0.1:8300): pages are server
  components fetching the API through a `/api/*` rewrite; `ffl/season.py` is pure (lineup-optimal,
  waiver deltas, `add` — formula in STRATEGY.md "Waivers"), `ffl/espn_season.py` is the I/O. Production
  runs from the main checkout under launchd (`launchd/`), on :3300, exposed tailnet-only via
  `tailscale serve` at https://macbook-pro.tailnet.ts.net:3300. Ports 3000/8000 are other projects'.
- A League scoreboard tile opens that game on the Matchup board at `/league/<week>/<away team id>`
  (`GET /week/{n}?team=`, `week_state(team=)`). The payload's `me`/`opp` are the board's left/right sides:
  I stand left of my own game (`mine: true`), any other game reads away then home. No play feed off my game;
  future weeks have no lineups, so their tiles are not links.
- A fantasy team's name (standings, the Matchup scorebug, Draft and Journal rows) opens its roster as a
  plain list at `/team/<id>` (`GET /team/{id}`, `espn_season.team_state`: that side of the week's box
  scores, grouped like the board, with the club and its rank; live points through `live.overlay_side`).
  Journal rows take the id from the transaction id, not the name column: teams get renamed.
- **Transactions page** (`/transactions`, `GET /transactions`): `season.group_transactions` (pure) turns
  `journal/transactions.csv` into moves. A waiver run stamps every claim with one timestamp across teams, so a
  move is (timestamp, team); a trade is every row of a timestamp holding a TRADE_* row, one side per team.
  `espn_season.transactions_state` dresses sides with the current club (teams get renamed) and players with
  pos/NFL team (`tx_player_info`: one `kona_player_info` call per 40 ids, no `limit` in the filter (ESPN 400s on
  it), D/ST derived from the id). Journal keeps only the decisions log.
  `GET /player/{id}/transactions` is one player's slice (`season.player_transactions`) plus his draft pick
  (`season.draft_entry` over `espn_season.draft_picks`: `journal/draft/picks.csv`, or ESPN's `League.draft` when
  the CSV is missing, empty or unreadable). Both player dialogs render it through `PlayerRecord`
  (`web/components/player-dialog.tsx`), the shared log + past seasons + transactions block; add a data section
  there, not to one dialog.
- Every player dict carries `home` (True hosts, False travels, None unknown): espn-api drops the venue,
  so `espn_season.home_by_team` re-reads ESPN's `proTeamSchedules_wl` view, cached per League build,
  and a failed read leaves every venue None rather than failing the page. Headshots and logos are
  ESPN's public CDN (`web/lib/teams.ts`); D/ST and any missing portrait fall back to the club mark.
- **Live scoring** (`ffl/fastcast.py`, `ffl/live.py`): during games the API holds one subscription to ESPN's
  public FastCast feed (topic `scoreboard-ffl-fantasycast`, no cookies): a CDN checkpoint of every player's raw
  stats plus JSON-Patch pushes every few seconds. `live.Runner` rescores the document with the league's stat
  table (`settings["scoring"]`, which reproduces ESPN's box score exactly) and lays the points over the cached
  Week/League/Roster payloads at serve time (`live.overlay_*`, pure). The same overlay adds `game`
  (status, quarter, clock, both teams' score, from `fastcast.game_states`, keyed by pro team) to every player
  whose game has kicked off, and `stat_line` (`live.stat_line`: the scoring stats by position, from the raw
  stat row) to every player with a stat that scores; the `Kickoff` component shows clock and score in place
  of the start time, "Final" after. A clock tick alone never bumps the live version (the 60 s poll carries
  it); a quarter or status change does. The client reports errors through `on_error` as they happen, so a
  callback that raises on every doc shows on `/live/status` instead of reading as "connected". `GET /live` is a server-sent event stream
  of a version number; `web/components/use-live.ts` refetches the page's GET on each tick, with a 60 s poll as
  the fallback. The socket is held only while a game is in progress or a kickoff is close (`live.idle_for`),
  inside `season.is_live`. The stream's `cache-control: no-transform` is load-bearing: without it Next's
  compression gzips the stream and the browser never sees a byte. Run a second dev API with `FFL_LIVE=0`.
  The play feed (`ffl/plays.py`) is derived from the same rescoring: every points change of any league
  starter (`espn_season.league_starters`) becomes a row (fantasy `team_id`, delta, total, play text, clock)
  appended to `data/live/plays-week-<n>.jsonl` and reloaded at boot. Rows carry no side: each board takes
  its two teams' rows and `plays.for_board` sides them from its left team. Every `/week` board carries its
  game's latest five; `/plays` (`?team=` for any other game) the week.
  On field: `fastcast.possession` adds `ball`/`red_zone` (lastPlay.end: next snap's team and yards to go;
  else the drive) to each game state, and `live.field` marks a player `on`/`red_zone` when his club has the
  ball, a D/ST when the other club does. The feed names no personnel, so this is the whole signal.
  The caption is the newest play *naming the player* (`plays.PlayCache`, fed from every game's lastPlay,
  drive plays and scoringPlays), never simply the game's last play: stats arrive one to three frames after
  the play text, by which time lastPlay is often the next play. `match: "last"` marks the fallback (D/ST).
- **Roster page** (`/roster`): `season.roster_cards` is the start/bench call (STRATEGY.md "Lineup"), pure;
  `ffl/outlook.py` writes labelled bullets under each card (Injury, Usage, Depth, Team, Take; each cites
  dated, linked sources; never projections) by running `claude -p` (Opus, WebSearch + WebFetch) from a
  fact sheet: the card's non-projection facts, per-game usage rows (`form["rows"]`) and `ffl/news.py`'s
  context — Rotowire blurbs (ESPN's public fantasy news feed, per player), Sleeper's depth order (one bulk
  dump), the teammates in scope by position with their injury status, and ESPN's per-player news stamps
  (one `kona_player_info` call). Four calls at a time, each card saved as it lands, cached in
  `data/outlook/week-<n>.json` under a hash of the facts, the news stamps and the day (so at least daily).
  GETs never generate. `POST /outlook/refresh` starts a locked background run; `launchd/com.jonulak.ffl-outlook`
  curls it at five past every hour. One card is rewritten on a click (the card footer's Rewrite):
  `POST /outlook/{id}` → `outlook.start_one(kind=ROSTER)`, stored with the fact hash so the hourly run skips
  him; it and the full run refuse each other (409), since the run rewrites the file it loaded. A free agent's outlook is written only on a click (the Waivers row's
  "outlook" dialog): `POST /waivers/outlook/{id}` runs `outlook.start_one` (same sheet with `free_agent=True`:
  no call, a claim-framed Take; WORKERS in flight at most) into `data/outlook/waivers-week-<n>.json`, apart
  from the Roster file; the dialog polls `GET /waivers/outlook/{id}`. The CLI must be logged in for the launchd user (`claude -p` from a plain
  terminal answers; from inside a Claude Code session it says "Not logged in").

## Rules for working here
- Every roster decision made in a session is appended to `journal/DECISIONS.md` (decision, reasoning, alternatives)
  before committing. Draft picks are logged automatically to `journal/draft/picks.csv`; add the reasoning by hand.
- Commit and push (`git push`) at the end of every session; the GitHub repo is the backup.
- Never commit `.env` or anything containing `espn_s2`/`SWID`.
- `journal/results/` is written by `journal sync` / the UI's Refresh and is committed like the rest of `journal/`.
- UI (`web/`): prebuilt, tested components over hand-rolled ones. Use a shadcn component from `web/components/ui`
  when one fits, and add a missing one with `pnpm dlx shadcn add <name>` (style base-nova, on `@base-ui/react`)
  rather than writing it. If shadcn has nothing that fits, use a well-tested library (Base UI primitives,
  TanStack Table, Recharts, date-fns, ...). Hand-roll only when neither fits, and say why in the commit.


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol

### Two memory stores, split by shape

**`bd prime` injects every bd memory's full body every session; `MEMORY.md` injects only its
one-line index, bodies loading on recall.** So bd's cost grows with every memory kept and
MEMORY.md's does not.

- **`bd remember`** — rules that must fire **unprompted**: prohibitions, and anything applying to
  every change. Recall cannot carry these — a rule about what *not* to do has no term in the prompt
  to trigger it. Keep it small; store the operative rule, not its history.
- **`MEMORY.md`** — situational knowledge a topic in the prompt will recall. Detail is nearly free.

`bd prime` prints "do NOT use MEMORY.md files"; that is its default guidance, deliberately
overridden here, and not editable without a `.beads/PRIME.md` override.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

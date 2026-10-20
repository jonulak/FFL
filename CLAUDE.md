# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is
Tooling and season record for a private ESPN fantasy football league (2026, **2-QB starting lineup**). Two jobs:
pre-draft board/strategy and a live draft assistant; then in-season tracking of every decision and result.

## Commands (always via `uv run`)
```
uv run alembic upgrade head            # create / migrate the DB schema (deploy.sh runs it)
uv run python -m ffl.db import         # one-time: old journal/ + data/ files into the DB (idempotent; done 2026-10-08, originals in data/pre-db/)
uv run python -m ffl.db backup         # checked snapshot to Google Drive (launchd nightly 03:30)
uv run python -m ffl.settings          # league settings -> data/settings.json + settings.md (run first)
uv run python -m ffl.players           # ESPN player pool w/ league-scored projections -> data/espn_players.csv
uv run python -m ffl.sleeper           # Sleeper projections rescored to league -> data/sleeper_players.csv
uv run python -m ffl.adp               # FFC 2QB + 1QB ADP -> data/adp.csv (re-run morning of draft)
uv run python -m ffl.board             # merge + VORP + tiers -> data/board.csv, data/board.md
uv run python -m ffl.browser           # headed Chrome on the draft room (cookies injected, CDP :9222)
uv run python -m ffl.browser shot x.png  # screenshot the running Chrome window
uv run python -m ffl.draft_watch       # LIVE: picks off the draft room socket; writes the DB's draft_picks
uv run python -m ffl.advise            # LIVE: the candidate table, recomputed from the raw pick feed
uv run python -m ffl.avail WR 8        # LIVE: who is left at a position, by projection (the table hides replacement-level players)
uv run python -m ffl.simulate 7        # dry-run the draft from a slot (opponents pick to 2QB ADP); `all` for every slot
FFL_ROLLOUT=1 uv run python -m ffl.<mod>  # draft_watch / advise / simulate: add the Monte Carlo rollout (second opinion)
uv run python -m ffl.replay journal/draft/practice-8-slot4.csv   # a finished draft through score + rollout, from my seat
uv run python -m ffl.ffc fetch         # FantasyFootballCalculator 10-team 2QB draft logs -> data/ffc/ (replay --human)
uv run python -m ffl.practice 4        # start a League Specific Practice Draft from slot 4 (see journal/PRACTICE_DRAFT.md)
uv run python -m ffl.autodraft q.txt   # draft from a written queue (practice runs; --live to arm it for real)
uv run python -m ffl.draft_watch once  # one-shot REST snapshot (pre-draft sanity check only)
uv run python -m ffl.journal sync      # in-season: transactions + weekly results into the DB
uv run python -m ffl.newsdesk run      # one news-desk run (collect, tag, write, queue pushes); seed / status (launchd POSTs /news/run every 10 min)
uv run python -m ffl.newsdesk bench    # replay frozen news (`bench freeze` first) through tagging with saved Jev answers, against ffl/newsdesk/bench_cases.py; --player grupe, --write (Haiku too), --refresh
uv run python -m ffl.decisions list --open   # decisions still needing a rationale or outcome; add / outcome / rationale / show / export
uv run python -m ffl.calibrate         # in-season: score STRATEGY.md's K (usage-adjusted ROS) against the weekly snapshots in the DB (calibration_*)
uv run python -m ffl.calibrate winprob # in-season: fit the win meter's σ per position (sigma_fits) + Brier; launchd runs `calibrate wednesday` (winprob, then sources) Wed 07:00
uv run python -m ffl.pace              # in-season: who is beating his projection, every roster + the wire (write-up rules: .claude/skills/pace-report)
uv run python -m ffl.sources compare   # old (ESPN+Sleeper) vs consensus projection for every rostered player; status: source health
uv run python -m ffl.calibrate sources   # fit per-source weights (stored inactive); --activate <fitted_at> applies them
uv run python -m ffl.props score       # props trial (weeks 4-6): ESPN+Sleeper vs +props MAE and the verdict (launchd pulls Thu/Sun)
uv run uvicorn ffl.api:app --host 127.0.0.1 --port 8300   # season API (launchd runs this; dev only)
curl -X POST 127.0.0.1:8300/outlook/refresh   # regenerate Roster-page outlooks whose facts changed (launchd does this hourly)
curl -X POST 127.0.0.1:8300/notify/tick   # sync + injury/waiver checks + push flush + pre-game pull (launchd every 15 min)
curl -X POST 127.0.0.1:8300/refresh       # force every page, projection and injury cache to refetch now, and wait (no UI button)
uv run python -m ffl.notify keys       # one-time: VAPID keys for .env
uv run python scripts/pageload.py       # load time of every season page on a throttled phone (default: prod :3300)
curl 127.0.0.1:8300/live/status        # the live-scoring feed: connected?, version, players scored (FFL_LIVE=0 keeps a dev API off it)
cd web && pnpm dev                     # season UI on :3301 against the API above
scripts/deploy.sh                      # rebuild + restart the launchd agents (post-merge hook runs it)
scripts/install.sh                     # one-time: launchd agents, hooks path, tailscale serve :3300
```
Pipeline order: settings -> players -> sleeper -> adp -> board -> (browser + draft_watch).

## Config
`.env` (gitignored): `LEAGUE_ID`, `ESPN_S2`, `SWID`, `SEASON`, optional `MY_TEAM_ID` (auto-detected from SWID).
Push: `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` in `.env`; `FFL_NOTIFY=0` keeps a dev API from pushing.
News: `TYPESAFE_API_KEY` in `.env` is Jev's (TypeSafe AI), which tags and groups the newsdesk's items (`ffl/newsdesk/jev.py`); without it the tag stage stops and nothing new reaches the feed.
`FFL_RECORD_FAKE=1` (worktree test server only): fake moves (FFL_WRITES=0) record their decisions anyway, titled `[fake] …` with `facts.fake`.
Mock/practice runs: `LEAGUE_ID=<mock id> FFL_DATA_DIR=data-mock uv run python -m ffl.<mod>` keeps the
real board and pick log untouched.
Full practice-draft procedure (both modes, and what a practice draft cannot test): `journal/PRACTICE_DRAFT.md`.
`data/` is gitignored, and everything in it is regenerable except `data/ffl.db` (the season's data; see Persistence), which is backed up nightly to Drive, not by git.
`journal/` and `STRATEGY.md` are the durable prose and are committed.

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
- Projections: `proj` is the weekly **consensus** (`ffl/consensus.py`, Hodges-Lehmann; `spread` = trimmed range) of ESPN
  (`proj_espn`, league-scored by ESPN; an input only, 0 is a placeholder), Sleeper (raw stats rescored in `ffl/sleeper.py::score`),
  CBS, FantasySharks and FleaFlicker. `ffl/sources/` holds cbs, sharks, fleaflicker (`Cached`: TTL + last good + thin guard; every good fetch written
  through to the `source_cache` table and restored on a source's first read after a restart, inside its TTL as fresh, past it as last good;
  without it a restart's cold Matchup build took ~27 s, ~3 s with it); health is
  `STATUS` / the `source_status` state, `GET /sources/status`, `ffl.sources status`, and a notice after 24 h failing. K and D/ST use
  Sleeper's standard points. Merge is on normalized name + position (D/ST on team): `espn_season.weekly_sources(week, settings)` →
  `merge_weekly(players, sources)`. A finished week's board (`week_state`, week < ESPN's) prices from `projection_rows` (kind `week`, the kickoff record; else kind `week_late`, what Sleeper and FantasySharks answered the first time the week was opened, saved so it is fetched once; CBS's week page serves the current week whatever week is asked, FleaFlicker has no past weeks) and takes today's ROS lookup. The payload carries only `proj`/`spread`/`n_src` and `ros_proj`/`ros_spread`/`ros_n`. Each source's
  number is written to `projection_rows` by the notify tick (`record_projections`, frozen at kickoff); `source_weights` are fitted
  Wednesdays by `ffl.calibrate wednesday` and apply only after `ffl.calibrate sources --activate`. Props keep their own ESPN+Sleeper
  `baseline`. `uv run python -m ffl.sources compare` is the before/after check.
- Rest-of-season projections (the Players page's `add` column) are `ffl/espn_season.py::season_lookup(settings, week)`:
  the consensus of Sleeper's weekly projections for weeks w..18 summed (`sleeper_ros`, through the per-week `_sleeper_for` cache;
  any failed week drops Sleeper's ROS for that build rather than undercount), CBS's public rest-of-season pages (`ffl/sources/cbs.py`,
  QB/RB/WR/TE only, rescored) and FantasySharks' weekly projections summed over w..18 like Sleeper's (`ffl/sources/sharks.py`; its Rest of Year page covers only ~10 games), each value in `ros_src`. Every
  player dict carries `ros_proj`/`ros_spread`/`ros_n`; `season.ros_proj(p, week)` falls back to
  season total × weeks left when no source has him, and only then counts an OUT/IR player's games from his
  return week (all sources already leave those games out); the haircut is on ROS, never on a delta, so
  `add_score` only applies the week factor. `free_agent_pool` reports
  `ros_source` so the page can say when the whole pool fell back. Sleeper is Rotowire's single model.
  `w` is the **ROS week** (`espn_season.ros_week_for` → `season.ros_week`): ESPN's scoring period, or the
  next one once every kickoff in it is four hours old (off the pro-schedule document) — ESPN rolls its week
  some time Tuesday, and until then the sum would count a game already played. The week term is zero then.
- The ROS that `add` and the deltas run on is **usage-adjusted** (`season.ros_adj`, STRATEGY.md "Usage-adjusted
  ROS"): the model's per-game rate shrunk toward his window's `l3` by the window's snap-weighted game count
  (`form["weight"]`, each game = snap share ÷ that week's `season.snap_reference` median for the position's lead
  players, capped at one), the prior worth `season.K_PRIOR` (4) games. `ros_proj` stays the sources' consensus beside
  it. K is checked, not tuned: every pool build snapshots what the blend read (`ffl/calibrate.py`,
  the `calibration_snapshots`/`calibration_rows` tables, once per ROS week: the first build stands) and `ffl.calibrate` scores the Ks against what happened.
- Recent form (STRATEGY.md "Recent form"): `season.recent_form` is pure over a player's weekly game rows
  (`espn_season.game_from_stats` off espn-api's per-week `stats`; every rostered player's earlier weeks come from
  each earlier form week's box scores (`_roster_games`, so the replacement line runs on adjusted ROS too; a week he was on no
  fantasy roster, a pickup's free-agent weeks, from one `kona_player_info` call per such week by ids, `_gap_games`, kept in memory and the `gap_games` table, re-read by the
  correction pass while their week is unsettled: `free_agents` filters by today's status, so it never has him), free agents' from
  one `free_agents(week=w)` call per position and form week) with Sleeper's snaps (`SLEEPER_STATS`,
  `off_snp`/`tm_off_snp`, a day behind the games) joined per week, and off the same rows completed air yards and red-zone looks (`parse_sleeper_usage`: Sleeper has no intended air yards, so `air_share`/`air_catch` say where he was caught, not thrown to). Every Roster card and Waiver row carries
  `form` (None before his first game); the columns are shown raw, and only the snap-weighted `l3` reaches `add`,
  through `ros_adj`. The same Sleeper rows give `exp` (years in the league) and the injuries feed gives `ahead`
  (hurt teammates at his position, with return week): the rookie and the fill-in are told apart by hand.
  The bye still ahead comes from the same pro-schedule document as the venue (`bye_by_team`).
- Injury return dates come from ESPN's public NFL injuries feed (`ffl/espn_season.py::fetch_injuries`, no auth,
  30-min cache, last-good on failure). `ffl.settings` writes `week1_start`, which `season.week_of` needs to turn a
  return date into a week — re-run `uv run python -m ffl.settings` on the main checkout after deploying this, or the
  feed degrades to notes only and the OUT/IR constants keep pricing.
- **Position caps**: `settings["position_limits"]` (ESPN's `rosterSettings.positionLimits`, keyed by *position* id, not slot
  id: QB 4, RB 8, WR 8, TE 3, K 3, D/ST 3; IR counts). `claims.validate` errors on a target at a full position whose group
  has no cut of his; `claims.cap_warnings` (non-blocking, `warnings` in `/claims`) on a group whose wins could pass a cap.
  Both pages show `position_caps` (`season.position_caps`). A settings.json without the key shows and checks nothing:
  re-run `uv run python -m ffl.settings` on the main checkout after deploying it.
- ADP source is FantasyFootballCalculator's public API (FantasyPros gates its table behind login). The 2QB feed
  is primary; `adp_1qb` is kept for comparison.
- **Persistence** (`ffl/db.py`, SQLAlchemy Core + Alembic, no ORM; the only SQLAlchemy importer): SQLite in WAL mode at
  `<DATA>/ffl.db` (or `FFL_DB_URL`). Tables, with the files each replaced: `transactions` ← `journal/transactions.csv`;
  `draft_picks` ← `journal/draft/picks.csv` (real draft only: `FFL_DATA_DIR` practice runs still write `data-mock/picks.csv`
  for `ffl.replay`); `results` ← `journal/results/weekly.csv`; `plays` ← `data/live/plays-week-<n>.jsonl`; `outlooks` (kind
  `week`/`waivers-week`) ← `data/outlook/*.json`; `calibration_snapshots` + `calibration_rows` ← `data/calibration/week-<w>.json`;
  `sigma_fits` ← `sigma.json` (latest wins, history kept; `calibrate.load_sigma` never fails); `claim_plans` ←
  `journal/waivers/week-<n>.json`; `sits` (players sat by hand for a week, no file before it); `box_scores` (ESPN's raw matchup read per week; `fetched_at` moves on every read, `settled` after the Thursday following the week, `final` when read after the week finished); `gap_games` ← `_GAP_GAMES`; `box_score_corrections` (old/new points per player); `source_cache` (`sources.Cached`'s scrapes); `page_cache` (`api.Cache(persist=True)`:
every installed page payload in `api.PERSIST` that survives a JSON round-trip, written through under `_db_lock`; `load()` at boot takes them
back expired with no builder, so the first open after a restart is served at once and rebuilt behind; `drop`/`drop_prefixes`/`clear`
delete the rows; player cards older than 7 days pruned at load; a key's inline first build is single-flight (`ffl/flight.py`): callers arriving
while it runs share its payload or its 502, as do concurrent cold reads of `sources.Cached`, `_sleeper_for` and `box_scores`); `decisions` ← `journal/DECISIONS.md` (frozen; `ffl.decisions` parses it on import; app rows carry `facts` and a rationale of preset keys, NULL = open). `season` leads every natural key (`plays` is keyed by an autoincrement `id`, unique on `(season, week, player_id, at, total)`). A write is one short `BEGIN IMMEDIATE` transaction, never held
  across an ESPN call; the connect hook sets `busy_timeout` before `journal_mode=WAL`. `db.engine()` never creates tables: a
  missing or stale schema raises `SchemaError` (run `uv run alembic upgrade head`); a DB error on a page read 500s the page, only a missing
  row degrades to empty. Callers outside `db.py` catch `db.DBError`. Schema changes are new Alembic revisions
  (`uv run alembic revision --autogenerate -m ...`, batch mode, under `migrations/`). `uv run python -m ffl.db import`
  (`ffl/db_import.py`) is insert-if-absent from the old files, with per-table counts and dupes; it exits 1 when short or when a
  non-plays table has shared keys (the cutover ran it 2026-10-08; the originals are in `data/pre-db/`). `scripts/deploy.sh`
  runs `alembic upgrade head` after tests and build, before the restart. A snapshot in Drive opens only after copying it local (WAL). `uv run python -m ffl.db backup` (`ffl/db_backup.py`) takes a read-only online backup, checks it, and
  renames it into `~/Library/CloudStorage/GoogleDrive-admin@example.com/My Drive/FFL/backup/ffl-YYYY-MM-DD.db`
  (`FFL_BACKUP_DIR` overrides), keeping 14; `launchd/com.jonulak.ffl-backup` runs it nightly at 03:30. Tests run on a per-test
  copy of a session template DB built by `alembic upgrade head` (autouse fixture sets `FFL_DB_URL`), never `data/ffl.db`.
  A worktree gets its own `data/ffl.db`; a dev API there needs `uv run alembic upgrade head` once.
- **Box scores** (`espn_season.box_scores`): a finished week is read from the `box_scores` table (ESPN only when it has no `final` row: one read after the week finished; the current week's last, maybe mid-game, read is not); the current
  week ESPN-first, a row under 60 s old (off `fetched_at`) counting as the hit and the row serving when ESPN fails; a league move
  (`forget_current_boxes` → `db.expire_live_boxes`, non-final rows only) expires it. The tick's `correct_boxes` (`api.correction_pass`) re-reads each unsettled finished week hourly
  (a short answer, fewer matchups or a stored player missing, keeps the stored doc unsettled), logs changed points to `box_score_corrections`, plays a starter's correction into his week's feed (match `correction`; a finished
  week's board reads its feed from the `plays` table) and expires the pages built on it. `expire_everything` and the pregame pull keep finished weeks.
- **Season UI** (`web/`, Next 16 + shadcn; `ffl/api.py`, FastAPI on 127.0.0.1:8300): pages are server
  components fetching the API through a `/api/*` rewrite; `ffl/season.py` is pure (lineup-optimal,
  waiver deltas, `add` — formula in STRATEGY.md "Waivers"), `ffl/espn_season.py` is the I/O. Production
  runs from the main checkout under launchd (`launchd/`), on :3300, exposed tailnet-only via
  `tailscale serve` at https://macbook-pro.tailnet.ts.net:3300. Ports 3000/8000 are other projects'.
- **Players page** (`/players`, was Waivers; `/waivers` redirects with its query): `GET /players?preset=&pos=&fa=&cols=&f=&sort=&q=`.
  `espn_season.free_agent_pool` returns `positions` (free agents only, `POOL_SIZE` deep per position: claims,
  the waiver outlook and the calibration snapshot read it, so it must never carry a rostered player) and
  `rostered` (every rostered player through the same `waiver_deltas`, "worth to me", tagged `owner`/`mine`;
  my own rows carry no add or deltas). `fa=1` (default) serves `positions[pos]`, `fa=0` adds `rostered[pos]`,
  and `q` searches both halves at every position whatever `fa` says; `season.rank_players` keeps one row per
  player (the rostered one wins) by add, None last. The Free agents toggle rides in the URL as `fa=1`/`fa=0` when it differs from the preset; an Availability filter naming theirs or mine reads the rostered half whatever `fa` says. A view (ffl-8iz, `ffl/player_views.py`, pure) is a preset (`player_presets` state key: `{presets, default}`, seeded "Waivers"; `GET/POST /players/presets`, `POST /players/presets/{id}`, `/{id}/delete`, `/default`) with the URL's overrides laid over; no params opens the default, an unknown preset id opens the default. Filters run server-side over the whole pool (a missing figure fails a numeric filter; a search ignores them); `pos=ALL` spans every position and `pos=FLEX` RB/WR/TE (`GROUPS`), each capped to 150 by the view's sort when unfiltered (`capped`). The payload carries `view`, `presets`, `default`; COLUMNS mirrors `web/lib/player-columns.ts`.
- A League scoreboard tile opens that game on the Matchup board at `/league/<week>/<away team id>`
  (`GET /week/{n}?team=`, `week_state(team=)`). The payload's `me`/`opp` are the board's left/right sides:
  I stand left of my own game (`mine: true`), any other game reads away then home. No play feed off my game;
  future weeks have no lineups, so their tiles are not links.
- A fantasy team's name (standings, the Matchup scorebug, Transactions sides) opens its roster as a
  plain list at `/team/<id>` (`GET /team/{id}`, `espn_season.team_state`: that side of the week's box
  scores, grouped like the board, with the club and its rank; live points through `live.overlay_side`).
- **Push notifications** (`ffl/notify.py`): detectors (new transaction rows, my starters' injuries, waiver-run results) →
  `notices` outbox (`key` = dedupe) → `/notify/tick` flush (quiet hours, moves batched, 24 h drop; one tick at a time, 409
  otherwise) → pywebpush → `web/public/sw.js`; `/settings` holds the device, switches and quiet window. The message
  is Declarative Web Push (`notify.payload_for`: `web_push` 8030, `notification.navigate` absolute off `FFL_APP_URL`):
  Safari shows and opens it without the worker, since iOS often never fires `notificationclick` for a backgrounded
  Home Screen app (WebKit bug 268797, ffl-e9x.10); the legacy fields beside it feed sw.js's push handler elsewhere.
  The tap path is traced on-device (`POST /trace` → `data/trace.jsonl`, rows from sw.js and `SwRegister`).
  After the tick, `api.pregame_pull`: once per kickoff time (`season.pregame_due`, inside `PREGAME_PULL_MIN` = 85 min,
  after the inactives are out; done ones in the `pregame_pulls` state) it expires every page and source cache and rebuilds
  on a thread. Refresh keeps a source entry fetched with a long TTL (FantasySharks' far weeks): re-scraping 14 weeks
  is what took the old nav button past 30 s.
- **News desk** (`ffl/newsdesk/`, beads ffl-0gv): `pipeline.run` is seed (`seed.SOURCES`) → index (`players.fetch_index`) →
  `collect.run_all` then `collect.recheck` (in that order: recheck's "still in the feed" is the poll just made) →
  `media.run_all` (transcripts split per player) → `tag.run` (Jev: effect ▲/▼/●, kind, story) → `write.run` (Haiku through
  `claude -p`: headline, overview, a line per player it moves ▲/▼; one it only names gets no line and no place on his news page) → notices → `health.notices`, each stage tried alone (the index's
  stages skipped without one), the stage dict in state `news_last_run`. `POST /news/run` (202; 409 while `_NEWS` is held)
  runs it on a thread and flushes after if `_NOTIFY` is free; `launchd/com.jonulak.ffl-news` POSTs it every 10 min;
  `GET /news/status` is the sources' health and the last run. A push (`notices.for_story`, kind `news`, the `news` pref)
  is a written revision's ▼ on my roster or ▲ on a free agent at `NEWS_PUSH_PROB`, with a line, unflagged, not muted
  (`news_mutes`), key `news:<id>:<rev>`; at most one per player and kind a day (`notices.recent`, off the notices table
  and the stories' rows) unless an earlier rev of the story pushed (a material update). A correction or retraction pushes
  once (`news:<id>:<status>`), only after the original did. Owners come from `pipeline.owners()` (also read at request
  time); when it fails nothing is queued and the revisions wait in state `news_to_notify` for the next run.
- **Transactions page** (`/transactions`, `GET /transactions`): `season.group_transactions` (pure) turns
  the `transactions` table's rows into moves. A waiver run stamps every claim with one timestamp across teams, so a
  move is (timestamp, team); a trade is every row of a timestamp holding a TRADE_* row, one side per team.
  The log is kept current three ways (`journal.sync_transactions`; the table's `(season, id)` key dedupes, so concurrent syncs need no lock): every `/transactions` build syncs
  first; every fresh current-week `espn_season.box_scores` fetch (every page's rosters come through it) syncs
  when ESPN's owners disagree with the draft replayed through the log (`reconcile_log`, once per distinct
  disagreement); and `launchd/com.jonulak.ffl-transactions`
  POSTs `/transactions/sync` hourly at :35.
  `espn_season.transactions_state` dresses sides with the current club (teams get renamed) and players with
  pos/NFL team (`tx_player_info`: one `kona_player_info` call per 40 ids, no `limit` in the filter (ESPN 400s on
  it), D/ST derived from the id).
  `GET /player/{id}/transactions` is one player's slice (`season.player_transactions`) plus his draft pick
  (`season.draft_entry` over `espn_season.draft_picks`: the `draft_picks` table, or ESPN's `League.draft` when
  it is empty). Both player dialogs render it through `PlayerRecord`
  (`web/components/player-dialog.tsx`), the shared log + past seasons + transactions block; add a data section
  there, not to one dialog.
- Every player dict carries `home` (True hosts, False travels, None unknown): espn-api drops the venue,
  so `espn_season.home_by_team` re-reads ESPN's `proTeamSchedules_wl` view, cached per League build,
  and a failed read leaves every venue None rather than failing the page. Headshots and logos are
  ESPN's public CDN (`web/lib/teams.ts`, sized through its resizer; `web/public/sw.js` keeps them in the `espn-img-v1` cache,
  served first, refetched behind after a week); D/ST and any missing portrait fall back to the club mark.
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
  written through to the `plays` table (`plays.Store` keeps memory, reloads at boot; a failed write shows on `/live/status` as "plays not saved"). Rows carry no side: each board takes
  its two teams' rows and `plays.for_board` sides them from its left team. Every `/week` board carries its
  game's latest five; `/plays` (`?team=` for any other game, `?week=` the board's week; each strip links its own) the week.
  On field: `fastcast.possession` adds `ball`/`red_zone` (lastPlay.end: next snap's team and yards to go;
  else the drive) to each game state, and `live.field` marks a player `on`/`red_zone` when his club has the
  ball, a D/ST when the other club does. The feed names no personnel, so this is the whole signal.
  The caption is the newest play *naming the player* (`plays.PlayCache`, fed from every game's lastPlay,
  drive plays and scoringPlays), never simply the game's last play: stats arrive one to three frames after
  the play text, by which time lastPlay is often the next play. `match: "last"` marks the fallback (D/ST).
- **NFL tab** (`/nfl`, `/nfl/<week>/<club>` with the whole play feed and its `?f=` filter; the old `/plays` address redirects there; `ffl/nfl.py`, GET `/nfl` and `/nfl/{week}/{team}`): ESPN's
  public site scoreboard and core-API drives (one call embeds every play with participant athlete ids, which are the fantasy
  player ids), no cookies, behind `nfl.TtlCache` (inline, last good, `stale_since`; drives keyed by (event, status); the scoreboard lives
  to the next kickoff (+5 s), between 20 s and 10 min, and 20 s while a game is on or a `pre` game is past its kickoff; drives 10 min pregame, 15 s while on, a final game's first fetch 10 min (ESPN's drives can lag the status flip) and a refetch of it a day, its `pre`/`in` entries dropped; not `ffl/sources`, so an
  outage never pushes a notice; `/refresh` expires it). Plays are marked `mine`/`opp` against this week's two lineups
  (`api._nfl_sides`, the Matchup board, which also gives the In this game table its two team names and owners, `api._nfl_teams`; D/ST by club on defence for a score, turnover, sack or safety; on HTTPException or
  SystemExit it degrades to no marks rather than failing the page, and the game payload's `marked` is false so My players says so); FastCast's clock and score lie over the scoreboard
  (`nfl.overlay`). The game page polls every 15 s (`useLive` `pollMs`) since `/live` ticks only on fantasy points, and the feed
  keeps one arrival ledger over the whole game (not per drive or per filter) so only plays that land after the page opened
  slide in, each only the first time it is on screen. The payloads' `live` is true from 15 minutes before kickoff (`nfl.is_live`) so a
  pregame page starts polling; the week picker offers `api.NFL_WEEKS` (18), not the fantasy 14. A junk club 404s before any fetch. A game line (`GameLink`, `gameLinkFor`; 40px tap target) on Matchup, League boards, Team and Roster opens the game.
- **Win probability** (`season.win_prob`, pure; STRATEGY.md "Lineup": context, never an input to the call):
  each side's starters as posted points plus projection × the game left (`season.game_left`: the feed's quarter
  and clock, else ESPN's game_played; a starter with points and no clock carries the rest of his projection),
  variance σ_pos² × left, P = Φ(margin/σ). σ per position from the latest `sigma_fits` row
  (`uv run python -m ffl.calibrate winprob`, which prints the pregame Brier too; `calibrate.load_sigma`), else
  `season.SIGMA_FALLBACK`. The Week payload's `win_prob` and each League row's (over the side's `starters`, the
  board's merged players, whose sum is now the tile's `proj`) are re-priced by `live.overlay_week`/`overlay_league`
  on every push. UI: `web/components/win-meter.tsx` on Base UI's Meter.
- **Roster page** (`/roster`): `season.roster_cards` is the start/bench call (STRATEGY.md "Lineup"), pure; a START
  carries `call_slot`, the slot `optimal_lineup` places him in (`_place_by_kickoff`: same starters, latest kickoffs in
  flex, a started game kept in place), and the chip names it;
  `ffl/outlook.py` writes labelled bullets under each card (Injury, Usage, Depth, Team, Take; each cites
  dated, linked sources; never projections) by running `claude -p` (Opus, WebSearch + WebFetch) from a
  fact sheet: the card's non-projection facts, per-game usage rows (`form["rows"]`) and `ffl/news.py`'s
  context — Rotowire blurbs (ESPN's public fantasy news feed, per player), Sleeper's depth order (one bulk
  dump), the teammates in scope by position with their injury status, plus every hurt teammate at his own position
  wherever the chart puts him (ESPN's injury feed drops IR players) with that teammate's Rotowire items, where return dates are written, and ESPN's per-player news stamps
  (one `kona_player_info` call). Four calls at a time, each card saved as it lands, cached in
  the `outlooks` table (kind `week`) under a hash of the facts, the news stamps and the day (so at least daily).
  GETs never generate. `POST /outlook/refresh` starts a locked background run; `launchd/com.jonulak.ffl-outlook`
  curls it at five past every hour. One card is rewritten on a click (the card footer's Rewrite):
  `POST /outlook/{id}` → `outlook.start_one(kind=ROSTER)`, stored with the fact hash so the hourly run skips
  him; it and the full run refuse each other (409), since a full run started before the click would write its own older card over the newer single one. A free agent's outlook is written only on a click (the Players row's
  "outlook" dialog): `POST /waivers/outlook/{id}` runs `outlook.start_one` (same sheet with `free_agent=True`:
  no call, a claim-framed Take; WORKERS in flight at most) into `outlooks` with kind `waivers-week`, apart
  from the Roster's; the dialog polls `GET /waivers/outlook/{id}`. The CLI must be logged in for the launchd user (`claude -p` from a plain
  terminal answers; from inside a Claude Code session it says "Not logged in").
  The page groups rows by ESPN's slot (lineup order, then bench, IR) with the call as chips beside it (a ring when
  ESPN's lineup and the call disagree); rows are compact, detail expands, and the name opens the shared player
  dialog (Form on `ros_adj`). `espn_season.with_espn_slots` takes each card's slot and lock from a fresh mRoster
  read (`roster_flags`), never the cached box score; a failed read keeps the box slot, unlocked.
  **`POST /lineup`** is the one-tap swap (`ffl/espn_write.py` is the only code that writes to ESPN): it re-reads
  mRoster for slots and locks, refuses when ESPN's week (`pending_state`) differs from the page's (and drops the
  cached League and page so Reload sees the new week), takes the non-blocking `_LINEUP` lock (409 when held),
  validates with `season.lineup_moves`, sends ESPN's ROSTER/LINEUP transaction (`espn_write.move_lineup`), records
  `season.lineup_entry` in the `decisions` table as soon as ESPN may have taken it (a timeout records it as
  uncertain and answers 502 `moved`; the answer carries `decision: {id, needs_rationale}`, and a move that follows the call is recorded with rationale `followed_call`, otherwise open), then reads the page back (`roster_state`) into the cache with `Cache.put`
  (its generation guard stops an older in-flight rebuild landing over it; a failed read-back drops the key and
  answers 502 `moved`). Under `FFL_WRITES=0` nothing reaches ESPN: `espn_write.fake_slots()` holds the moves
  and wins over ESPN's slots in the page and the validator. `web/components/use-live.ts` ignores a refetch that
  started before a move landed (generation guard).
  A slot's starters stand in ESPN's entry order, which is roster-acquisition order (draft, then adds) and never
  changes on a move, so a swap would jump rows: `season.reseat` gives the moved-in player the seat of the one who
  left (the `seats` table: player → whose place in that order he stands in), `with_espn_slots` reads it into
  `espn_order`, and `lineup.applyMoves` mirrors it so the optimistic rows match the read-back.
  **Sit for the week** (the switch at the top of a player's swap sheet; `POST /roster/sit/{id}` `{sat}`): a row in
  the `sits` table for ESPN's current week, so it clears at rollover. `roster_state` and my side of `week_state` mark
  him `sat`; `optimal_lineup` leaves him out like IR, so the Roster call (chip SIT, `play_proj` 0 so the sheet never
  offers him), the other cards' gaps and the Matchup board's by-projection swaps are all made without him.
  `playable_proj` is untouched (the win meter reads ESPN's real lineup) and the waiver pool ignores sits (calibration
  reads it). `model_call` is the call he would have had: the outlook hashes it, so a sit never rewrites his outlook.
  The endpoint re-calls the cached cards (no ESPN read) under `_LINEUP`, and records only when writes are real or `FFL_RECORD_FAKE=1` on a dev API (its answer carries `decision: {id, needs_rationale}`). `POST /claims/submit` answers `decisions: [...]` (the claims row and each free add). `POST /roster/sit/{id}` is the same `decision` answer as `/lineup`.

## Rules for working here
- Every roster decision made in a session is recorded with `uv run python -m ffl.decisions add` (kind, title, decision, rationale preset or Other + note, alternatives, exit conditions) before committing; outcomes later with `ffl.decisions outcome <id>`. The app records its own moves (lineup, sit, claims, free adds) and the Decisions page asks for the reason; Settings can turn the recording (and its "Why?" prompts) off, and the page off, which holds the recording off too (`decision_prefs` state, `GET/POST /decisions/prefs`, read by the layout). journal/DECISIONS.md is frozen history (to 2026-10-10), imported into the `decisions` table.
- Commit and push (`git push`) at the end of every session; the GitHub repo is the backup.
- **Landing a worktree branch on main — only after Jonathan explicitly says to land that branch** (asking whether
  to land is fine; a finished task is not permission). A worktree session is refused git against the main checkout,
  so it runs `scripts/land.sh` (ff-only merge + push; the post-merge hook deploys) in the FFL workspace's Herdr tab
  labelled `Terminal`, a plain shell in `~/Developer/FFL`. Pane IDs change across Herdr restarts: find it with
  `herdr workspace list` / `herdr tab list --workspace <id>` / `herdr pane list --workspace <id>`, one plain
  command each with literal IDs. Then: `git rebase main` in the worktree; check the tab is at its prompt
  (`herdr pane read <pane> --lines 3`); `herdr pane run <pane> "scripts/land.sh <branch>"`;
  `herdr pane wait-output <pane> --regex "land: done|error|fatal|rejected|Not possible" --timeout 600000`; read
  the pane (the deploy prints `deployed <sha>`, then `land: stopping pid …` for each of the branch's test servers: every
  listener whose cwd is inside its worktree, with its `tailscale serve` entry). On any failure stop and report; never fix it in his tab.
  Without his go-ahead, or outside Herdr, hand him `cd ~/Developer/FFL && scripts/land.sh <branch>`.
- Never commit `.env` or anything containing `espn_s2`/`SWID`.
- Weekly results go to the `results` table (`journal sync` / the hourly `/transactions/sync`); `journal/results/` is no longer written. Standings are read live from ESPN.
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

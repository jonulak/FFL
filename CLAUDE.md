# CLAUDE.md

Guidance for Claude Code (and any contributor) working in this repository.

## What this is
FFL is a self-hosted companion for ESPN fantasy football leagues. It has two halves:
- **Draft tools** (CLI): a pre-draft board (VORP, tiers, ADP), a live draft assistant that reads the draft room, a
  simulator and a replayer.
- **Season app** (FastAPI + Next.js): start/sit calls, a waiver ranking, live scoring, a play feed, news, outlooks
  written by `claude -p`, push notifications, and a record of every roster decision. Several users and several
  leagues share one install; every league is scored with its own settings (a 2-QB or OP lineup is a setting).

## Commands (always `uv run`, never bare `python`/`pip`/`pytest`)
```
uv run pytest -q                       # tests (need a local Postgres: scripts/pg-test-setup.sh)
uv run alembic upgrade head            # create / migrate the schema (the API image's entrypoint runs it on start)
scripts/devdb.sh [name] [--from-dump F | --from DB_URL | --from-sqlite F] [--replace] [--no-env]   # a local database ffl_<name>; writes FFL_DB_URL into .env
uv run python -m ffl.db backup [dir]   # checked pg_dump -Fc into dir (else FFL_BACKUP_DEST), keeps 14
uv run python -m ffl.db restore <src> --url URL --yes   # newest dump in a dir, or a .dump file; without --yes it prints the target and stops
uv run python -m ffl.db counts URL_A URL_B   # rows per table side by side (`env` = FFL_DB_URL); exit 1 on a difference
uv run python -m ffl.db import         # one-time importer for the pre-database files (idempotent)
uv run python -m ffl.db pgcopy data/ffl.db   # the old SQLite file into FFL_DB_URL
uv run python -m ffl.settings          # league settings from ESPN into the DB (run first for a league)
uv run python -m ffl.leagues list      # registered leagues; `add espn <id> [--season N] [--default]`, `user add <email>`, `join <key> --user <email> --team <id> [--default]`
uv run python -m ffl.leagues caps <key> QB=3   # my roster caps in a league (never above the league's; no args: the table)
uv run python -m ffl.players           # ESPN pool with league-scored projections (--refresh)
uv run python -m ffl.sleeper           # Sleeper's season projections (--refresh)
uv run python -m ffl.adp               # FFC 2QB + 1QB ADP (re-run the morning of the draft)
uv run python -m ffl.board             # the board as markdown; writes nothing
uv run python -m ffl.browser           # headed Chrome on the draft room (cookies injected, CDP :9222); `shot x.png`
uv run python -m ffl.draft_watch       # LIVE: picks off the draft room's socket; `once` (REST snapshot), `store` (a finished draft)
uv run python -m ffl.advise            # LIVE: the candidate table
uv run python -m ffl.avail WR 8        # LIVE: who is left at a position
uv run python -m ffl.simulate 7        # dry-run the draft from a slot; `all` for every slot
FFL_ROLLOUT=1 uv run python -m ffl.advise   # add the Monte Carlo rollout (also draft_watch, simulate)
uv run python -m ffl.strategy show     # the league's strategy text (--league KEY; `list`)
uv run python -m ffl.replay practice:<name>   # a stored draft log through score + rollout (--league, --team, --human, --keep NAME --from DB_URL)
uv run python -m ffl.ffc fetch         # FantasyFootballCalculator draft logs into draft_logs (`list`)
uv run python -m ffl.practice 4        # start an ESPN practice draft from slot 4 (--check)
uv run python -m ffl.autodraft q.txt   # draft from a written queue (mock DBs only unless --live)
uv run python -m ffl.journal sync      # transactions + weekly results into the DB
uv run python -m ffl.playbook backfill 1-6   # finished weeks' NFL plays from ESPN's drives
uv run python -m ffl.newsdesk run      # one news-desk run; `seed`, `status`, `bench [freeze] [--player X] [--write] [--refresh]`
uv run python -m ffl.decisions list --open   # decisions missing a rationale or outcome; add / outcome / rationale / show / export
uv run python -m ffl.calibrate         # score the usage-adjusted ROS blend's K; `winprob`, `sources [--activate <fitted_at>]`, `wednesday`
uv run python -m ffl.pace              # who is beating his projection, every roster + the wire
uv run python -m ffl.sources compare   # projection sources compared per rostered player; `status`
uv run python -m ffl.props score       # the props experiment's scoring
uv run python -m ffl.notify ensure-keys   # push keys into the DB when none open (the API image's entrypoint runs it)
uv run python -m ffl.creds set --user <name>   # a user's ESPN cookies (stdin: ESPN_S2=/SWID= lines); status / clear
uv run python -m ffl.vault keygen      # a new FFL_SECRET_KEYS entry; `rotate` re-seals every row with the first key
uv run uvicorn ffl.api:app --host 127.0.0.1 --port 8300   # dev API
cd web && pnpm dev                     # dev UI on :3301 against the API above; pnpm typecheck / test / lint
cd extension/ffl && pnpm build         # the browser extension -> dist/chrome, dist/safari; `pnpm test`, `pnpm package`
# Safari: extension/spike-safari (Xcode). Xcode Cloud archives both shared schemes on a push to main touching extension/
#   and hands them to TestFlight; ci_scripts/ci_post_clone.sh installs Node and builds dist/safari
uv run python scripts/pageload.py [base]   # load time of every page on a throttled phone (default :3300)
curl -X POST -H "x-ffl-job: $FFL_JOB_TOKEN" 127.0.0.1:8300/notify/tick   # also /outlook/refresh, /news/run, /transactions/sync, /refresh
curl -fsSL https://raw.githubusercontent.com/jonulak/FFL/main/install.sh | sh   # install, and also the upgrade (keeps .env, re-fetches docker-compose.yml); never a bare `compose pull`
curl -fsSL https://raw.githubusercontent.com/jonulak/FFL/main/install.sh | sh -s -- --auto-on   # opt-in automatic upgrades: launchd/cron every 5 min running --auto (one check), held during live games; --auto-off removes it
```
Draft pipeline: settings -> players -> adp -> browser + draft_watch. Every draft tool builds the board from the DB
(`board.load_inputs(ref)` + `build`); the ADP feed is 2QB when the lineup starts two QBs or an OP (`FFL_ADP_MODE` overrides).

## Config
**Stack.** Production is the Docker Compose stack (`docker-compose.yml`, installed by `install.sh` into `~/ffl`):
`db` (`pgvector/pgvector:pg16`: postgres:16 with pgvector for the news search; Mac: `brew install pgvector`, `scripts/pg-test-setup.sh`; not published on the host; a `docker-compose.override.yml` opens it for the draft tools), `api` (`ghcr.io/jonulak/ffl-api`, the root `Dockerfile`; `deploy/entrypoint.sh`
migrates then starts uvicorn on :8300; no Node, no agent CLI), `runner` (`ghcr.io/jonulak/ffl-runner`, `Dockerfile.runner`:
the standalone `claude` binary behind `ffl/harness.py`, stdlib only, :8310 on the internal network, no published port,
`x-ffl-job` required), `stt` (`ghcr.io/jonulak/ffl-stt`, `Dockerfile.stt`: Parakeet speech to text behind `ffl/stt.py`,
:8320, the model loaded on the first request and dropped when idle; used only while Settings › App › Podcast
transcription is on (`appconf.TRANSCRIBE`): the news desk then polls the no-transcript shows (`seed.STT_ONLY`), queues
their episodes and sends one a run to `FFL_STT_URL`, leaving them queued while it is unreachable), `web` (`ghcr.io/jonulak/ffl-web`, `web/Dockerfile`, host port `FFL_PORT`, 3300)
and `jobs` (the api image under busybox crond, `deploy/crontab`: the tick every 15 min, news every 10, outlooks at :05,
transactions at :35, calibration Wednesdays, a nightly backup). HTTPS is a reverse proxy in front of the web port, with
the app's address (Settings › App) set to the https URL (push and the Secure cookie need it). Images are built by `.github/workflows/images.yml`
on a push to `main` and on `v*` tags; `FFL_VERSION` pins one. A push to `main` is a deploy: once all four images are out, the release job moves `latest` and
then the `release` git tag, where `install.sh` gets its compose file and where installs with automatic upgrades (`install.sh --auto`) notice a new release.

**`.env`.** One file beside the compose file holds only what the stack needs before it reaches the database: the key
ring, job token, Postgres password (all generated by `install.sh`, which keeps an existing `.env`), `FFL_PORT`, `TZ`,
`FFL_VERSION` (`deploy/env.example`). Every other install setting is an **app setting** (`ffl/appconf.py`: sealed
rows in `app_secrets`, read through a 60 s cache): the app's address (stored from the first `/setup`'s Origin), the
push contact, the Claude token, the YouTube key, the push key pair (`notify.ensure_keys`, run by
`deploy/entrypoint.sh`, so a dev API never pushes). An admin sets them in the setup wizard (skippable steps) and
Settings › App (`/app/settings`); never add a user-facing setting to `.env`. No code reads them from the environment.
Every credential a user must bring (a key, a token, a cookie) gets a skippable wizard step, a card in Settings, and a
"How to get…" button (`web/components/credential-steps.tsx`) whose dialog walks through getting it, with links.
In development `FFL_DB_URL` (`postgresql+psycopg://...`) names the one database; Postgres is the only backend, and
an unset URL or another scheme is a `SchemaError`. An environment variable beats `.env`. Never commit `.env`.

**Key ring.** `FFL_SECRET_KEYS` (`<id>:<base64 32 bytes>[,...]`): the first key seals, every key opens
(`ffl/vault.py`, AES-GCM with the row's owner as associated data). ESPN cookies and the app settings are sealed with it. The
API will not start without it. Rotate by putting a new key first, running `ffl.vault rotate`, then dropping the old.
A new ring opens no old row: keep a copy.

**Sign-in.** Every API route needs a session or the job token. A session is username + password (`ffl/auth.py`,
scrypt in `ffl/passwords.py`; sessions slide over 90 days). On first run `/setup` claims the first admin; admins invite
users and reset passwords under `/admin/users` (single-use set-password links). The job token is the `x-ffl-job`
header = `FFL_JOB_TOKEN`, sent by the jobs container. Two test servers on one host set distinct
`FFL_SESSION_COOKIE=ffl_session_<web port>` on their API and web.

**Claude token.** `claude -p` (outlooks, the news writer) runs on the token stored in Settings › App › Claude
(`ffl/claude_cli.py`; never the environment's). The API resolves the
token and sends the call (prompt, schema, model, credential) to the runner at `FFL_RUNNER_URL`; with it unset (dev on
the Mac, tests) `claude_cli` calls `harness.call` in process, so a local `claude` on PATH is all dev needs. The
contract (`ffl/harness.py`) is harness-neutral; a second harness is one entry in `HARNESSES`. Errors are `LlmError`
(`.account` for a login/plan/runner failure) or `subprocess.TimeoutExpired`. It runs from a neutral
temp directory so no CLAUDE.md applies. Every call is a row in `llm_calls`; `GET /llm/usage` is admins'.

**ESPN connection** (`ffl/espn_auth.py`, `ffl/espn_connect.py`). Each user's ESPN cookies (espn_s2 + SWID) are their
own sealed rows in `espn_credentials`, one per ESPN account; no code reads cookies from the environment
(`tests/test_no_env_cookies.py`). Each membership names the account it reads through. Writes go out only under the
acting member's own cookies; shared league reads fall back to another member's working cookies; a 401/403 marks an
account failing. A user connects with the browser extension (`extension/ffl`) or by pasting cookies (`POST
/espn/connect`). The extension pairs from the open FFL tab (its session confirms the code) or by a code the signed-in
user types; then it sends cookies with its device token. `POST /espn/pair/start`, `/espn/pair/poll` and
`/espn/cookies` are public, each guarded by its own secret: **never flip the pairing direction**. The extension stores
a token and a hash of the last login, never a cookie value. Never log a cookie, code, token or the fan API's URL (it
carries the SWID). Disconnecting an account leaves every league it served (`db.drop_memberships`).

**Other switches.** `FFL_WRITES=0` fakes ESPN writes (`FFL_RECORD_FAKE=1` still records them as decisions);
`FFL_NOTIFY=0` keeps a dev API from pushing; `FFL_LIVE=0` keeps it off the live feed; `FFL_SOURCES_OFF` drops
projection sources on one host (some refuse cloud addresses); `FFL_OUTLOOK_WORKERS` (default 20) caps parallel
`claude -p` calls. Practice drafts use a `*mock*` database (`scripts/devdb.sh mock --no-env`); `ffl.autodraft`
refuses any other without `--live`. `data/` is gitignored caches.

## Architecture
- **Module map.** `ffl/config.py` env and the ESPN `League`; `ffl/db.py` all SQL (SQLAlchemy Core, no ORM, the only
  SQLAlchemy importer); `ffl/api.py` the FastAPI app; `ffl/season.py` pure season logic (lineup, waiver deltas, `add`,
  win probability); `ffl/espn_season.py` season I/O and page builders; `ffl/board.py`, `ffl/draft_watch.py`,
  `ffl/rollout.py` the draft; `ffl/sources/` projection scrapers; `ffl/live.py` + `ffl/fastcast.py` live scoring;
  `ffl/plays.py` + `ffl/playbook.py` the play feed; `ffl/outlook.py` outlooks; `ffl/newsdesk/` news; `ffl/notify.py`
  push; `ffl/espn_write.py` the only code that writes to ESPN; `web/` the Next 16 UI; `extension/ffl` the extension.
- **No implicit league.** Every function on league data takes the league `ref` (required, keyword-only in `db`);
  nothing falls back to a default league (`tests/test_no_implicit_league.py`). The CLI acts as `FFL_USER` on
  `FFL_LEAGUE` / `--league`, else that user's default (`leagues.acting()`). API routes take `?league=<key>`
  (`api.open_league`; none = the user's default; a non-member gets 404 `unknown_league`). Job routes with no league run
  every league. Every league behaves identically: no league-specific features.
- **Per-user data.** One user never sees another's rows (`tests/test_two_users.py`). Personal rows carry `user_id`
  (decisions, watchlist, sits, notices, push subscriptions, `user_state`); what ESPN keeps per team carries `team_id`
  and co-managers share it. Personal db functions scope by the ref's user/team and raise without one. The server's
  modules never use `leagues.acting()` / `FFL_USER`. A membership (`memberships`) is a user's place in a league:
  team, default, display name, strategy. Page caches are per (user, league).
- **Persistence.** Postgres 16 features only. `db.engine()` never creates tables: a missing or stale schema raises
  `SchemaError`. A write is one short transaction, never held across an ESPN call. Callers outside `db.py` catch
  `db.DBError`; a DB error on a page read is a 500, only a missing row degrades to empty. Schema changes are new
  Alembic revisions under `migrations/` (`uv run alembic revision --autogenerate -m ...`); an upgrade is one
  transaction. Tests share one Postgres database per session (copied from a template at head), reset before each test;
  `FFL_TEST_PG` is the admin URL.
- **Draft room: one connection.** ESPN allows one draft-room connection per team and kicks the earlier one. Never open
  a second tab or a standalone websocket client: the watcher only listens passively over CDP to the tab you draft in,
  and only one whose URL carries its league's id (`draft_socket.room_page`). REST `mDraftDetail` shows nothing during a
  live draft.
- **The candidate table is not a verdict.** `draft_watch.recommend()` returns `score` as a default order plus the
  columns the decision is made on (`survive_prob`, `opp_demand`, `urgency`, `bye_risk`, `spread`, `injury`), against
  the league's strategy (`ffl.strategy show`; presets live in the `strategies` table, added by migrations). Do not
  encode new judgment as weights. Hard rules are the league's caps (`strategy.caps`) and the lineup blacklists. The
  rollout (`ffl/rollout.py`) is printed under the table, never merged into `score`; judge it with `ffl.replay` on real
  logs. The draft order is drawn at draft start: never hard-code a slot; `turn_math()` owns pick arithmetic.
- **Projections.** `proj` is the weekly consensus (`ffl/consensus.py`, Hodges-Lehmann; `spread` = trimmed range) of
  ESPN, Sleeper (rescored per position with the league's tables), CBS, FantasySharks and FleaFlicker. Scrapes are
  cached once for every league and scored at read (`sources.Cached`, written through to `source_cache`). Rest of
  season (`espn_season.season_lookup`) sums the weekly sources from the ROS week on; `season.ros_adj` shrinks it
  toward recent snap-weighted form (`K_PRIOR` games). The win meter is `season.win_prob` with σ per position from
  `sigma_fits`.
- **Live scoring.** The API holds one subscription to ESPN's public FastCast feed while games are on, rescores every
  player with each league's own stat table (`live.Lane` per league) and overlays points on cached payloads at serve
  time (`live.overlay_*`, pure). `GET /live` is an SSE stream of per-league deltas; its `cache-control: no-transform`
  is load-bearing (otherwise Next gzips the stream). The web port of the overlay (`web/lib/overlay.ts`) is held to the
  Python by `tests/test_live_overlay_fixtures.py` (rerun with `FFL_WRITE_FIXTURES=1` after changing an overlay). Every
  points change of a starter becomes a play-feed row (`plays.Store`); captions are the newest play naming the player.
- **Box scores.** Finished weeks are read from the `box_scores` table once final; the current week ESPN-first with a
  60 s row as the fallback. An hourly correction pass re-reads unsettled weeks and records stat corrections.
- **Outlooks.** One outlook per player per week, shared by every league and user (`outlooks` keyed
  `(season, week, player_id)`), written by `claude -p` from a fact sheet (no projections, no call). Only the hourly job
  (`POST /outlook/refresh`, job token only) writes; GETs never generate. Pending players go in batches; a fact hash
  decides what is rewritten. A sheet reads the stories about him, not his newest (`newsdesk/sheet.py`): arrows, then
  text, then excerpts cut for him; a story that only names him gives one line, which is outside the hash.
- **News desk** (`ffl/newsdesk/`): collect -> tag (rules, no model: `rules.py`, a status ladder per player; Sleeper's
  chart adds the next man up behind an out starter, under the push cutoff) -> write (Haiku via `claude -p`: headline,
  overview, a line per player it moves) -> check (Haiku, not told the arrow, reads the report behind each arrow that could
  push; a different answer flags it, a failed call holds the push) -> notices. Each stage runs alone; `POST /news/run` starts a run.
- **Push.** Detectors write a `notices` outbox (deduped by key); the tick flushes it per user (quiet hours, switches)
  as Declarative Web Push, because iOS often never fires `notificationclick` for a Home Screen app (WebKit 268797).
- **ESPN writes.** `POST /lineup`, `/roster/sit`, `/claims*` re-read ESPN's roster, take a per-league lock, validate,
  write through `espn_write`, record a decision, then read the page back. Claims: a claim is one add plus at most one
  drop and cancels when either is gone (`ffl/claims.py`).
- **Web.** Server components fetch the API through a `/api/*` rewrite. The open league is the URL's `?league=`
  (`web/proxy.ts` -> `x-ffl-league`); `components/league-link.tsx` carries it onto every link; the switcher changes
  league by a full page load.

## Conventions
- `uv run` for every Python command; `uv add` for dependencies.
- Work lands by pull request into `develop` (squash merge, linear history); `main` is what the published images are
  built from (merge commits from develop). The `test` workflow (pytest, web build, typecheck) runs on every PR.
- A migration's test upgrades to its own revision (never "head") and reads raw SQL, not the app's readers. Only the
  newest migration's test and the round-trip test (`tests/test_per_user_migration.py`) go to head.
- UI: use a shadcn component from `web/components/ui` (add one with `pnpm dlx shadcn add <name>`, style base-nova on
  `@base-ui/react`), else a well-tested library (Base UI, TanStack Table, Recharts, date-fns). Hand-roll only when
  neither fits, and say why in the commit. Check every UI change at phone width. `web/AGENTS.md`: this Next.js version
  differs from training data; read `node_modules/next/dist/docs/` first.
- Issues live in GitHub Issues. Specs, plans and design notes go in an issue's body or comments, not in .md files.
- Never commit `.env` or anything containing `espn_s2` / `SWID`.
- An upgrade is re-running `install.sh` against an existing `~/ffl`: it re-fetches `docker-compose.yml` (at the pinned
  `FFL_VERSION` tag, else the `release` git tag) and keeps `.env`, never adding a key to it. A change to the compose file,
  the images or `deploy/env.example` must work for an install upgraded that way; a key a new release needs goes in the
  app settings.
- Installed schedules run the released `install.sh --auto` for good, and it asks the running API `GET /upgrade/hold`:
  keep that flag and that route working, since an install cannot be reached to change its schedule.

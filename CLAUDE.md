# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is
Tooling and season record for a private ESPN fantasy football league (2026, **2-QB starting lineup**). Two jobs:
pre-draft board/strategy and a live draft assistant; then in-season tracking of every decision and result.

## Commands (always via `uv run`)
```
uv run alembic upgrade head            # create / migrate the DB schema (release.sh runs it on the box)
uv run python -m ffl.db import         # one-time: old journal/ + data/ files into the DB (idempotent; done 2026-10-08, originals in data/pre-db/)
uv run python -m ffl.db backup [dest]  # checked pg_dump -Fc to dest (dir or s3://bucket/prefix/; FFL_BACKUP_DEST, else Drive), keeps 14 (`ffl-backup.timer` on the box nightly 03:30, to s3://ffl-backups/db/)
uv run python -m ffl.db restore <src> --url URL [--to-rds]  # newest dump in a dir/s3 prefix, or a .dump file, into URL (required, or `--url-from-env`: FFL_DB_URL from the environment, e.g. `uv run --env-file ~/.config/ffl/rds-rw.env ...` (a write: `.env.rds` is read-only), so the RDS password stays out of argv; the target host/db is printed first; pg_restore --clean; a non-local target, or an RDS through the loopback tunnel (asked of the server: `rds_superuser`), needs --to-rds). Restore into an empty or disposable DB with the API stopped: the DROPs wait on live queries, objects not in the dump stay
scripts/devdb.sh [name] [--from-dump F | --from DB_URL | --from-sqlite F] [--replace] [--no-env]   # a local Postgres database ffl_<name> (default: the worktree's directory name), empty + alembic head or restored from a pg_dump -Fc file / a live DB / the old SQLite file; writes FFL_DB_URL into .env (--no-env: print it; mock, bench)
uv run python -m ffl.db pgcopy data/ffl.db   # the SQLite file (schema 0024, opened read-only) into FFL_DB_URL (at head): emptied, copied, sequences advanced, counts checked; exit 1 on any difference; a non-local or RDS destination needs --to-rds
uv run --env-file .env.rds python -m ffl.db counts postgresql+psycopg:///ffl env   # every public table's rows in A and B side by side (`env`: FFL_DB_URL), read-only, one snapshot each; exit 1 on any difference (a table on one side only too)
uv run python -m ffl.settings          # league settings from ESPN -> the DB (formats, league_settings, caps); prints the summary (run first)
uv run python -m ffl.leagues list      # the registry: key, season, my team, home, name
uv run python -m ffl.leagues add espn <id> [--season N] [--default]   # register a league for FFL_USER (their membership) and store its settings, formats and caps (reads ESPN)
uv run python -m ffl.leagues user add <email>   # a user for the CLI to act as, in a database nobody signs in to (a mock); `join <key> --user <email> --team <id>` files a membership
uv run python -m ffl.leagues caps espn:100000001 QB=3   # my roster caps per league (never above the league's; no args: the table)
uv run python -m ffl.players           # ESPN pool w/ league-scored projections -> espn_pools (per position scoring format; only what is missing, --refresh all)
uv run python -m ffl.sleeper           # Sleeper's raw season projections -> sleeper_season (once a season; --refresh); prints them rescored to the league
uv run python -m ffl.adp               # FFC 2QB + 1QB ADP for the league's size and PPR -> adp_feeds (re-run morning of draft)
uv run python -m ffl.board             # merge + VORP + tiers, built from the DB on demand; prints the markdown, writes nothing
uv run python -m ffl.browser           # headed Chrome on the draft room (cookies injected, CDP :9222)
uv run python -m ffl.browser shot x.png  # screenshot the running Chrome window
uv run python -m ffl.draft_watch       # LIVE: picks off the league's draft room socket (FFL_LEAGUE, else FFL_USER's default); writes the DB's draft_picks, draft_feeds, draft_states
uv run python -m ffl.advise            # LIVE: the candidate table, recomputed from the raw pick feed
uv run python -m ffl.avail WR 8        # LIVE: who is left at a position, by projection (the table hides replacement-level players)
uv run python -m ffl.simulate 7        # dry-run the draft from a slot (opponents pick to 2QB ADP); `all` for every slot
uv run python -m ffl.strategy show     # the league's strategy, the prose to advise from (--league KEY; `list`: presets and versions)
FFL_ROLLOUT=1 uv run python -m ffl.<mod>  # draft_watch / advise / simulate: add the Monte Carlo rollout (second opinion)
uv run python -m ffl.replay practice-8-slot4   # a stored draft log (draft_logs: practice:<name>, ffc:<id>) through score + rollout, from my seat (--league KEY: its draft_picks; --keep NAME --from postgresql+psycopg:///ffl_mock --league KEY stores a practice run's, read-only on that database)
uv run python -m ffl.ffc fetch         # FantasyFootballCalculator 10-team 2QB draft logs -> draft_logs (source ffc; `list`); `ffl.replay --human` replays every human seat
uv run python -m ffl.practice 4        # start a League Specific Practice Draft from slot 4 (see journal/PRACTICE_DRAFT.md)
uv run python -m ffl.autodraft q.txt   # draft from a written queue (practice runs; --live to arm it for real)
uv run python -m ffl.draft_watch once  # one-shot REST snapshot (pre-draft sanity check only)
FFL_LEAGUE=<key> uv run python -m ffl.draft_watch store   # a finished draft nobody watched: ESPN's own record -> that league's draft_picks (no pick times; once)
uv run python -m ffl.journal sync      # in-season: transactions + weekly results into the DB
uv run python -m ffl.playbook backfill 1-6   # finished weeks' NFL plays from ESPN's drives, and their called-back rows (the tick keeps new games current)
uv run python -m ffl.newsdesk run      # one news-desk run (collect, tag, write, queue pushes); seed / status (`ffl-news.timer` POSTs /news/run every 10 min)
uv run python -m ffl.newsdesk bench    # replay frozen news (`bench freeze` first) through tagging with saved Jev answers, against ffl/newsdesk/bench_cases.py; --player grupe, --write (Haiku too), --refresh
uv run python -m ffl.decisions list --open   # decisions still needing a rationale or outcome; add / outcome / rationale / show / export
uv run python -m ffl.calibrate         # in-season: score the K of the league's strategy (`uv run python -m ffl.strategy show`) (usage-adjusted ROS) against the weekly snapshots in the DB (calibration_*)
uv run python -m ffl.calibrate winprob # in-season: fit the win meter's σ per position (sigma_fits) + Brier; `ffl-winprob.timer` runs `calibrate wednesday` (winprob, then sources) Wed 07:00
uv run python -m ffl.pace              # in-season: who is beating his projection, every roster + the wire (write-up rules: .claude/skills/pace-report)
uv run python -m ffl.sources compare   # old (ESPN+Sleeper) vs consensus projection for every rostered player; status: source health
uv run python -m ffl.calibrate sources   # fit per-source weights (stored inactive); --activate <fitted_at> applies them
uv run python -m ffl.props score       # props trial (weeks 4-6, ended 2026-10-21: failed): ESPN+Sleeper vs +props MAE and the verdict, off props_snapshots
uv run uvicorn ffl.api:app --host 127.0.0.1 --port 8300   # season API (production: ffl-api.service on the box; this line is dev only)
curl -X POST -H "x-ffl-job: $FFL_JOB_TOKEN" 127.0.0.1:8300/outlook/refresh   # regenerate Roster-page outlooks whose facts changed (`ffl-outlook.timer` does this at :05 hourly)
curl -X POST -H "x-ffl-job: $FFL_JOB_TOKEN" 127.0.0.1:8300/notify/tick   # sync + injury/waiver checks + push flush + pre-game pull (`ffl-notify.timer` every 15 min)
curl -X POST -H "x-ffl-job: $FFL_JOB_TOKEN" 127.0.0.1:8300/refresh       # force every page, projection and injury cache to refetch now, and wait (no UI button; every league, `?league=` for one)
# $FFL_JOB_TOKEN is not in a plain shell: on the box `set -a; . /etc/ffl/env; set +a` first (as root or ffl); a dev API's is in the worktree's .env (never echo it)
uv run python -m ffl.notify keys       # one-time: VAPID keys for .env
uv run python -m ffl.creds set --user <email>   # a user's ESPN cookies, sealed into their row (stdin: ESPN_S2=/SWID= lines); status / clear
uv run python -m ffl.vault keygen      # a new FFL_SECRET_KEYS entry; `rotate` re-seals every row with the ring's first key
uv run python scripts/pageload.py       # load time of every season page on a throttled phone (default: prod :3300; point it at https://ffl.whitebridgelabs.com for production; signed out it measures the /signin redirect)
curl -b "ffl_session=<cookie>" 127.0.0.1:8300/live/status   # the live-scoring feed: connected?, version, players scored (FFL_LIVE=0 keeps a dev API off it); every GET but /health and /auth/* needs a session (a browser's ffl_session)
cd web && pnpm dev                     # season UI on :3301 against the API above
cd extension/ffl && pnpm build         # the browser extension (#130) -> dist/chrome, dist/safari for production (FFL_ORIGIN=https://... picks another server; `pnpm build:dev` the :3302 test server); `pnpm test` (Vitest, pure.ts); the Safari wrapper (extension/spike-safari, Xcode) rebuilds dist/safari itself (ffl-origin.sh, the iOS extension's first phase) for FFL_ORIGIN, else the configuration's FFL_ORIGIN_DEFAULT (Debug the test server, Release the live site), and its app opens the same server (Info.plist FFLOrigin)
scripts/install.sh                     # the Mac's one launchd agent, the ffl-rds tunnel (`--tunnel`, the old spelling, too)
uv run --env-file ~/.config/ffl/rds-rw.env python -m ffl.avail WR 3   # any draft tool against production's RDS (it writes: the write credentials; see Config)
```
Pipeline order: settings -> players -> adp -> (browser + draft_watch); Sleeper is fetched on first use, and every
draft tool builds the board from the DB (`board.load_inputs(ref)` + `build`; the ADP feed is `board.adp_mode(settings)`:
2QB when the lineup starts two QBs or an OP, `FFL_ADP_MODE` overrides).

## Config
**ESPN cookies are each user's own rows** (ffl-soc.3, `ffl/espn_auth.py`): `espn_credentials`, one row per ESPN account (#460, 0033: a user may connect several; `swid_hash` = `db.swid_hash(SWID)` names the account, filled on first open for a row from before 0033; a row this ring cannot open is dropped by the user's next connect, never left standing as their first account) (espn_s2 + SWID sealed as one token by `ffl/vault.py`, AES-GCM, the user's place as associated data), stored with `uv run python -m ffl.creds set --user <email>` (stdin: `ESPN_S2=`/`SWID=` lines, never argv, never printed; `status`, `clear`). No code reads cookies from the environment (`tests/test_no_env_cookies.py`); ESPN_S2/SWID lines in an env file reach nothing. Each membership names its account (`memberships.espn_account`, NULL = the user's first; `espn_auth.own_for(ref)`), and every call for a league goes out under that account's cookies. A write goes out under the acting member's own cookies only (`espn_write._own`: none is `NotConnected`, refused ones `Refused`, each a plain reason); the pending-claims read (ESPN shows a team's claims to it alone) under the team's (the member's, else a co-manager's: `espn_auth.for_team`; `_READ` kept per (league, team)); a shared league read under the member's own, else another member's that work (`espn_auth.for_read`; `config.league` remembers which cookies built it); a 401/403 marks that account failing (`failing_since`, lent to nobody; `espn_auth.refused(cookies)`) and the read goes again under the next member's; nobody's left on a private league is `espn_auth.NoConnection` (the page's 502 says so). The NFL-wide reads (past schedules, `leaguedefaults` cards) send none. A user connects through `ffl/espn_connect.py` (ffl-soc.6.3.3): the browser extension pairs by a code it shows and the signed-in user types (`POST /espn/pair/start`, `/espn/pair/poll` and `/espn/cookies` are public, each holding by its own secret: poll secret, device token; never flip the direction), then sends the cookies with its device token (`extension_devices`, 0032; the extension is `extension/ffl`: storage.local holds the token, a pending pairing, a SHA-256 of the last login sent and the last answer, never a value; a 200 or 422 remembers the hash, a 429/5xx retries on the next ESPN page; it reads per cookie store, the content script's document.cookie the fallback, every SWID there is (`cookies.getAll`: ESPN's scripts write SWID too, so a browser can hold a stale one, and `cookies.get` returns the oldest; the rest go as `others` and `espn_connect.connect_any` takes the first the fan API knows), a refused send shown on `/espn/status` as `refused` for 15 minutes so the wizard says why no login came, and watches the login every 3 s while an ESPN tab is in view; `api.py`'s CORSMiddleware answers only `chrome-extension://`/`safari-web-extension://` origins, no credentials); `POST /espn/connect` is the manual paste. Both validate against ESPN (fan API off the SWID, then a private league read under the cookies), store a new SWID as another account beside the others (the same SWID however copied updates its row), and store espn_s2 exactly as sent. `/espn/status` lists the accounts (id, state, the leagues each serves). `/espn/disconnect` deletes every account and unpairs every extension; `{account: id}` deletes that one alone; either way the user leaves every league the account served (`db.drop_memberships`: memberships, member state and caps, their page_cache rows; `espn_connect.ON_LEAVE` drops the in-memory caches; their decisions and watchlist stay for a rejoin, the league stays registered): a league left on would read on through a league-mate as if another member used the app. The leagues to pick (ffl-soc.6.3.4): `GET /espn/leagues[?account=id]` lists the fan API's entries for every SWID in the user's own stored accounts, or that one (the wizard adds one login at a time, and that login refused or unlistable there is its error, 422/502, never a greyed list: greyed leagues are for a login that expired after it was added, in the full list Settings shows; never a SWID a client names; a league under two accounts is the first-connected one's unless only the other can pick it), each with the team ESPN lists them on and its `account` id, read by mSettings + mTeam under that account's cookies (a 401/403 marks it failing). A league that can't be picked stays listed, greyed by the web, with `problem` {error, title, message (the hover), fix (the dialog)}: `refused` (a failing login's leagues are listed off the fan API alone, never read), `not_your_team`, `espn_unavailable`; an account the fan API can't list is a `problems` row; kept 5 minutes per user. `POST /espn/leagues {ids, default}` re-reads that list and runs `leagues.connect` per pick: an unknown league's settings read and stored first (`settings.collect`, under their cookies though no member yet; a failed read files nothing), then the membership naming that account, strategy NULL; a registered league is a membership only. A league outside their own entries answers exactly as one that does not exist; one league failing stops none of the others; the first pick is the default only for a user with no league. `POST /espn/leave {league}` stops following one (Settings' switch; `db.drop_memberships`), `?fresh=true` reads ESPN past the 5-minute list cache. UI: the wizard is `/welcome` (`components/connect-wizard.tsx`: Connect finds the extension in this browser (its content script runs on FFL's own site too and answers by postMessage, `web/lib/extension.ts`) and links it with one tap, "Connect this browser": the extension starts a pairing and the page confirms its code under its own session, then sends the user to ESPN with a link; the typed code is the fallback, and the Home Screen app, where Safari's extensions never run, says to connect in Safari; or a paste on a computer, polling `/espn/status` for the login to arrive; Leagues for that one login; Set up one league at a time from the page; a first run, or `?add=1` from Settings, `?paste=1` straight to the paste), Settings › ESPN connection is `components/espn-connection.tsx`, both off `lib/espn.ts` (pure, tested); the install buttons show only when `FFL_TESTFLIGHT_URL` / `FFL_CHROME_STORE_URL` / `FFL_MAC_APP_URL` are set on the web. Never log a value, a code, a token or the fan API's URL (it carries the SWID). `ffl.leagues add` uses the acting user's first account and `ffl.browser` the league's; the team is still the membership's (`leagues.my_team_id`). **The key ring** is `FFL_SECRET_KEYS` (`<id>:<base64 32 bytes>[,...]`, the first seals, every one opens; `uv run python -m ffl.vault keygen`; rotate: put a new key first, `uv run python -m ffl.vault rotate`, drop the old): on the box `/etc/ffl/env` from SSM `/ffl/env/FFL_SECRET_KEYS`, on the Mac's draft tools `~/.config/ffl/rds-rw.env`, in a worktree its `.env` (`scripts/devdb.sh` writes a dev ring; a row sealed under production's ring reads as not connected there). The API will not start without it and `release.sh` refuses a box without it (never generating one). Push keys (`push_subscriptions.keys`) are sealed the same way (0031). League facts (id, season, my team) live in the `leagues` table (`ffl/leagues.py`; `uv run python -m ffl.leagues list`; migration 0014 seeded the home row from the old `LEAGUE_ID`/`SEASON`/`MY_TEAM_ID` lines); `config.SEASON` is derived from the date (`nfl_season`), (a league's team is its member's: `memberships`; 0029 dropped `leagues.home`/`team_id`, and config carries no league). A league's settings are DB rows, not files (`ffl/formats.py`, migration 0015 imported the old `data/settings.json`): per-position scoring tables in `scoring_formats` (one row per distinct table, shared by every league on it; `rescore_key` covers only the stats projections are rescored on), linked per league in `league_scoring`; the lineup in `lineup_formats` (`leagues.lineup_format`); everything else in `league_settings`; `league_caps` holds the league's position limits and `member_caps` each member's own caps (0030; `leagues.caps`: the lower). `settings.load(ref)` puts back the dict settings.json held, plus `first_week`, `league_key`, `scoring_tables` (the per-position tables rescoring reads) and `scoring_items` (ESPN's raw items, which live scoring reads; None until `ffl.settings` re-runs after the import); `formats.league_table(ref)` is the per-position scoring.
**No implicit league** (ffl-soc.4.12): every function on league data takes the league (`ref`, required); nothing falls back to a home league (`tests/test_no_implicit_league.py` greps for one). The CLI acts as `FFL_USER` (the sign-in email, in `.env`; unset is an error naming the fix) on `FFL_LEAGUE=<key>` (or `--league`), else that user's default (`leagues.acting()`): `ffl.settings`/`players`/`adp`/`board`, the draft tools (advise, avail, simulate, draft_watch, ...), `journal`, `decisions`, `pace`. The box's timer CLIs need no user: `calibrate wednesday` fits over every league, `db backup` has no league. What belongs to no league (an NFL-wide read through a league's view, notices about the app itself: `notify.app_notices`) uses `leagues.oldest()`.
Push: `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` in `.env`; `FFL_NOTIFY=0` keeps a dev API from pushing.
News: `TYPESAFE_API_KEY` in `.env` is Jev's (TypeSafe AI), which tags and groups the newsdesk's items (`ffl/newsdesk/jev.py`); without it the tag stage stops and nothing new reaches the feed.
Sign-in (ffl-soc.6.2.1): every API route needs a session (Google: `FFL_GOOGLE_CLIENT_ID`/`_SECRET`, `FFL_AUTH_SECRET`; sign-up closed to `FFL_SIGNUP_EMAILS`, `*` opening it only as the sole entry; one user per email in any case (unique `lower(email)`, `db.sign_in_user` one transaction); `/notify/tick` sweeps expired sessions) Apple (ffl-soc.6.2.5, optional: `FFL_APPLE_CLIENT_ID` (the Services ID)/`_TEAM_ID`/`_KEY_ID`/`_PRIVATE_KEY` (the .p8 base64'd onto one line), https `FFL_APP_URL` only; the client secret is an ES256 JWT `auth._apple` rebuilds daily; Apple's answer is a cross-site form POST that carries no Lax cookie, so `POST /auth/apple/callback` 303s it to the GET callback, which checks state and nonce like Google's; Apple's `email_verified` may be the string "true"; the name comes only in the first sign-in's `user` field) or the job token (`x-ffl-job` = `FFL_JOB_TOKEN`, which the box's timers send; `release.sh` fills it when empty and refuses a release with the Google client, `FFL_APP_URL` or `FFL_SIGNUP_EMAILS` empty). Account (ffl-soc.6.2.4, `ffl/auth.py`, a session only, never the job token): `GET /account` (sessions, sign-ins, linkable providers), `POST /auth/sessions/{id}/revoke` (id = the stored token hash), `/auth/signout-others`, `/auth/delete` (`{email}` typed; `db.delete_user`: the users row's CASCADEs plus page_cache, and a team's rows only when nobody else is on it; leagues stay, and a league nobody is in is walked by no job: `leagues.joined()`), `GET /auth/google/link` (the sign-in callback links instead, `link_user` in the oidc cookie; never moves another user's identity). Settings and `/welcome` show it (`components/account-settings.tsx`). `FFL_DEV_SIGNIN=1` (worktree test servers on a local database only; `release.sh` refuses a box that has it) adds dev sign-in. A test server sets the same `FFL_SESSION_COOKIE=ffl_session_<web port>` on its API and its web (a browser shares cookies across ports, so two test servers on one host would sign each other out); production leaves it unset (`ffl_session`).
`FFL_RECORD_FAKE=1` (worktree test server only): fake moves (FFL_WRITES=0) record their decisions anyway, titled `[fake] …` with `facts.fake`.
`FFL_DB_URL` (`postgresql+psycopg://...`, in `.env` or the environment) names the one database; Postgres is the only one, and unset or any other scheme is a `SchemaError`. A worktree makes its own with `scripts/devdb.sh` (local servers only; it writes that one `.env` line); an env var beats `.env`.
**Production** is the box `ffl-box` (EC2 t4g.small, AL2023 aarch64, us-east-1; tailnet 100.64.0.10, public 203.0.113.10, ssh alias `ffl-box`; cutover 2026-10-26), systemd units from `deploy/box/` (`setup.sh` builds the box, `release.sh <sha>` installs a release): `ffl-api` (uvicorn 127.0.0.1:8300), `ffl-web` (`next start` 127.0.0.1:3300), Caddy on :443 for https://ffl.whitebridgelabs.com (`deploy/box/Caddyfile`; DNS-only A record to the tailnet IP, DNS-01 cert: tailnet-only), and timers `ffl-notify` (15 min), `ffl-news` (10 min), `ffl-outlook` (:05), `ffl-transactions` (:35), `ffl-winprob` (Wed 07:00), `ffl-backup` (03:30; pg_dump to s3://ffl-backups/db/, versioned). The curl timer jobs start after `ffl-api.service` and retry a refused connection. Env is `/etc/ffl/env` (names in `deploy/box/env.example`; `FFL_SOURCES_OFF=sharks,youtube_captions` there). Code is `/opt/ffl/current` -> `/opt/ffl/releases/<sha>`, data dir `/var/lib/ffl/data`. A box lost for good is rebuilt by `deploy/box/rebuild.sh` (operator's terminal; `--rehearsal` on a throwaway `terraform/rehearsal` instance): `apply -replace`, then `setup.sh` + `deploy/box/dolt/dolt-setup.sh` (beads' Dolt server, restored from GitHub `refs/dolt/data` and the nightly `s3://ffl-backups/dolt/` snapshots) over SSM Run Command, secrets from SSM `/ffl/*` SecureStrings (`deploy/box/secrets-to-ssm.sh` copies the box's), then a deploy.yml run. Logs and timers over ssh: `journalctl -u ffl-api`, `systemctl list-timers 'ffl-*'`. The database is Postgres `ffl` on the Cantrip RDS (`shared-db`, PG 16); the Mac's local `ffl` database and `data/ffl.db` are cold history, kept as they are. Production and its recovery never rely on the Mac (ffl-soc.6.1.19 retired its fallback: `fallback.sh`, `deploy.sh`, the production plists and the post-merge deploy are gone): EC2's own recovery (the status-check alarms) or `rebuild.sh`. The old https://macbook-pro.tailnet.ts.net:3300 address is retired.
The Mac's path to the RDS (production's database; draft tools run against it): the launchd agent `com.jonulak.ffl-rds-tunnel`
(`launchd/`, loaded by `scripts/install.sh`, the one agent the Mac has; log `~/Library/Logs/ffl-rds-tunnel.log`) owns `ssh -N ffl-rds`, a `Host ffl-rds`
block in `~/.ssh/config` (the box `203.0.113.10`, `ec2-user`, `~/.ssh/cantrip-ec2`, `LocalForward 5434
db.example.internal:5432`, ExitOnForwardFailure, ServerAlive 30/3, IdentitiesOnly, ControlMaster no):
the RDS's security group admits only the box. Local 5434, since Postgres.app's `cantrip_db` holds 5433; the public IP, since
Tailscale SSH on the box intercepts :22 on its tailnet IP and the tailnet policy has no ssh rule for it (closing the box's
public SSH needs that rule first, or this tunnel and `cantrip-beads` both die). Two credential files, both 0600, neither in any worktree
(ffl-soc.6.1.19: a worktree cannot reach production's database by default). The main checkout's `.env.rds` (gitignored) is
**read-only**: `FFL_DB_URL=postgresql+psycopg://ffl_ro:<pw>@127.0.0.1:5434/ffl?sslmode=require`, the role `ffl_ro` (SELECT on
every table in `ffl`, new ones too through `ALTER DEFAULT PRIVILEGES FOR ROLE ffl`; every write is "permission denied"), for
looking: `uv run --env-file ~/Developer/FFL/.env.rds python -m ffl.db counts ...`, queries. Writing takes the deliberate
switch: `~/.config/ffl/rds-rw.env`, outside every checkout, is the `ffl` role (the password the box's `/etc/ffl/env` holds),
which every draft tool needs, since they write (`sleeper_season` on first use; a draft writes production's draft tables):
`uv run --env-file ~/.config/ffl/rds-rw.env python -m ffl.<tool>` (the env var beats `.env`). Never copy either into a worktree.
Mock/practice runs: `FFL_DB_URL=postgresql+psycopg:///ffl_mock` (`scripts/devdb.sh mock --no-env`; the mock league registered as
home in it) keeps the real board and pick log untouched. `FFL_DATA_DIR` (`data-mock`) chooses only the data directory (browser profile, caches); it implies no database, and `ffl.autodraft` refuses unless `FFL_DB_URL` names a `*mock*` database or `--live`.
Full practice-draft procedure (both modes, and what a practice draft cannot test): `journal/PRACTICE_DRAFT.md`.
`data/` is gitignored and regenerable (caches, the news bench's `jev-cache.sqlite`, the browser profile); the season's data is the Postgres database, backed up nightly by `ffl.db backup` (pg_dump to Drive/S3), not by git.
`journal/` is the durable prose and is committed. The strategy is not a file: presets live in the DB's `strategies` table (added and versioned only by migrations; 0016 holds the old strategy file's text as `2qb-10team` v1, the home league's; 0020 added `1qb-10team` v1, generic 1QB draft rules over home's in-season sections, which the public league `espn:950458410` plays, its caps ESPN's own limits) and `uv run python -m ffl.strategy show` prints the league's.

## Architecture notes
- `ffl/config.py` owns env + the `League` singleton; `raw_league_get(view, ...)` hits ESPN views directly.
- `ffl/draft_watch.py` gets live picks from the draft room's **websocket**, read passively over CDP
  from the open Chrome tab (`ffl/draft_socket.py`). ESPN's REST `mDraftDetail` view is useless during
  a live draft — every slot stays at `playerId: -1` until the draft finishes — so REST mode
  (`draft_watch once` / `rest`) is only for pre-draft checks and post-draft recaps.
- **Never open a second draft-room connection.** ESPN allows one per team and kicks the earlier
  session out: a duplicate tab, or a standalone websocket client, will boot you out of your own
  draft mid-pick. The watcher only listens to the tab you are already drafting in, and only to a tab whose
  URL carries its own league's `leagueId` (`draft_socket.room_page`; `draft_pick` clicks only there too).
- `ffl/board.py::build()` is pure over (settings, players, adp, drafted_ids) so the watcher rebuilds it every poll;
  replacement level is computed from the still-available pool. Starter counts come from `settings["starters"]`
  with FLEX/OP slots split across positions (see `FLEX_SPLIT`).
- `ffl/draft_watch.py::recommend()` returns a **candidate table, not a verdict**: `score` is the default
  ordering; the decision is made on the columns beside it (`survive_prob`, `opp_demand`, `urgency`,
  `bye_risk`, `spread`, `injury`) against the hard rules in the league's strategy (`uv run python -m ffl.strategy show`). Do not encode new judgment as weights —
  seven practice drafts of that is how the board came to disagree with the strategy. Roster rules that are
  genuinely hard are the league's caps (`strategy.caps`: league data, seeded from the preset's `suggested_caps` on
  first read, then owned and changed with `ffl.leagues caps`; QB 3, TE 2, K 1, D/ST 1, RB/WR 7 at home), which
  `recommend(caps=)` and the rollout read, beside the lineup blacklists (a second K/D/ST, a second non-flex backup,
  K/D/ST before the run-in); everything else is a rule in the strategy.
- **The draft order is drawn at draft start.** The league's stored settings hold ESPN's placeholder (teams in id order)
  until then; `FFL_PICK_ORDER` overrides it and `learn_order()` rebuilds it from round 1's picks. Never
  hard-code a slot. `turn_math()` owns all pick-number maths and is shared by the watcher, `advise` and
  `simulate` so they cannot drift.
- `ffl/rollout.py` is the Monte Carlo second opinion, off unless `FFL_ROLLOUT=1`. It writes nothing and is pure over (board,
  order, rosters, candidate table), except that its caps and QB pace default to the league's (`strategy.caps` / `qb_pace`, read from the DB); it is printed *under* the table, never merged into `score`. Opponents draft
  to ADP + spread under roster constraints; my later picks are planned by assignment of open slots to remaining
  picks (`plan_position`); candidates are scored by `season_points` (best lineup, byes out, per week).
  Judge it with `ffl.replay` on real logs, not by tuning it until a simulation looks right.
- Projections: `proj` is the weekly **consensus** (`ffl/consensus.py`, Hodges-Lehmann; `spread` = trimmed range) of ESPN
  (`proj_espn`, league-scored by ESPN; an input only, 0 is a placeholder), Sleeper (raw stats rescored in `ffl/sleeper.py::score` with one position's table, `settings.rescoring(settings)[pos]`: the league's per-position formats, so a TE premium reaches TE only),
  CBS, FantasySharks and FleaFlicker. `ffl/sources/` holds cbs, sharks, fleaflicker (`Cached`: TTL + last good + thin guard; each entry the parsed stat lines keyed by week (or `ros`) alone, one download for every league, scored at read with the league's tables (`sources.scored`, the parsers' rounding; ffl-soc.4.16: a second league's cold build fell from 29 s to under 5); `_sleeper_for` likewise keeps Sleeper's raw rows per week (`_SL`) and each scoring's parse (`_SL_SCORED`); only sharks' ROS sum, which downloads nothing, is keyed by `sources.scoring_key`; every good fetch written
  through to the `source_cache` table and restored on a source's first read after a restart, inside its TTL as fresh, past it as last good;
  without it a restart's cold Matchup build took ~27 s, ~3 s with it); health is
  `STATUS` / the `source_status` state, `GET /sources/status`, `ffl.sources status`, and a notice after 24 h failing. `FFL_SOURCES_OFF` (comma list of `config.SOURCES_KNOWN`, an unknown name warned and ignored; `config.source_off`) drops a source on one host, absent rather than failing (`espn_season._on`; a finished week's late keep stops waiting for it; `sources.on_only` keeps it out of `/sources/status` and the 24 h notice): the box sets `sharks,youtube_captions` (both refuse AWS addresses; ffl-soc.6.1.14/.15), `media.run_all` then takes no video for captions, the Mac sets none. K and D/ST use
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
- The ROS that `add` and the deltas run on is **usage-adjusted** (`season.ros_adj`, the league's strategy, "Usage-adjusted
  ROS" (`uv run python -m ffl.strategy show`)): the model's per-game rate shrunk toward his window's `l3` by the window's snap-weighted game count
  (`form["weight"]`, each game = snap share ÷ that week's `season.snap_reference` median for the position's lead
  players, capped at one), the prior worth `season.K_PRIOR` (4) games. `ros_proj` stays the sources' consensus beside
  it. K is checked, not tuned: every pool build snapshots what the blend read (`ffl/calibrate.py`,
  the `calibration_snapshots`/`calibration_rows` tables, once per ROS week: the first build stands) and `ffl.calibrate` scores the Ks against what happened.
- Recent form (the league's strategy, "Recent form" (`uv run python -m ffl.strategy show`)): `season.recent_form` is pure over a player's weekly game rows
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
  30-min cache, last-good on failure). The league's settings carry `week1_start`, which `season.week_of` needs to turn a
  return date into a week — re-run `uv run python -m ffl.settings` on the main checkout after deploying this, or the
  feed degrades to notes only and the OUT/IR constants keep pricing.
- **Position caps**: `settings["position_limits"]` (ESPN's `rosterSettings.positionLimits`, keyed by *position* id, not slot
  id: QB 4, RB 8, WR 8, TE 3, K 3, D/ST 3; IR counts). `claims.validate` errors on a target at a full position whose group
  has no cut of his; `claims.cap_warnings` (non-blocking, `warnings` in `/claims`) on a group whose wins could pass a cap.
  Both pages show `position_caps` (`season.position_caps`). Stored settings without the key show and check nothing:
  re-run `uv run python -m ffl.settings` on the main checkout after deploying it.
- **Draft inputs** (`board.load_inputs(ref)`, the one place every draft tool gets its board from): ESPN's pool per position and
  scoring format (`espn_pools`, `players.store_pool`/`pool`: a leagues-shared row per format, ownership never stored, a position
  older than `POOL_MAX_AGE_H` = 24 h refetched by `ffl.players` through the league being drafted; `pool()` itself never fetches),
  Sleeper's raw season projections (`sleeper_season`, rescored per league at read) and the FFC ADP feeds (`adp_feeds`, for the
  league's size and PPR). The board is built on demand and written nowhere. **Mid-season draft**: when `settings["first_week"]` > 1
  (`board.start_week`; `board.banner` warns on the board, advise and the watcher), `proj` is the rest of the season
  (`board.ros_players`: `season_lookup`'s sourced ROS scaled to the fantasy weeks left, else the season total scaled the same way),
  byes already played cost nothing and `weeks_left` replaces 17 in urgency and the rollout; ADP stays preseason, so survive% is rough.
  It also lays ESPN's injuries feed over the pool's lagging status (`board.with_injuries`: `return_week`; a return past the season zeroes `proj`,
  any other leaves it to the sources) and shows recent usage beside the table (`board.usage` off Sleeper's stat rows for the three finished weeks
  before the draft's week: `snap`, `tgt`, `trend`; columns only, never in `score`). ESPN reports a league's first scoring week as 1 until it has
  drafted; `settings.first_week` takes the week after the draft date when the season is under way (2026-10-25's public draft: 8, as ESPN then said).
  The draft tables are keyed by league (`draft_picks`, `draft_feeds`, `draft_states`), and the watcher reads only its league's room.
- ADP source is FantasyFootballCalculator's public API (FantasyPros gates its table behind login). The 2QB feed
  is primary; `adp_1qb` is kept for comparison.
- **Persistence** (`ffl/db.py`, SQLAlchemy Core + Alembic, no ORM; the only SQLAlchemy importer): Postgres at
  `FFL_DB_URL` (production RDS PG 16, local Homebrew PG 18: nothing may need a feature past 16). Tables, with the files each replaced: `leagues` (the registry, `ffl/leagues.py`; no file, seeded by migration 0014 from `.env`), `strategies` (presets, migration 0016 from the old strategy file), `scoring_formats`/`league_scoring`/`lineup_formats`/`league_settings`/`league_caps` (`ffl/formats.py`, from `data/settings.json` by migration 0015); `transactions` ← `journal/transactions.csv`;
  `draft_picks` ← `journal/draft/picks.csv` (keyed by league, season, overall; migration 0018 filed the 2026 draft under the
  home league; a practice run's are in its own database, `ffl_mock`); `draft_feeds` ← `data/socket_picks.json` (the room's raw
  feed and attach offset, per league: the watcher writes it, advise/avail/autodraft/draft_pick read it); `draft_states` ←
  `data/draft_state.json` (the watcher's state, per league, which autodraft reads); `results` ← `journal/results/weekly.csv`; `plays` ← `data/live/plays-week-<n>.jsonl`; `outlooks` (kind
  `week`/`waivers-week`) ← `data/outlook/*.json`; `calibration_snapshots` + `calibration_rows` ← `data/calibration/week-<w>.json`;
  `sigma_fits` ← `sigma.json` (latest wins, history kept; `calibrate.load_sigma` never fails); `claim_plans` ←
  `journal/waivers/week-<n>.json`; `sits` (players sat by hand for a week, no file before it); `box_scores` (ESPN's raw matchup read per week; `fetched_at` moves on every read, `settled` after the Thursday following the week, `final` when read after the week finished); `gap_games` ← `_GAP_GAMES`; `espn_pools` (per position scoring format) ← `data/espn_players.csv` and `adp_feeds` ← `adp_2qb.csv`/`adp_1qb.csv` (0017), `sleeper_season` (raw, fetched on first use; `sleeper_players.csv` was already rescored); `box_score_corrections` (old/new points per player); `source_cache` (`sources.Cached`'s scrapes); `page_cache` (`api.Cache(persist=True)`:
every installed page payload in `api.PERSIST` that survives a JSON round-trip, written through under `_db_lock`; `load()` at boot takes them
back expired with no builder, so the first open after a restart is served at once and rebuilt behind; `drop`/`drop_prefixes`/`clear`
delete the rows; player cards older than 7 days pruned at load; a key's inline first build is single-flight (`ffl/flight.py`): callers arriving
while it runs share its payload or its 502, as do concurrent cold reads of `sources.Cached`, `_sleeper_for` and `box_scores`); `decisions` ← `journal/DECISIONS.md` (frozen; `ffl.decisions` parses it on import; app rows carry `facts` and a rationale of preset keys, NULL = open);
  migration 0019 (insert-if-absent from the files, which it leaves in place): `traces` ← `data/trace.jsonl` (`POST /trace`, `db.trace_rows()`);
  `draft_logs` (source, log_id; `rows` the pick-log shape, `meta`) ← `data/ffc/<id>.csv` (source `ffc`, `ffl.ffc fetch`) and
  the committed `journal/draft/*.csv` pick logs (source `practice`, the file stem; `ffl.replay` names a log `source:id`);
  `props_snapshots` (week, kind) ← `data/props/week-<n>-<kind>.json` and `props_raw` (week, kind, event_id) ← `data/props/raw/`.
  The news bench lives in two Postgres databases on FFL_DB_URL's server (`bench freeze` makes `ffl_bench`: the collected items untagged, the sources and the player index in its `bench_index` table; a run makes `ffl_bench_run` from it with CREATE DATABASE ... TEMPLATE and points FFL_DB_URL there) and the stdlib SQLite `jev-cache.sqlite` (saved answers, `last_runs`), and `claude -p` runs from `claude_cli.neutral_cwd` (`<tmp>/ffl-claude-cwd/<name>`, no CLAUDE.md
  above it), so the app writes nothing under `data/` but that cache. `league, season` lead the natural keys of the league data (migration 0021: transactions, results, plays, box_scores, gap_games, box_score_corrections, outlooks, calibration_*, projection_rows, claim_plans, sits, seats, watchlist, watch_signals, notices, decisions; existing rows filed under home; `called_back` too (0023: a called-back row is one league's starter priced on its table)), as of the draft tables (0018), and `page_cache` (0022: `league, season, key`, all home's rows); `league_state` (0022, `league, season, key`) holds the league's own state keys (pregame_pulls, final_plays_done, news_to_notify, espn_week; `ffl.db.LEAGUE_STATE_KEYS`); `db.get_league_state/put_league_state` also reach a member's keys (`member_state`: notify_prefs, decision_prefs, watch_state) and their team's (`team_state`: pending_claims, waiver_run) through the member's `ref` (0030), `notify_state` keeps only global keys (news_mutes, prefs, player_presets (0024: the Players views are every league's), source_status, news_* tags/usage/last_run); the global tables (nfl_plays, news_*, source_cache, sigma_fits, source_weights, push_subscriptions, notify_state) keep `season` first (`plays` is keyed by an autoincrement `id`, unique on `(league, season, week, player_id, at, total)`; `draft_logs`, `traces` and the props tables carry no season). db functions on league data take a required `ref` (keyword-only). A write is one short transaction (`db._write()`), never held
  across an ESPN call, on a small pooled psycopg engine (5 + 5 overflow, pre-ping: RDS's connection cap is shared with another app). `db.engine()` never creates tables: a
  missing or stale schema raises `SchemaError` (run `uv run alembic upgrade head`); a DB error on a page read 500s the page, only a missing
  row degrades to empty. Callers outside `db.py` catch `db.DBError`. An upgrade runs in one transaction, DDL included (`migrations/env.py`; Postgres DDL is transactional): one that fails part way leaves the revision it started from, re-runnable, and a file import in a migration skips an unreadable file rather than fail the deploy. Schema changes are new Alembic revisions
  (`uv run alembic revision --autogenerate -m ...`, batch mode, under `migrations/`). `uv run python -m ffl.db import`
  (`ffl/db_import.py`) is insert-if-absent from the old files, with per-table counts and dupes; it exits 1 when short or when a
  non-plays table has shared keys (the cutover ran it 2026-10-08; the originals are in `data/pre-db/`). The box's `release.sh`
  runs `alembic upgrade head` before the restart. `uv run python -m ffl.db backup` (`ffl/db_backup.py`) takes a checked `pg_dump -Fc` (client major >= the server's, found on PATH or under Homebrew/Debian's `postgresql@N`; the dump's `alembic_version` must equal head) to `ffl-YYYY-MM-DD.dump` in a directory or S3 prefix (`ffl/db_backup.py`'s `DirStore`/`S3Store`, boto3), keeping 14; a .dump opens with `pg_restore` as it is (no WAL copy step), `ffl.db restore` and `ffl.db pgcopy` refuse a non-local target without `--to-rds`: local means a loopback/socket host whose server has no `rds_superuser` role (`db_backup.is_local`, asked of the server, so the ffl-rds tunnel's 127.0.0.1:5434 is the RDS; unreachable is not local). Tests run on one shared Postgres database per session (a copy of a template built by `alembic upgrade head`), its rows reset by DELETE (and sequences restarted, the migrations' seed rows put back) before each test; the autouse fixture sets `FFL_DB_URL` to it and nothing reaches any other database. `FFL_TEST_PG` is the admin URL tests create their databases through (default `postgresql+psycopg:///postgres`, the local socket as you; CI: its postgres:16 service; `scripts/pg-test-setup.sh` starts the local server and makes the `ffl_test_admin` role). `ffl.db pgcopy` loads the old SQLite file into a database at head. A worktree makes its own database with `scripts/devdb.sh` (a dump of the main one, a live DB, or empty + `alembic upgrade head`); a dev API there needs nothing more.
- **Box scores** (`espn_season.box_scores(lg, week, *, ref)`; the caches and tables are per league): a week before the league's `first_week` (a mid-season draft: `espn:950458410` starts at 8) has no box scores and answers `[]` with no ESPN or DB read (settings unreadable: taken as 1), so `_roster_games` fills those form weeks from `_gap_games`, and `correct_boxes(lg, ref=)` runs `first_week..current-1`; a finished week is read from the `box_scores` table (ESPN only when it has no `final` row: one read after the week finished; the current week's last, maybe mid-game, read is not); the current
  week ESPN-first, a row under 60 s old (off `fetched_at`) counting as the hit and the row serving when ESPN fails; a league move
  (`forget_current_boxes` → `db.expire_live_boxes`, non-final rows only) expires it. The tick's `correct_boxes` (`api.correction_pass`) re-reads each unsettled finished week hourly
  (a short answer, fewer matchups or a stored player missing, keeps the stored doc unsettled), logs changed points to `box_score_corrections`, plays a starter's correction into his week's feed (match `correction`; a finished
  week's board reads its feed from the `plays` table) and expires the pages built on it. `expire_everything` and the pregame pull keep finished weeks.
- **Season reads take `ref`** (`espn_season`, `journal`: every page builder, `fresh_league`, `box_scores`, `transactions_state`, `sync_*`; a required keyword `ref`): the League, my team, the box/gap/rank caches and the db rows are the league's own; every league queues its own pushes (4.8: `notify.flush` plans each league's queue apart, names the league after the title once two are registered (`league_label`), opens it with `?league=` (`league_url`) and tags it `<key>:<tag>`, every league alike; quiet hours (the user's `prefs` row, `notify.quiet(user_id)`) and devices are the user's in every league, the switches the member's `notify_prefs` in each league (0029 moved home's there, 0030 to the member); `/notify/config` and `/notify/prefs` follow `?league=`; the news desk queues each league's own (`pipeline._notify_league`, its own `news_to_notify` queue and owners)) and sees no fake lineup store (4.5); `journal.ON_NEW_ROWS` listeners fire for every league (`ref=`, each dropping and rewarming its own league's pages).
- **Per-user data** (ffl-soc.6.2.3, migration 0030 filed every existing row under the lowest user id and their team; a database with personal rows and no user refuses the upgrade): one user never sees another's rows (`tests/test_two_users.py`, the two-user fixture in `tests/two_users.py`). Personal rows carry `user_id` (decisions, watchlist, watch_signals, sits, notices, push_subscriptions; state in `user_state`: prefs (quiet hours), news_mutes, player_presets, read by `db.get_state(key, user_id=)`); what ESPN keeps per team carries `team_id` and co-managers share it (claim_plans, seats, `team_state`). Every personal db function scopes by its `ref`'s `user_id`/`team_id` (`db._member`/`_team_of`: a ref with none raises); routes with no league take `auth.current_user` (`/notify/subscribe`, `/unsubscribe`, `/test`, `/news/mute`). The flush sends each user's notices to that user's devices on their quiet hours and their switches; a league move is queued for every member (`leagues.members(key)`; `leagues.memberships()` every membership). The jobs' personal work runs per member: injuries off one mRoster read to each member's team (`notify.check_injuries`), waivers per team (`_team_waivers`, one pending read per team, the result to each member on it), the watch pass per member (`api.watch_members`, the tick's `watch` keyed by user id), news pushes per member off one owners read (`pipeline._notify_member`: their team, mutes, watchlist). Notices about the app itself go only to `FFL_OPERATOR_EMAILS` (each under their default league; unset: nobody). `test_no_implicit_user_on_the_server` keeps the API's modules off `leagues.acting()`/`FFL_USER` (outside their `main`) and `leagues.oldest()` (bar NFL-wide player reads).
- **Memberships** (ffl-soc.6.2.2 A; `memberships` table, `leagues.for_user`/`membership`/`default_for`/`join`): a user's place in a league (team, default, their own name, strategy). Migration 0028 filed every league under the lowest user id; a sign-in never makes one (`uv run python -m ffl.leagues join <key> --user <email> --team <id> [--default]`). A LeagueRef is a member's view (`user_id`, `is_default`, `owner`); `leagues.all_leagues/get` are each league through its owner (lowest user id), oldest first, which jobs use until 6.2.3; the CLI acts through `leagues.acting()` (FFL_USER's membership). No league is home (0029). `open_league` resolves the signed-in user's membership: a non-member's answer is an unknown league's (404 `unknown_league`), no `?league=` their default (404 `no_leagues` with none: the web's `/welcome`, `lib/league.leagueMissRedirect`; a stale league drops to the default). Page caches and `page_cache` rows are per (user, league) (`cache_for`); league-wide changes reach every member's (`caches_for_league`). A rename is the member's (`memberships.name`), never the leagues row.
- **The open league** (`api.open_league`, the `?league=<key>` query parameter on every league route and `POST /refresh`): none is the signed-in user's default; a job (no user) naming none on a league route is 400 `league_required`, except the jobs' routes (`POST /transactions/sync`, `/outlook/refresh`, `/notify/tick`), which then run every registered league (each answer under `leagues` by key; one league failing is its own line) and the global work once (sources, flush, final plays); an unknown key is 404 `unknown_league`. Pages cache per (user, league) (`cache_for(ref)`; there is no module cache); every league's pages carry its own live points, play feed and outlooks. `sync_all(ref=)` syncs and refreshes that league. The pro schedule (`espn_season._schedule_doc`) is the NFL's: one document for every league, `refresh_league()` drops it.
- **The league switcher** (ffl-soc.4.13, mockup A): the web's open league is the page URL's `?league=<key>` (none: the user's default). `web/proxy.ts` copies it into the `x-ffl-league` request header, which `lib/api.ts`'s server calls append to every API path; `app/api-client.ts` appends it off `window.location`; the decisions are `lib/league.ts` (`decide`, `withLeague`, `tabsFor`; tested). The start page (`/`, and the Home Screen launch: the manifest's start_url `lib/league.START_URL` = `/roster?launch=1`, so a plain `/roster` link from a push still opens home) opens the last league used (cookie `ffl-league`, written by the proxy on full page loads of league pages only (`Sec-Fetch-Dest: document`: Next hides its prefetch/RSC headers from the proxy), never NFL/News/Settings; forgotten on a home league page). An iPhone install made before this keeps `/roster` as its start: re-add it to the Home Screen. `GET /leagues` lists them (name, provider, format). Every league opens every page (4.9): `components/league-link.tsx` is the app's `Link` and `useRouter` (imported in place of Next's everywhere but `sw-register.tsx`, whose push targets name their own league), carrying the open league onto every in-app link and router move (`lib/league.ts leagueHref`: a link that names a league keeps it; `/api/`, outside and relative links untouched); the nav's tabs and the gear carry it too, the NFL and News pages mark the open league's lineups and owners, and the Matchup page `/?league=` counts as a league page for the cookie. The "FFL ▾" wordmark (`league-switcher.tsx`) switches by a **full page load**, never `router.push`: the root layout (watch stars, decision switches, `LeagueProvider`) is read once per document for its URL's league. 
- **Jobs per league** (4.7): `notify.tick` runs `league_tick(ref)` (moves, injuries, waivers, projections) for each league, then the sources' health and the flush once; `/notify/tick` adds each league's rollover pass (`rollover_pass`: ESPN's period off the pending read against the `espn_week` state; when it moves on, the league's pages are dropped and rebuilt behind as after a move, since expired ones showed last week on their first open), correction pass, watch pass and pregame pull (`_tick_steps`), the final plays once (each league's `final_plays_done`). Outlooks: one `outlook.LOCK` run walks every league in turn (`outlook.start_leagues`, a league's cards read when the run reaches it; `outlook.last(ref)` its counts), one-off writes keyed by (league, player). On the box these are the systemd timers (`deploy/box/systemd/`).
- **Season UI** (`web/`, Next 16 + shadcn; `ffl/api.py`, FastAPI on 127.0.0.1:8300): pages are server
  components fetching the API through a `/api/*` rewrite; `ffl/season.py` is pure (lineup-optimal,
  waiver deltas, `add` — formula in the league's strategy, "Waivers" (`uv run python -m ffl.strategy show`)), `ffl/espn_season.py` is the I/O. Production
  runs on the box (see Config) at https://ffl.whitebridgelabs.com: Caddy :443 -> ffl-web :3300 -> ffl-api :8300. Dev servers on the Mac use :3301 for the UI; 3000/8000 are other projects'.
- **Players page** (`/players`, was Waivers; `/waivers` redirects with its query): `GET /players?preset=&pos=&fa=&cols=&f=&sort=&q=`.
  `espn_season.free_agent_pool` returns `positions` (free agents only, `POOL_SIZE` deep per position: claims,
  the waiver outlook and the calibration snapshot read it, so it must never carry a rostered player) and
  `rostered` (every rostered player through the same `waiver_deltas`, "worth to me", tagged `owner`/`mine`;
  my own rows carry no add or deltas). `fa=1` (default) serves `positions[pos]`, `fa=0` adds `rostered[pos]`,
  and `q` searches both halves at every position whatever `fa` says; `season.rank_players` keeps one row per
  player (the rostered one wins) by add, None last. The Free agents toggle rides in the URL as `fa=1`/`fa=0` when it differs from the preset; an Availability filter naming theirs or mine reads the rostered half whatever `fa` says. A view (ffl-8iz, `ffl/player_views.py`, pure) is a preset (`player_presets` global state key, the same views in every league: `{presets, default}`, seeded "Waivers"; `GET/POST /players/presets`, `POST /players/presets/{id}`, `/{id}/delete`, `/default`) with the URL's overrides laid over; no params opens the default, an unknown preset id opens the default. Filters run server-side over the whole pool (a missing figure fails a numeric filter; a search ignores them); `pos=ALL` spans every position and `pos=FLEX` RB/WR/TE (`GROUPS`), each capped to 150 by the view's sort when unfiltered (`capped`). The payload carries `view`, `presets`, `default`; COLUMNS mirrors `web/lib/player-columns.ts`.
- **Playoff weeks** (ffl-p2z): `settings["playoff_weeks"]` (`settings.playoff_weeks`: the NFL weeks of every matchup period
  after `matchupPeriodCount`, off ESPN's `scheduleSettings`; never hard-coded) rides on `/league`, `/nfl` and the player log
  as `playoff_weeks`. A divider marks them, never a per-week chip: `WeekSelect`'s list groups them under a Playoffs label,
  separated from the weeks either side, and a "Playoffs" label stands beside the trigger on one; the player log sets them
  under a Playoffs row and rules them off from the week after (`PlayoffsRow`/`PlayoffsEnd`). The League picker runs
  through the last of them.
  A settings.json without the key flags nothing and the League picker stops at the regular season: re-run
  `uv run python -m ffl.settings` on the main checkout after deploying it.
- A League scoreboard tile opens that game on the Matchup board at `/league/<week>/<away team id>`
  (`GET /week/{n}?team=`, `week_state(team=)`). The payload's `me`/`opp` are the board's left/right sides:
  I stand left of my own game (`mine: true`), any other game reads away then home. No play feed off my game;
  future weeks have no lineups, so their tiles are not links.
- A fantasy team's name (standings, the Matchup scorebug, Transactions sides) opens its roster as a
  plain list at `/team/<id>` (`GET /team/{id}`, `espn_season.team_state`: that side of the week's box
  scores, grouped like the board, with the club and its rank; live points through `live.overlay_side`).
- **Push notifications** (`ffl/notify.py`): detectors (new transaction rows, my starters' injuries, waiver-run results) →
  `notices` outbox (`key` = dedupe) → `/notify/tick` flush (quiet hours, moves batched, 24 h drop; one tick at a time, 409
  otherwise) → pywebpush → `web/public/sw.js`; `/settings` (the nav's gear) holds the device and quiet window, every league's; `/settings/league` (the gear beside each league in the switcher, `?league=`) holds that league's name (`POST /leagues/name`, `leagues.rename`: ESPN's until renamed, kept when `ffl.settings` re-runs), push switches and Decisions switches. The web app shows no roster caps and no strategy (ffl-srn: the public release has no draft tool; caps are `ffl.leagues caps`, CLI only). The message
  is Declarative Web Push (`notify.payload_for`: `web_push` 8030, `notification.navigate` absolute off `FFL_APP_URL`):
  Safari shows and opens it without the worker, since iOS often never fires `notificationclick` for a backgrounded
  Home Screen app (WebKit bug 268797, ffl-e9x.10); the legacy fields beside it feed sw.js's push handler elsewhere.
  The tap path is traced on-device (`POST /trace` → the `traces` table, rows from sw.js and `SwRegister`).
  After the tick, `api.pregame_pull`: once per kickoff time (`season.pregame_due`, inside `PREGAME_PULL_MIN` = 85 min,
  after the inactives are out; done ones in the `pregame_pulls` state) it expires every page and source cache and rebuilds
  on a thread. Refresh keeps a source entry fetched with a long TTL (FantasySharks' far weeks): re-scraping 14 weeks
  is what took the old nav button past 30 s.
- **News desk** (`ffl/newsdesk/`, beads ffl-0gv): `pipeline.run` is seed (`seed.SOURCES`) → index (`players.fetch_index`) →
  `collect.run_all` then `collect.recheck` (in that order: recheck's "still in the feed" is the poll just made) →
  `media.run_all` (transcripts split per player) → `tag.run` (Jev: effect ▲/▼/●, kind, story) → `write.run` (Haiku through
  `claude -p`: headline, overview, a line per player it moves ▲/▼; one it only names gets no line and no place on his news page) → notices → `health.notices`, each stage tried alone (the index's
  stages skipped without one), the stage dict in state `news_last_run`. `POST /news/run` (202; 409 while `_NEWS` is held)
  runs it on a thread and flushes after if `_NOTIFY` is free; `ffl-news.timer` POSTs it every 10 min;
  `GET /news/status` is the sources' health and the last run. A push (`notices.for_story`, kind `news`, the `news` pref)
  is a written revision's ▼ on my roster or ▲ on a free agent at `NEWS_PUSH_PROB`, with a line, unflagged, not muted
  (`news_mutes`; a K or D/ST only as a ▼ on mine for an injury or a release read off the line, `QUIET_POS`/`RELEASED`), key `news:<id>:<rev>`; at most one per player and kind a day (`notices.recent`, off the notices table
  and the stories' rows) unless an earlier rev of the story pushed (a material update). A correction or retraction pushes
  once (`news:<id>:<status>`), only after the original did. Owners come from `pipeline.owners()` (also read at request
  time); when it fails nothing is queued and the revisions wait in state `news_to_notify` for the next run.
- **Watchlist** (beads ffl-x0h; `ffl/watch.py`, pure): players I do not have, watched for the moment one gets his chance.
  Tables `watchlist` (a row is a watch; unwatching deletes it) and `watch_signals` (one row per `key`, kind
  ahead/usage/news/bench/available, kept on unwatch). Detectors: `ahead_signals` (a teammate who has stood above him at his
  position on Sleeper's chart is Doubtful, Out or IR; a teammate ESPN's feed lists is read there alone, the chart's own
  status only for one it lacks; Questionable never),
  `usage_signal` (newest game vs the mean of his form window's earlier games: snaps +`SNAP_JUMP` 15, targets +3 for
  RB/WR/TE, red-zone looks +2), `bench_signal` (`delta_roster` crossing from ≤0 to >0, naming my weakest at the position),
  `available_signal` (owned at the last pass, free now), `news_signal` (a ▲ row at prob ≥ `NEWS_PROB` 0.5 with a
  reason, unflagged). `notice_for` is a player's one push a day (key `watch:<id>:<day>`, kind `watch`, URL `/players?watch=1`);
  `page` groups rows into moving (a signal in `MOVING_DAYS` 3, newest first), quiet (by add) and elsewhere (rostered),
  each with a chip per detector kind lit within the window, plus an `available` chip once one has fired. The pool (`free_agent_pool`) carries `watched`: every watched player
  as a Players row, one deeper than the lists read by id (`_watched_rows`, `kona_player_info` + `_gap_games`), never added
  to `positions`. `api.watch_pass` runs inside `/notify/tick` at most hourly (`watch_state`: `last_pass`, `above`,
  `bench`, `owner`; a player's first pass only baselines bench and owner; the baselines are pruned to `db.watched_ids()`
  on every pass and every unwatch, never to the rows a pass priced): every detector over every watched player, signals
  all stored. It reads the previous pool build (`_serve`, up to an hour old) for prices and signals, but a fresh
  `pipeline.owners()` read gates its pushes: a push is queued only for one with no owner in both, one the fresh read
  makes mine is unwatched, and a failed read queues nothing (never the stale owner). Nobody watched: it skips the build. The news hook (`newsdesk/pipeline.py`): Rotowire's ids
  include the watched (`collect._rotowire_ids`); a watched player's ▲ on a story within `NEWS_PUSH_MAX_AGE` is a news
  signal; its watch push is skipped when that run's news push already carries him, he is rostered, or muted. Routes: `GET /watch` (the page, off the
  cached pool; a player now mine is unwatched there), `GET /watch/ids`, `POST /watch/{id}` `{watched}` (a new id the
  cached pool lacks expires it, so `/watch` lists him once it rebuilds). Pref `watch` (default on). Nothing reaches ESPN
  or `decisions`. UI: `WatchProvider`/`useWatch` (`watch-context.tsx`, seeded by the layout from `/watch/ids`) behind every
  `WatchStar` (Players rows, the player dialog's band); the Players page's Watch tab (`/players?watch=1`, after the positions; `/watch` redirects there; `watch-list.tsx`, a free agent's row carrying the claim button) follows the provider's ids, so an
  unwatch folds the row away with an undo, and an id starred in this browser session that `/watch` lacks stands under
  JUST ADDED while the page polls (8 s, 15 asks, none while hidden). `/watch` answers `ids` (after its unwatch of
  mine) and the page hands them to the provider's `sync`, so a server-side unwatch leaves the page.
- **Transactions page** (`/transactions`, `GET /transactions`): `season.group_transactions` (pure) turns
  the `transactions` table's rows into moves. A waiver run stamps every claim with one timestamp across teams, so a
  move is (timestamp, team); a trade is every row of a timestamp holding a TRADE_* row, one side per team.
  The log is kept current three ways (`journal.sync_transactions`; the table's `(season, id)` key dedupes, so concurrent syncs need no lock): every `/transactions` build syncs
  first; every fresh current-week `espn_season.box_scores` fetch (every page's rosters come through it) syncs
  when ESPN's owners disagree with the draft replayed through the log (`reconcile_log`, once per distinct
  disagreement); and `ffl-transactions.timer`
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
  table for each player's position (`fastcast.scoring_table`: the raw scoring items, every lineup slot's pointsOverrides kept, by the checkpoint's `positionId`; settings without raw items score the flat `scoring` list; 1,017 home player-weeks match ESPN exactly; every registered league on its own table off the one socket, a `live.Lane` each: home's points are `STATE.points`, another's `STATE.points_for(key)`/`week_for(key)`, its starters, week and play store its lane's, `api._lane(ref)`; a league before its first week pages a week the feed has not reached, so it is not overlaid) and lays the points over the cached
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
  A change that scored no new play (no count moved: `plays.revision`) is `match: "reversed"` (labelled Called back, like a
  play called back before it scored: a count fell, or yards fell on a flagged play) or `"adjusted"` (Stat fix: only
  yards moved, the ball re-spotted, either way; or a catch re-ruled a run, yards kept, `plays.reruled`, and its passer's lost completion, told from an
  incompletion by the teammate in the same frame, `plays.threw_reruled`); ESPN's post-week rows (`"correction"`) are labelled Correction. Rows from before stats were read are
  retagged by `plays.old_revisions` (text only, so narrow: ≤0.5 pts within 30 s of the play's first row), run by
  `ffl.playbook backfill`.
  Every NFL play is stored once in `nfl_plays` (`ffl/playbook.py`; FastCast's as the feed sees them, ESPN's core
  drives' over them, written by the NFL tab's game page for the live week and by the tick's `final_plays` for each
  game 30 min after it is final). A play keeps every wording with when it was first seen (`texts`,
  `playbook.versioned`); a feed row on a stored play keeps no text of its own and shows, by `play_id`, the wording in
  force when it happened, a revision row (Called back, Stat fix) the newest (`playbook.wording`); a row
  older than every stored wording keeps the text it was written with. A play
  reading NULLIFIED / No Play that names a starter (a D/ST: its club on defence) gets a `called_back` row (play,
  player; `match: "called_back"`, delta 0, `would_be` read off the text before the flag less `kept`), rewritten in
  `Store.record` whenever the play or his rows on it change, dropped when the play stands after all.
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
- **Win probability** (`season.win_prob`, pure; the league's strategy, "Lineup" (`uv run python -m ffl.strategy show`): context, never an input to the call):
  each side's starters as posted points plus projection × the game left (`season.game_left`: the feed's quarter
  and clock, else ESPN's game_played; a starter with points and no clock carries the rest of his projection),
  variance σ_pos² × left, P = Φ(margin/σ). σ per position from the latest `sigma_fits` row
  (`uv run python -m ffl.calibrate winprob`, which prints the pregame Brier too; `calibrate.load_sigma`), else
  `season.SIGMA_FALLBACK`. The Week payload's `win_prob` and each League row's (over the side's `starters`, the
  board's merged players, whose sum is now the tile's `proj`) are re-priced by `live.overlay_week`/`overlay_league`
  on every push. UI: `web/components/win-meter.tsx` on Base UI's Meter.
- **Roster page** (`/roster`): `season.roster_cards` is the start/bench call (the league's strategy, "Lineup" (`uv run python -m ffl.strategy show`)), pure; a START
  carries `call_slot`, the slot `optimal_lineup` places him in (`_place_by_kickoff`: same starters, latest kickoffs in
  flex, a started game kept in place), and the chip names it;
  `ffl/outlook.py` writes labelled bullets under each card (Injury, Usage, Depth, Team, Take; each cites
  dated, linked sources; never projections) by running `claude -p` (Sonnet, no tools: ~12 s a card) from a
  fact sheet alone: the card's non-projection facts, per-game usage rows (`form["rows"]`) and `ffl/news.py`'s
  context — the news desk's stories naming him from the last ten days, its only news (`news.fetch_desk` over
  `db.player_stories`: the desk's write-up, his ▲/▼/● line, each live source's site, date and link; the desk
  polls Rotowire itself), Sleeper's depth order (one bulk dump), the teammates in scope by position with their
  injury status, plus every hurt teammate at his own position wherever the chart puts him (ESPN's injury feed
  drops IR players) with that teammate's newest three stories, where return dates are written (ids off one
  `kona_player_info` call). Twenty calls at a time (`WORKERS`, the whole roster), each card saved as it lands, cached in
  the `outlooks` table (kind `week`) under a hash of the facts, the stories (id, rev, status, sources) and the day (so at least daily).
  GETs never generate. `POST /outlook/refresh` starts a locked background run; `ffl-outlook.timer`
  curls it at five past every hour. One card is rewritten on a click (the card footer's Rewrite):
  `POST /outlook/{id}` → `outlook.start_one(kind=ROSTER)`, stored with the fact hash so the hourly run skips
  him; it and the full run refuse each other (409), since a full run started before the click would write its own older card over the newer single one. A free agent's outlook is written only on a click (the Players row's
  "outlook" dialog): `POST /waivers/outlook/{id}` runs `outlook.start_one` (same sheet with `free_agent=True`:
  no call, a claim-framed Take; WORKERS in flight at most) into `outlooks` with kind `waivers-week`, apart
  from the Roster's; the dialog polls `GET /waivers/outlook/{id}`. The CLI must be logged in for the service user (`ffl` on the box) (`claude -p` from a plain
  terminal answers; from inside a Claude Code session it says "Not logged in").
  The page groups rows by ESPN's slot (lineup order, then bench, IR) with the call as chips beside it (a ring when
  ESPN's lineup and the call disagree); rows are compact, detail expands, and the name opens the shared player
  dialog (Form on `ros_adj`). `espn_season.with_espn_slots` takes each card's slot and lock from a fresh mRoster
  read (`roster_flags`), never the cached box score; a failed read keeps the box slot, unlocked.
  Writes (`/lineup`, `/roster/sit`, `/claims*`) follow `?league=` (one lock per league, `api._lock`; the fake lineup store, ESPN URL/team and decision rows are the league's), as do the outlook routes.
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
- Commit and push the branch (`git push`) at the end of every session; the GitHub repo is the backup. Work lands through
  pull requests: `develop` is the default branch (squash-merge PRs only, linear history), `main` is what production runs
  (merge-commit PRs from develop only). Neither can be deleted or force-pushed (rulesets, checked weekly by the
  `verify-branch-protection` workflow), and the `test` workflow (pytest, web build, typecheck) runs on every PR.
- **Landing a worktree branch on develop — only after Jonathan explicitly says to land that branch** (asking whether
  to land is fine; a finished task is not permission). From the worktree: `git rebase origin/develop`;
  `git push -u origin <branch>`; `gh pr create --base develop --fill` (title: what the branch does; the body ends with
  the commit trailers' attribution and the Claude Code line); `gh pr checks --watch` (right after the create it can say
  no checks reported: wait a few seconds and retry); `gh pr merge --squash --delete-branch` (a local branch-delete error
  after a successful merge is fine: confirm with `gh pr view --json state`); then `scripts/stop-test-servers.sh` stops the
  branch's test servers (every listener whose cwd is inside the worktree, with its `tailscale serve` entry; it refuses
  the main checkout, and `--dry-run` only prints).
- **Promoting develop to main (a deploy) — only after Jonathan explicitly says to promote.** `gh pr create --base main
  --head develop --fill`, `gh pr checks --watch`, `gh pr merge --merge`. The push to main is the deploy (below). It runs
  the box's own `/opt/ffl/release.sh` and units, never the release's: a change to `deploy/box/release.sh` or
  `deploy/box/systemd/` is installed on the box before the promotion (re-run `setup.sh`, or `install` release.sh to
  `/opt/ffl/release.sh` and the changed units to `/etc/systemd/system` + `systemctl daemon-reload`). Afterwards the main checkout pulls it
  (`cd ~/Developer/FFL && git pull`) only to keep the Mac's checkout current (tunnel, draft tools); it deploys nothing. A worktree session is refused git
  against the main checkout, so that pull goes through the FFL workspace's Herdr tab labelled `Terminal` (a plain shell
  in `~/Developer/FFL`; pane IDs change across Herdr restarts: `herdr workspace list` / `herdr tab list --workspace <id>` /
  `herdr pane list --workspace <id>`, one plain command each with literal IDs): check it is at its prompt
  (`herdr pane read <pane> --lines 3`), `herdr pane run <pane> "git pull"`, read the pane.
  Without his go-ahead, or outside Herdr, hand him the `cd ~/Developer/FFL && git pull` line.
  The deploy is the Actions run on the push to main (repo variable `DEPLOY_TARGET=box`; `.github/workflows/deploy.yml`:
  test.yml on the commit, web built on the runner, the tarball to `s3://ffl-backups/releases/`, OIDC role `ffl-deploy`,
  SSM document `ffl-release` runs `/opt/ffl/release.sh <sha>` on the box): watch it
  (`gh run list --workflow deploy.yml --branch main -L 1`, then `gh run watch <id> --exit-status`) and read
  `deployed <sha>` in its last step. Its configuration is repo variables and the `production` environment, no
  secrets (the workflow's header).
- Never commit `.env` or anything containing `espn_s2`/`SWID`.
- Issues live in GitHub Issues (`gh issue list`, `gh issue view <n>`, `gh issue create`); beads was retired 2026-10-28 (old ids like `ffl-0gv` are on the issues labelled `from-beads`, each body opening with its id: `gh issue list --state all --search '"ffl-0gv" in:body'`; epics hold their children as sub-issues, `blocks` became blocked-by links). Labels: type (`bug`, `enhancement`, `task`, `epic`, `spike`), `P0`-`P4`, `in progress`, `deferred`; the org's Priority issue field mirrors the P label (Urgent / High / Medium / Low for P0 / P1 / P2 / P3-P4), set with `gh api repos/White-Bridge-Labs/FFL/issues/<n>/issue-field-values -X POST --input -` on `{"issue_field_values":[{"field_id":34754994,"value":"Medium"}]}`. Specs, plans, designs and notes go in an issue's body or comments, never in .md files (docs/superpowers/specs, plans, ...), whatever a skill says.
- Never merge a worktree branch's PR into develop, and never promote develop to main (a deploy), without Jonathan's explicit go-ahead for that branch or promotion. Asking whether to land is fine; a finished task is not permission.
- Never make roster moves (lineup, sit, claims, adds) on Jonathan's behalf in the home league: he makes every call; the app advises. A real ESPN write only when he asks for that specific one. Exception (Jonathan 2026-10-25, "Do the moves yourself"): the public league `espn:950458410` during ffl-soc.4.11's full-week run: Claude makes its moves through the app, by the league's strategy, each recorded in decisions.
- Before any irreversible real action whose request is being captured (ESPN roster move, etc.), prove the listener records a harmless write of the same kind first (e.g. a watch-list toggle on/off via `ffl.netcapture`) and see it in the log. No proof, no Confirm.
- Production and its recovery must not rely on the MacBook (Jonathan 2026-10-26): recovery stays in AWS (EC2 auto-recovery, rehearsed rebuild from Terraform + S3/RDS). The Mac is a dev machine and draft-tool client only.
- Never hand Jonathan a recurring manual step (weekly command, periodic rerun): schedule it as a launchd agent in `launchd/` (added to `scripts/install.sh`), install and kickstart it, and confirm the log.
- Multi-league: every league behaves identically (outlooks, pushes, jobs, pages); never make a feature league-specific unless Jonathan says so.
- Every UI change in `web/` (even a small one, even inside a backend task) goes through the impeccable skill first: load it, run its context, read craft-floor before editing, check phone width (2026-10-25: the league switcher shipped beside the tabs on one row with no room on a phone).
- A migration's test upgrades to its own revision (never "head") and reads raw SQL, not the app's readers (they need head). Only the newest migration's test and the production-shaped round trip (`tests/test_per_user_migration.py`) go to head: tests pinned to head broke on every later migration touching their tables (2026-10-27).
- Weekly results go to the `results` table (`journal sync` / the hourly `/transactions/sync`); `journal/results/` is no longer written. Standings are read live from ESPN.
- UI (`web/`): prebuilt, tested components over hand-rolled ones. Use a shadcn component from `web/components/ui`
  when one fits, and add a missing one with `pnpm dlx shadcn add <name>` (style base-nova, on `@base-ui/react`)
  rather than writing it. If shadcn has nothing that fits, use a well-tested library (Base UI primitives,
  TanStack Table, Recharts, date-fns, ...). Hand-roll only when neither fits, and say why in the commit.

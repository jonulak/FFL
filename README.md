# FFL

A self-hosted companion for ESPN fantasy football leagues. It reads your leagues with your own ESPN login. Each person
on an install has their own login; a household or a few league-mates can share one.

- **Matchup**: live scoring off ESPN's feed, every scoring play as it lands, and a win probability.
- **Roster**: a start/sit call per player, one-tap lineup swaps, and a written outlook (injury, usage, depth) per player.
- **Players**: free agents and rostered players priced on rest-of-season projections from five sources, with filters
  and saved views; claims and free adds from the table.
- **News**: stories grouped per player from beat writers, team sites, press conferences and podcasts (the clubs'
  own shows, and with the optional transcriber every Locked On show and the national fantasy shows), each marked up
  or down.
- **Push notifications**: injuries to your starters, waiver results, league moves, news on your players.
- **Decisions**: every roster move recorded with its reason, so the season can be reviewed.
- **Draft tools** (command line): a live draft assistant and practice-draft simulator.

It runs as four Docker containers (Postgres, the API, the web app, a cron jobs container) on your own machine. The
outlooks and news write-ups use `claude -p` with your Claude subscription token or an Anthropic API key.

You need an ESPN account in the leagues, a Claude subscription or API key, and a machine with Docker and about 2 GB of
memory (about 5 GB and a few CPU cores with the optional podcast transcriber, below).

## Install

Needs Docker with Compose, `curl` and `openssl`.

```
curl -fsSL https://raw.githubusercontent.com/jonulak/FFL/main/install.sh | sh
```

It makes `~/ffl` (or `$FFL_HOME`), downloads `docker-compose.yml`, writes `.env` with generated secrets, starts the
stack and prints the address: `http://localhost:3300`. Re-running it keeps your `.env`.

Keep a copy of `.env`. `FFL_SECRET_KEYS` seals the stored ESPN logins and the app's settings (the Claude token, push
keys, the YouTube key); a new one opens none of them.

## First run

Open the address. The setup wizard asks for:

1. **Your account**: a username and password. The first account is the admin, and the address you opened becomes
   the app's address (Settings › App changes it).
2. **A Claude token**: run `claude setup-token` and paste the result (an Anthropic API key works too).
3. **Optional services**: an email push services can reach you at, and a YouTube API key for faster press
   conferences in the news. Skip either; the card in Settings › App walks you through it later.
4. **ESPN**: connect your ESPN login, with the browser extension or by pasting the `espn_s2` and `SWID` cookies.
5. **Leagues**: pick the leagues to follow.

Every step but the account can be skipped, and every setting it asks for stays in Settings › App.

Add other people in Settings › App › Users: each invite is a one-time link (7 days) they open to set a username and
password.

## Upgrade

Run the installer again:

```
curl -fsSL https://raw.githubusercontent.com/jonulak/FFL/main/install.sh | sh
```

It keeps your `.env`, fetches the current `docker-compose.yml`, pulls the images and restarts what changed (set
`FFL_HOME` first if the install is not in `~/ffl`). Don't upgrade with `docker compose pull` alone: it keeps the old
`docker-compose.yml`, and a new release may need a new one (a new service, a new database image).

An upgrade replaces the containers, never the volumes: your settings, logins and history live in the database (the
`pgdata` volume) and stay put. Never run `docker compose down -v`, which deletes the volumes and everything in them.

The API migrates the database on start; a failed migration shows only in `docker compose logs api` (the api restarts
until it succeeds). `FFL_VERSION` in `.env` pins an image tag instead of `latest`, and the installer then fetches that release's compose file. It re-fetches
`docker-compose.yml`, so put local changes in a `docker-compose.override.yml` beside it.

## Configuration

An admin sets the app in **Settings › App**: the app's address, the push contact email, the Claude token and the
YouTube API key. They're stored (sealed) in the database. Push keys are made there too, the first time the API starts.

`~/ffl/.env` holds only what the stack needs before it reaches the database; run `docker compose up -d` after a
change. `install.sh` fills the secrets.

| Setting | What it does |
|---|---|
| `FFL_PORT` | The host port the web app listens on (3300). |
| `TZ` | The timezone for the jobs' schedule and quiet hours. |
| `FFL_VERSION` | The image tag to run (`latest`). |
| `FFL_SECRET_KEYS`, `FFL_JOB_TOKEN`, `POSTGRES_PASSWORD` | Generated secrets. Do not change them on a running install. |

Advanced, unset by default (add a line to `.env` to use one):

| Setting | What it does |
|---|---|
| `FFL_SOURCES_OFF` | Projection or news sources to skip, comma separated. Some refuse cloud hosts: on a VPS, `sharks,youtube_captions`. |
| `FFL_OUTLOOK_WORKERS` | Outlooks written at once (20). Use 3 on a 2 GB machine. |

## Podcast transcription (optional)

The news desk reads the clubs' official podcasts because their feeds carry transcripts. The shows the week's analysis
is argued on (Locked On, one a day per club; Fantasy Footballers, Fantasy Football Today, Establish The Run, the
Ringer, PFF and the rest) ship none, so an optional `stt` container transcribes them on your own machine with NVIDIA's
Parakeet model: nothing leaves the machine, and no key is needed. It costs about 3 GB of memory and, on a desktop
CPU, a minute or two per half-hour episode (about 30 hours of audio a day across every show; measured 2026-11-04 on
an M-series Mac in Docker: 84 s for a 32-minute show, 2.7 GB resident).

Turn it on with a line in `~/ffl/.env`, then restart the stack:

```
COMPOSE_PROFILES=stt
```

```
docker compose up -d
```

Or install with it from the start: `curl -fsSL ... | FFL_STT=1 sh`. `docker compose logs stt` shows the model loading
and `docker compose logs api` each episode as the news desk sends it (one per news run, newest first). Without the
container the shows are still polled, and their episodes wait; nothing fails.

On a Mac, Docker runs the model on the CPU of its Linux VM, with no access to the Apple silicon's accelerators. A
native process (for example `parakeet-mlx`) serving the same contract (`POST /transcribe {url}` with the `x-ffl-job`
header, answering `{ok, segs: [{start, text}]}`, see `ffl/stt.py`) is a drop-in: point the api at it with
`FFL_STT_URL` in a `docker-compose.override.yml` and leave the profile off.

## Troubleshooting

- **The page does not load:** `docker compose ps` shows which container is down, and `docker compose logs api` shows why. A failed database migration appears there.
- **No outlooks or news write-ups:** Settings › App › Claude says whether a token is stored. Without one, `docker compose logs api` shows `no Claude token`. `runner unreachable` means the `runner` container is down (`docker compose logs runner`).
- **Leagues stop updating:** your ESPN login expired. Sign in to ESPN again with the extension installed, or paste fresh cookies in Settings › ESPN connection.
- **No push notifications:** push needs https (see HTTPS), the app's address in Settings › App set to that https
  address, and a device turned on in Settings. `docker compose logs api` shows each failed push with the push
  service's reason.
- **Jobs not running:** `docker compose logs jobs` shows each scheduled run and its answer.
- **Podcasts without a transcript are not in the news:** the transcriber is off (`COMPOSE_PROFILES=stt` in `.env`, then `docker compose up -d`) or down (`docker compose logs stt`). `GET /news/status` (signed in as an admin) shows the last run's stages: `transcribe` says `off` while the news desk cannot reach it.

## Backups

The jobs container dumps the database every night at 03:30 into the `backups` volume, keeping 14. To restore the
newest one (this replaces the database's contents):

```
docker compose stop api jobs
docker compose exec db sh -c 'dropdb -U ffl --force ffl && createdb -U ffl ffl'
docker compose run --rm --no-deps api sh -c 'uv run --no-sync python -m ffl.db restore /backups --url "$FFL_DB_URL" --yes'
docker compose up -d
```

To copy dumps off the machine: `docker compose cp jobs:/backups ./backups`.

## HTTPS

The stack serves plain http on port 3300. Push notifications and secure cookies need https, so put a reverse proxy
with a certificate in front of it. With [Caddy](https://caddyserver.com):

```
ffl.example.com {
	reverse_proxy localhost:3300
}
```

Then open `https://ffl.example.com` and set it as the app's address in Settings › App. (Sign up there the first
time and it's set for you.)

## Remote install

Postgres is not published on the host: only the web port is. The draft tools (`ffl.advise`, `ffl.draft_watch`, ...)
run from a checkout on your own computer and need the database, so publish it on the server's loopback with a
`docker-compose.override.yml` beside the compose file (Compose merges it; the installer never touches it), then
`docker compose up -d`:

```
services:
  db:
    ports:
      - "127.0.0.1:5432:5432"
```

Reach it through ssh (local port 5434, so a Postgres of your own on 5432 is left alone):

```
ssh -N -L 5434:127.0.0.1:5432 you@your-server
```

Put the connection in a file outside the checkout, say `~/.config/ffl/remote.env` (mode 600), with
`POSTGRES_PASSWORD` and `FFL_SECRET_KEYS` from the server's `.env`:

```
FFL_DB_URL=postgresql+psycopg://ffl:<POSTGRES_PASSWORD>@127.0.0.1:5434/ffl
FFL_SECRET_KEYS=<the server's>
FFL_USER=<your username>
```

Then `uv run --env-file ~/.config/ffl/remote.env python -m ffl.avail WR 5`.

## The browser extension

`extension/ffl` sends your ESPN login to the app whenever you are signed in to ESPN, so it never goes stale. One build
serves every server: open your FFL in a tab, click the extension's icon, then Connect (or pair with a code for a
server you type).

```
cd extension/ffl && pnpm install && pnpm build
```

Load `dist/chrome` as an unpacked extension (chrome://extensions, Developer mode). Without it, paste the cookies in
the wizard instead.

## Moving an existing install

From another FFL database (such as the old hosted one) to a new stack:

1. Stop the old API and its jobs, then dump the old database: `pg_dump -Fc -f ffl.dump <its postgres URL>` (or its
   last nightly backup). The URL is a plain libpq one, `postgresql://user:password@host:5432/ffl`, not the app's
   `postgresql+psycopg://` form.
2. Install the stack on the new machine (above), copy `ffl.dump` into `~/ffl`, then replace its empty database with
   the dump:

   ```
   cd ~/ffl
   docker compose stop api jobs
   docker compose exec db sh -c 'dropdb -U ffl --force ffl && createdb -U ffl ffl'
   docker compose run --rm --no-deps -v "$PWD/ffl.dump:/tmp/ffl.dump:ro" api sh -c 'uv run --no-sync python -m ffl.db restore /tmp/ffl.dump --url "$FFL_DB_URL" --yes'
   ```

3. Copy the old `FFL_SECRET_KEYS` into the new `.env`, so the stored ESPN logins and app settings open.
4. `docker compose up -d` (the API migrates the restored database), then open `/setup` right away: with existing
   users and no passwords, it makes the lowest user id the admin. Give the others set-password links from
   Settings › App › Users (New link).
5. Point DNS and the reverse proxy at the new machine and check the app's address in Settings › App (see HTTPS).

The old `TYPESAFE_API_KEY` and the Google and Apple sign-in secrets are no longer used and can be deleted.

## Development

Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm, and a local Postgres 16 or newer (the
scripts expect Homebrew's `postgresql@18`).

```
uv sync
scripts/pg-test-setup.sh          # starts the local Postgres, makes the test role
scripts/devdb.sh                  # a database for this checkout; writes FFL_DB_URL and a dev key ring into .env
uv run uvicorn ffl.api:app --port 8300
cd web && pnpm install && pnpm dev   # http://localhost:3301
uv run pytest -q
```

`CLAUDE.md` is the full guide to the code. Issues and pull requests are welcome; PRs go to `develop`.

### Publishing images

A push to `main` (or a `v*` tag) builds `ghcr.io/jonulak/ffl-api` and `ffl-web`
(`.github/workflows/images.yml`). Packages pushed with `GITHUB_TOKEN` start private: after the first publish, make
both public once (each package's Package settings › Change visibility), or installs cannot pull them.

## License

MIT, see [LICENSE](LICENSE).

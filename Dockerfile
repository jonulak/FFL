# The API image; the jobs container runs it too, with `crond -f -L /dev/stdout` as its command.
FROM python:3.12-slim

# Node 22 for the claude CLI (claude -p writes outlooks and news), pg_dump/pg_restore 18 (restores any dump up to 18, dumps the postgres:16 server; the distro ships
# another major) for `ffl.db backup`/`restore`, busybox for crond, curl for the cron jobs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg busybox \
 && . /etc/os-release \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt $VERSION_CODENAME-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs postgresql-client-18 \
 && rm -rf /var/lib/apt/lists/*

# Pinned, in its own layer: a release's layers keep their digests (images.yml caches them), so an upgrade downloads
# only what changed; bump this to take a new claude.
ARG CLAUDE_CODE_VERSION=2.1.316
RUN npm i -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} && npm cache clean --force

# Debian's busybox reads crontabs from /var/spool/cron/crontabs; point it at /etc/crontabs like Alpine's
RUN ln -s /bin/busybox /usr/local/bin/crond \
 && mkdir -p /etc/crontabs /var/spool/cron /backups \
 && ln -s /etc/crontabs /var/spool/cron/crontabs

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache
COPY ffl ffl
COPY migrations migrations
COPY alembic.ini ./
COPY scripts scripts
COPY deploy deploy
COPY deploy/crontab /etc/crontabs/root

EXPOSE 8300
CMD ["/app/deploy/entrypoint.sh"]

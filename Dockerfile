# The API image; the jobs container runs it too, with `crond -f -L /dev/stdout` as its command.
FROM python:3.12-slim

# No agent CLI here: `claude -p` runs in the runner image (Dockerfile.runner). pg_dump/pg_restore 18 (restores any dump up to 18, dumps the postgres:16 server; the distro ships
# another major) for `ffl.db backup`/`restore`, busybox for crond, curl for the cron jobs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg busybox \
 && . /etc/os-release \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt $VERSION_CODENAME-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-18 \
 && rm -rf /var/lib/apt/lists/*

# Debian's busybox reads crontabs from /var/spool/cron/crontabs; point it at /etc/crontabs like Alpine's
RUN ln -s /bin/busybox /usr/local/bin/crond \
 && mkdir -p /etc/crontabs /var/spool/cron /backups \
 && ln -s /etc/crontabs /var/spool/cron/crontabs

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
# glibc gives every thread its own malloc arena: the API's RSS ran ~2x its heap without this (922 MB vs ~440 MB)
ENV MALLOC_ARENA_MAX=2

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

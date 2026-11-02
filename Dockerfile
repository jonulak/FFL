# The API image; the jobs container runs it too, with `crond -f -L /dev/stdout` as its command.
# Bases pinned by digest (Dependabot bumps them): a floating tag's upstream rebuild would rebuild every layer, and every
# install's upgrade would pull the whole image again instead of the layers that changed.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

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

COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache
# The news search's embedding model (ffl/embed.py's MODEL; tests/test_embed.py holds them equal), baked in: production
# never downloads it. Named here rather than read from the code, so no code edit rebuilds this 68 MB layer.
ENV FASTEMBED_CACHE_PATH=/app/models
RUN uv run --no-sync python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
ENV HF_HUB_OFFLINE=1
COPY ffl ffl
COPY migrations migrations
COPY alembic.ini ./
COPY scripts scripts
COPY deploy deploy
COPY deploy/crontab /etc/crontabs/root

EXPOSE 8300
CMD ["/app/deploy/entrypoint.sh"]

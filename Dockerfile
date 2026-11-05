# The API image; the jobs container runs it too, with `crond -f -L /dev/stdout` as its command.
# Bases pinned by digest (Dependabot bumps them): a floating tag's upstream rebuild would rebuild every layer, and every
# install's upgrade would pull the whole image again instead of the layers that changed.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
# images.yml sets it (a constant) so the build is reproducible: in the RUNs' env, Python compiles hash-checked .pycs
ARG SOURCE_DATE_EPOCH

# No agent CLI here: `claude -p` runs in the runner image (Dockerfile.runner). pg_dump/pg_restore 18 (restores any dump up to 18, dumps the postgres:16 server; the distro ships
# another major) for `ffl.db backup`/`restore`, busybox for crond, curl for the cron jobs.
# shortcut: apt takes the current packages, so this layer changes when Debian or pgdg ship an update (wanted: security
# fixes); pin a snapshot.debian.org date if that churn ever matters.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg busybox \
 && . /etc/os-release \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt $VERSION_CODENAME-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-18 \
 && rm -rf /var/lib/apt/lists/* /var/log/apt/* /var/log/dpkg.log /var/log/alternatives.log /var/cache/ldconfig/aux-cache  # the logs and ld cache differ on every build

# Debian's busybox reads crontabs from /var/spool/cron/crontabs; point it at /etc/crontabs like Alpine's
RUN ln -s /bin/busybox /usr/local/bin/crond \
 && mkdir -p /etc/crontabs /var/spool/cron /backups \
 && ln -s /etc/crontabs /var/spool/cron/crontabs

COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Bytecode from one process in sorted order (-j1), not uv's parallel compile: a .pyc's bytes depend on what its process
# compiled before, so parallel workers make a different layer each build. /tmp holds a randomly named uv lock.
RUN uv sync --frozen --no-dev --no-cache \
 && python -m compileall -q -j1 --invalidation-mode checked-hash .venv \
 && find /tmp -mindepth 1 -delete
# The news search's embedding model (ffl/embed.py's MODEL; tests/test_embed.py holds them equal), baked in: production
# never downloads it. Named here rather than read from the code, so no code edit rebuilds this 68 MB layer. The cleanup
# drops onnxruntime's telemetry device ID, Hugging Face's download logs and a /tmp session file, all new each build.
ENV FASTEMBED_CACHE_PATH=/app/models
RUN uv run --no-sync python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
 && rm -rf /root/.cache && find /tmp -mindepth 1 -delete
ENV HF_HUB_OFFLINE=1
COPY ffl ffl
COPY migrations migrations
COPY alembic.ini ./
COPY scripts scripts
COPY deploy deploy
COPY deploy/crontab /etc/crontabs/root

# The commit this image is (GET /app/version). Last, and an ENV writes no layer: an upgrade still pulls only what changed.
ARG FFL_COMMIT
ENV FFL_COMMIT=$FFL_COMMIT

EXPOSE 8300
CMD ["/app/deploy/entrypoint.sh"]

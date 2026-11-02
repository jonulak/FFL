#!/bin/sh
# FFL in one line: curl -fsSL https://raw.githubusercontent.com/jonulak/FFL/main/install.sh | sh
# Puts docker-compose.yml and a filled .env in $FFL_HOME (default ~/ffl) and starts the stack. Safe to re-run: .env is kept.
# Re-running it is also the upgrade: a bare `docker compose pull` keeps the old docker-compose.yml, which a new image
# may not run against (a new service, a new db image).
set -eu

fetch() {  # fetch <repo path> <dest>
  case $BASE in
    file://*) cp "${BASE#file://}/$1" "$2" ;;
    *) curl -fsSL "$BASE/$1" -o "$2" ;;
  esac
}

set_env() {  # set_env FILE KEY VALUE: KEY=VALUE in FILE (values carry no '|')
  sed "s|^$2=.*|$2=$3|" "$1" > "$1.tmp" && mv "$1.tmp" "$1"
}

free_port() {  # free_port KEY: move the port KEY names in .env up past anything listening on it
  # shortcut: no nc on the host means no probe (compose fails as before); add another probe if that bites.
  command -v nc >/dev/null 2>&1 || return 0
  old=$(sed -n "s/^$1=//p" .env)
  port=$old
  tries=0
  while nc -z 127.0.0.1 "$port" >/dev/null 2>&1 && [ $tries -lt 50 ]; do port=$((port + 1)); tries=$((tries + 1)); done
  if [ "$port" != "$old" ]; then
    set_env .env "$1" "$port"
    echo "port $old ($1) is in use; using $port" >&2
  fi
}

# Everything runs from main, called on the last line: a download cut short runs nothing.
main() {
  { command -v docker && docker compose version; } >/dev/null 2>&1 || {
    echo "FFL runs in Docker with the compose plugin, which is not installed: https://docs.docker.com/get-docker/" >&2
    exit 2
  }
  command -v openssl >/dev/null 2>&1 || { echo "openssl is needed to make the secrets: install it and re-run" >&2; exit 2; }

  FFL_HOME=${FFL_HOME:-$HOME/ffl}
  umask 077  # .env holds the secrets
  mkdir -p "$FFL_HOME"
  cd "$FFL_HOME"
  # A pinned FFL_VERSION (an image tag such as v1.2.0, which is also its git tag) takes that release's compose file
  version=$( [ -f .env ] && sed -n 's/^FFL_VERSION=//p' .env || true)
  case ${version:-latest} in
    latest) ref=main ;;
    *) ref=$version ;;
  esac
  BASE=${FFL_INSTALL_BASE:-https://raw.githubusercontent.com/jonulak/FFL/$ref}
  fetch docker-compose.yml docker-compose.yml

  if [ ! -f .env ]; then
    # Bare assignments, so a failing openssl stops the script (inside an argument it would not).
    ring=$(openssl rand -base64 32)
    job=$(openssl rand -hex 32)
    pg=$(openssl rand -hex 32)
    fetch deploy/env.example .env.new  # filled, then moved in: a run cut short leaves no half-filled .env
    set_env .env.new FFL_SECRET_KEYS "k1:$ring"
    set_env .env.new FFL_JOB_TOKEN "$job"
    set_env .env.new POSTGRES_PASSWORD "$pg"
    mv .env.new .env
  fi

  docker compose pull

  # Our own running stack holds its port: only probe before the first start (or after one that failed to bind).
  if [ -z "$(docker compose ps -q --status running)" ]; then
    free_port FFL_PORT
  fi
  docker compose up -d

  FFL_PORT=$(sed -n 's/^FFL_PORT=//p' .env)
  echo "Open http://localhost:${FFL_PORT:-3300}"
}

main "$@"

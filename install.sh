#!/bin/sh
# FFL in one line: curl -fsSL https://raw.githubusercontent.com/White-Bridge-Labs/FFL/main/install.sh | sh
# Puts docker-compose.yml and a filled .env in $FFL_HOME (default ~/ffl) and starts the stack. Safe to re-run: .env is kept.
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

# Everything runs from main, called on the last line: a download cut short runs nothing.
main() {
  { command -v docker && docker compose version; } >/dev/null 2>&1 || {
    echo "FFL runs in Docker with the compose plugin, which is not installed: https://docs.docker.com/get-docker/" >&2
    exit 2
  }
  command -v openssl >/dev/null 2>&1 || { echo "openssl is needed to make the secrets: install it and re-run" >&2; exit 2; }

  FFL_HOME=${FFL_HOME:-$HOME/ffl}
  BASE=${FFL_INSTALL_BASE:-https://raw.githubusercontent.com/White-Bridge-Labs/FFL/main}

  umask 077  # .env holds the secrets
  mkdir -p "$FFL_HOME"
  cd "$FFL_HOME"
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
    set_env .env.new VAPID_SUBJECT mailto:admin@localhost
    mv .env.new .env
  fi

  docker compose pull

  # Apart from the block above so a run that stopped before the keys fills them on the next.
  if grep -q '^VAPID_PUBLIC_KEY=$' .env; then
    keys=$(docker compose run --rm -T --no-deps api uv run --no-sync python -m ffl.notify keys)
    pub=$(printf '%s\n' "$keys" | sed -n 's/^VAPID_PUBLIC_KEY=//p')
    priv=$(printf '%s\n' "$keys" | sed -n 's/^VAPID_PRIVATE_KEY=//p')
    [ -n "$pub" ] && [ -n "$priv" ] || { echo "ffl.notify keys printed no VAPID keys; re-run to try again" >&2; exit 1; }
    set_env .env VAPID_PUBLIC_KEY "$pub"
    set_env .env VAPID_PRIVATE_KEY "$priv"
  fi

  docker compose up -d

  FFL_PORT=$(sed -n 's/^FFL_PORT=//p' .env)
  echo "Open http://localhost:${FFL_PORT:-3300}"
}

main "$@"

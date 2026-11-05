#!/bin/sh
# FFL in one line: curl -fsSL https://raw.githubusercontent.com/jonulak/FFL/main/install.sh | sh
# Puts docker-compose.yml and a filled .env in $FFL_HOME (default ~/ffl) and starts the stack. Safe to re-run: .env is kept.
# Re-running it is also the upgrade: a bare `docker compose pull` keeps the old docker-compose.yml, which a new image
# may not run against (a new service, a new db image). `... | sh -s -- --auto-on` upgrades by itself, `--auto-off` stops.
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

start() {  # The images this upgrade replaced, now untagged, go only after a healthy start: not worth failing it over.
  docker compose up -d --wait --wait-timeout 600 || {
    echo "FFL did not start healthy; see: docker compose logs api" >&2
    exit 1
  }
  docker image prune -f >/dev/null || echo "could not remove old images; try: docker image prune -f" >&2
}

# One scheduled check. Quiet unless it upgrades; a job's run of nothing to do exits 0.
auto() {
  cd "${FFL_HOME:-$HOME/ffl}" 2>/dev/null && [ -f .env ] || exit 0
  case $(sed -n 's/^FFL_VERSION=//p' .env) in ""|latest) ;; *) exit 0 ;; esac
  # A stack someone stopped stays stopped; so does one Docker is not up for.
  [ -n "$(docker compose ps -q --status running 2>/dev/null)" ] || exit 0
  commit=$(curl -fsSL --max-time 20 "https://api.github.com/repos/jonulak/FFL/git/ref/tags/release" \
    | sed -n 's/.*"sha": *"\([0-9a-f]\{40\}\)".*/\1/p')
  [ -n "$commit" ] || exit 0
  [ "$commit" != "$(cat .upgrade-last 2>/dev/null || true)" ] || exit 0
  # Wait out live games. An API that cannot answer does not hold: a new release may be its fix.
  # shellcheck disable=SC2016  # $FFL_JOB_TOKEN is the container's
  if docker compose exec -T api sh -c 'curl -fsS --max-time 10 -H "x-ffl-job: $FFL_JOB_TOKEN" http://127.0.0.1:8300/upgrade/hold' 2>/dev/null \
    | grep -q '"hold":true'; then
    exit 0
  fi
  # cron can start a run on top of a slow one; a lock this old was left by a killed run.
  find .upgrade-lock -maxdepth 0 -mmin +60 -exec rmdir {} \; 2>/dev/null || true
  mkdir .upgrade-lock 2>/dev/null || exit 0
  trap 'rmdir .upgrade-lock' EXIT
  echo "$(date): upgrading from $(cat .upgrade-last 2>/dev/null || echo unknown) to $commit"
  # The commit, not the tag: raw.githubusercontent serves a moved tag's old files for minutes.
  BASE=https://raw.githubusercontent.com/jonulak/FFL/$commit
  fetch docker-compose.yml docker-compose.yml
  docker compose pull
  # In jobs, the api's image: it is up even while a bad release has the api restarting.
  docker compose exec -T jobs uv run --no-sync python -m ffl.db backup /backups/pre-upgrade
  # Marked before the start: a release that will not start is not retried until a newer one, each try restarts the app.
  echo "$commit" > .upgrade-last
  start
  echo "$(date): upgraded to $commit"
}

# What the schedule runs: the released script, so the upgrader upgrades with the app.
AUTO_CMD="curl -fsSL https://raw.githubusercontent.com/jonulak/FFL/release/install.sh | sh -s -- --auto"
AUTO_LABEL=com.jonulak.ffl.auto-upgrade

auto_off() {
  if [ "$(uname)" = Darwin ]; then
    launchctl bootout "gui/$(id -u)/$AUTO_LABEL" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$AUTO_LABEL.plist"
  else
    { crontab -l 2>/dev/null | grep -v "# $AUTO_LABEL" || true; } | crontab -
  fi
}

auto_on() {
  home=${FFL_HOME:-$HOME/ffl}
  [ -f "$home/.env" ] || { echo "no install in $home: install FFL first, or set FFL_HOME" >&2; exit 2; }
  auto_off
  # A scheduled job has neither this shell's PATH (docker and its credential helper) nor FFL_HOME, so both are kept.
  if [ "$(uname)" = Darwin ]; then
    plist=$HOME/Library/LaunchAgents/$AUTO_LABEL.plist
    mkdir -p "$(dirname "$plist")"
    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$AUTO_LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string><string>$AUTO_CMD</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATH</string>
    <key>FFL_HOME</key><string>$home</string>
  </dict>
  <key>StartInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>$home/upgrade.log</string>
  <key>StandardErrorPath</key><string>$home/upgrade.log</string>
</dict></plist>
EOF
    launchctl bootstrap "gui/$(id -u)" "$plist"
  else
    { crontab -l 2>/dev/null || true
      echo "*/5 * * * * export PATH='$PATH' FFL_HOME='$home'; { $AUTO_CMD; } >> '$home/upgrade.log' 2>&1 # $AUTO_LABEL"
    } | crontab -
  fi
  echo "Automatic upgrades are on: every 5 minutes, waiting out live games. A log is kept in $home/upgrade.log"
}

# Everything runs from main, called on the last line: a download cut short runs nothing.
main() {
  case ${1:-} in
    --auto) auto; exit 0 ;;
    --auto-on) auto_on; exit 0 ;;
    --auto-off) auto_off; echo "Automatic upgrades are off."; exit 0 ;;
  esac
  { command -v docker && docker compose version; } >/dev/null 2>&1 || {
    echo "FFL runs in Docker with the compose plugin, which is not installed: https://docs.docker.com/get-docker/" >&2
    exit 2
  }
  command -v openssl >/dev/null 2>&1 || { echo "openssl is needed to make the secrets: install it and re-run" >&2; exit 2; }

  FFL_HOME=${FFL_HOME:-$HOME/ffl}
  umask 077  # .env holds the secrets
  mkdir -p "$FFL_HOME"
  cd "$FFL_HOME"
  # A pinned FFL_VERSION (an image tag such as v1.2.0, which is also its git tag) takes that release's compose file;
  # else `release`, the tag images.yml moves once all four images are out (main's file can be ahead of them).
  version=$( [ -f .env ] && sed -n 's/^FFL_VERSION=//p' .env || true)
  case ${version:-latest} in
    latest) ref=release ;;
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
  start

  FFL_PORT=$(sed -n 's/^FFL_PORT=//p' .env)
  echo "Open http://localhost:${FFL_PORT:-3300}"
}

main "$@"

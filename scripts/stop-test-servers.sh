#!/usr/bin/env bash
# Stop a worktree's test servers: every TCP listener whose cwd is inside the worktree, and the `tailscale serve`
# entry proxying to its port. Run after a branch lands (CLAUDE.md "Landing a worktree branch").
#
#   scripts/stop-test-servers.sh [--dry-run] [worktree path]    (default: the current worktree)
#
# Matched by cwd alone, so production (the main checkout, ~/Developer/FFL, which this refuses) and other
# worktrees' servers are never touched. One lsof listener selector only: a second -i ORs with the first.
set -euo pipefail

dry=0
if [ "${1:-}" = --dry-run ]; then dry=1; shift; fi
tree=$(cd "${1:-$(git rev-parse --show-toplevel)}" && pwd -P)
# the main checkout: every worktree's git dir hangs off its .git
common=$(git -C "$tree" rev-parse --path-format=absolute --git-common-dir)
main_tree=$(cd "$common/.." && pwd -P)
[ "$tree" != "$main_tree" ] || { echo "stop-test-servers: $tree is the main checkout (production runs here)" >&2; exit 1; }

list=$(lsof -nP -iTCP -sTCP:LISTEN -Fpn 2>/dev/null | awk '/^p/{p=substr($0,2)} /^n/{n=split($0,a,":"); print p, a[n]}' | sort -u |
  while read -r pid port; do
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | awk '/^n/{print substr($0,2)}')
    case "$cwd" in ("$tree"|"$tree"/*) echo "$pid $port" ;; esac
  done)
[ -n "$list" ] || { echo "stop-test-servers: no test servers running from $tree"; exit 0; }

while read -r pid port; do
  echo "stopping pid $pid on :$port ($tree)"
  [ "$dry" = 1 ] && continue
  kill "$pid" 2>/dev/null || true
  if tailscale serve status 2>/dev/null | grep -q "proxy http://127.0.0.1:$port\$"; then
    tailscale serve --https="$port" off >/dev/null 2>&1 || echo "stop-test-servers: tailscale serve :$port not turned off" >&2
  fi
done <<< "$list"

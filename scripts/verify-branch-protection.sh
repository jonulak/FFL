#!/usr/bin/env bash
# Proves the permanent branches' protection is still what the PR workflow assumes (run by
# .github/workflows/verify-branch-protection.yml; runnable by hand with `gh` logged in as an admin).
# Env: REPO (owner/name; default: this checkout's), BRANCHES (default "develop main").
# Exits 1 on any failed check, naming each.
set -uo pipefail
REPO=${REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}
BRANCHES=${BRANCHES:-"develop main"}
failures=0
fail() { echo "::error::$1"; failures=$((failures + 1)); }

# Layer 1: the rulesets, by name. Listing them needs admin rights; the workflow token has none, and then the
# rules that apply to each branch (readable with contents: read) are checked instead.
if rulesets=$(gh api "repos/$REPO/rulesets" --jq '.[].id' 2>&1); then
  named() { # name -> that ruleset's JSON, empty when absent
    local id
    for id in $rulesets; do
      gh api "repos/$REPO/rulesets/$id" | jq -ce --arg n "$1" 'select(.name == $n)'
    done
  }
  perm=$(named "Protect permanent branches")
  dev=$(named "Require PR (develop)")
  main=$(named "Require PR")
  if [[ -z "$perm" ]]; then
    fail 'ruleset "Protect permanent branches" is missing'
  else
    for r in deletion non_fast_forward; do
      jq -e --arg r "$r" '[.rules[].type] | index($r)' <<<"$perm" >/dev/null || fail "\"Protect permanent branches\" lacks the $r rule"
    done
    for b in develop main; do
      jq -e --arg b "refs/heads/$b" '.conditions.ref_name.include | index($b)' <<<"$perm" >/dev/null || fail "\"Protect permanent branches\" does not include $b"
    done
  fi
  if [[ -z "$dev" ]]; then
    fail 'ruleset "Require PR (develop)" is missing'
  else
    jq -e '[.rules[] | select(.type == "pull_request") | .parameters.allowed_merge_methods] == [["squash"]]' <<<"$dev" >/dev/null || fail '"Require PR (develop)" is not squash-only'
    jq -e '[.rules[].type] | index("required_linear_history")' <<<"$dev" >/dev/null || fail '"Require PR (develop)" lacks required_linear_history'
  fi
  if [[ -z "$main" ]]; then
    fail 'ruleset "Require PR" (main) is missing'
  else
    jq -e '[.rules[] | select(.type == "pull_request") | .parameters.allowed_merge_methods] == [["merge"]]' <<<"$main" >/dev/null || fail '"Require PR" is not merge-only'
  fi
  # a ruleset in "evaluate" or "disabled" enforces nothing
  for rs in "$perm" "$dev" "$main"; do
    [[ -z "$rs" ]] || jq -e '.enforcement == "active"' <<<"$rs" >/dev/null || fail "ruleset \"$(jq -r .name <<<"$rs")\" is not active (enforcement: $(jq -r .enforcement <<<"$rs"))"
  done
  echo "rulesets: checked by name"
else
  echo "::warning::rulesets not readable with this token (${rulesets:0:120}); checking the rules applied to each branch instead. Set BRANCH_PROTECTION_TOKEN (Administration: Read) for the full check."
  for b in $BRANCHES; do
    if ! rules=$(gh api "repos/$REPO/rules/branches/$b" 2>&1); then
      fail "$b: could not read applied rules: ${rules:0:200}"
      continue
    fi
    for r in deletion non_fast_forward pull_request; do
      jq -e --arg r "$r" 'map(.type) | index($r)' <<<"$rules" >/dev/null || fail "$b: no $r rule applies"
    done
    want=merge
    [[ $b == develop ]] && want=squash
    jq -e --arg m "$want" '[.[] | select(.type == "pull_request") | .parameters.allowed_merge_methods] == [[$m]]' <<<"$rules" >/dev/null || fail "$b: merge method is not $want only"
    if [[ $b == develop ]]; then
      jq -e 'map(.type) | index("required_linear_history")' <<<"$rules" >/dev/null || fail "develop: no required_linear_history rule applies"
    fi
  done
fi

# Layer 2: classic protection, an independent subsystem that survives any ruleset edit. Needs admin too.
for b in $BRANCHES; do
  if ! p=$(gh api "repos/$REPO/branches/$b/protection" 2>&1); then
    if [[ -z "${BRANCH_PROTECTION_TOKEN:-}" && "$p" == *"403"* ]]; then
      echo "::warning::$b: classic protection not readable with the workflow token (needs BRANCH_PROTECTION_TOKEN); skipped"
    else
      fail "$b: classic branch protection missing or unreadable: ${p:0:200}"
    fi
    continue
  fi
  [[ $(jq -r '.allow_deletions.enabled' <<<"$p") == false ]] || fail "$b: classic protection allows deletions"
  [[ $(jq -r '.allow_force_pushes.enabled' <<<"$p") == false ]] || fail "$b: classic protection allows force pushes"
  echo "$b: classic protection ok"
done

((failures == 0)) || { echo "::error::$failures protection check(s) failed"; exit 1; }
echo "Branch protection verified."

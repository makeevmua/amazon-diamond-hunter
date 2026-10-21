#!/usr/bin/env bash
# Durable, best-effort backup outside GitHub Actions' evictable cache.
# This branch stores only public deal observations plus dedupe metadata.
set -euo pipefail
state_branch="diamond-data"
fetch_state() {
  git fetch --quiet --no-tags origin "refs/heads/${state_branch}:refs/remotes/origin/${state_branch}"
}
restore() {
  if ! fetch_state; then
    echo "STATE: durable branch unavailable; Actions cache still supported"
    return 0
  fi
  for file in catalog.sqlite seen.json; do
    if [[ ! -s "${file}" ]]; then
      if git show "origin/${state_branch}:state/${file}" > "${file}.restored" 2>/dev/null && [[ -s "${file}.restored" ]]; then
        mv "${file}.restored" "${file}"
        echo "STATE: recovered ${file} from durable branch"
      else
        rm -f "${file}.restored"
      fi
    fi
  done
}
backup() {
  if [[ ! -s catalog.sqlite || ! -s seen.json ]]; then
    echo "STATE: no complete state snapshot to save"
    return 0
  fi
  if ! fetch_state; then
    echo "STATE: durable branch fetch failed; cache remains active"
    return 0
  fi
  # Initial snapshot, manual run, or at four UTC hour windows per day.
  hour="$(date -u +%H)"
  if [[ "${GITHUB_EVENT_NAME:-}" != "workflow_dispatch" && "${hour}" != "00" &&
        "${hour}" != "06" && "${hour}" != "12" && "${hour}" != "18" ]]; then
    if git cat-file -e "origin/${state_branch}:state/catalog.sqlite" 2>/dev/null; then
      echo "STATE: snapshot deferred; cache updated this scan"
      return 0
    fi
  fi
  worktree="$(mktemp -d)"
  cleanup() {
    git worktree remove --force "${worktree}" >/dev/null 2>&1 || true
    rmdir "${worktree}" >/dev/null 2>&1 || true
  }
  trap cleanup RETURN
  git worktree add --quiet --detach "${worktree}" "origin/${state_branch}"
  mkdir -p "${worktree}/state"
  cp catalog.sqlite "${worktree}/state/catalog.sqlite"
  cp seen.json "${worktree}/state/seen.json"
  git -C "${worktree}" config user.name "github-actions[bot]"
  git -C "${worktree}" config user.email "41898282+github-actions[bot]@users.noreply.github.com"
  git -C "${worktree}" add -f state/catalog.sqlite state/seen.json # .gitignore excludes these by basename
  if git -C "${worktree}" diff --cached --quiet; then
    echo "STATE: snapshot unchanged"
    return 0
  fi
  git -C "${worktree}" commit --quiet -m "Update Diamond Hunter deal observations"
  if git -C "${worktree}" push --quiet origin "HEAD:refs/heads/${state_branch}"; then
    echo "STATE: durable snapshot published successfully"
  else
    echo "STATE: snapshot push blocked; GitHub cache remains available"
  fi
}
case "${1:-}" in
  restore) restore ;;
  backup) backup ;;
  *) echo "Usage: bash state-store.sh restore|backup" >&2; exit 2 ;;
esac

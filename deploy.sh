#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
ok(){ printf '  [OK] %s\n' "$*"; }
info(){ printf '  [INFO] %s\n' "$*"; }
error(){ printf '  [ERROR] %s\n' "$*" >&2; }
printf 'bitx-bk deploy\n==============\n\n'
command -v git >/dev/null || { error "git is not installed."; exit 1; }
[[ -d .git ]] || { error "$ROOT is not a Git working tree."; exit 1; }
[[ -f bin/bitx-bk ]] || { error "bin/bitx-bk is missing."; exit 1; }
branch=$(git branch --show-current)
[[ -n "$branch" ]] || { error "Detached HEAD; deploy aborted."; exit 1; }
ok "Repository: $(git remote get-url origin 2>/dev/null || printf unknown)"
ok "Branch: $branch"
git config core.fileMode false
if [[ -n "$(git status --porcelain)" ]]; then
 error "Working tree contains local changes; deploy aborted."
 git status --short
 exit 1
fi
ok "Working tree clean"
old_version=$(bash bin/bitx-bk --version 2>/dev/null || printf unknown)
info "Checking for updates..."
git fetch origin "$branch"
git merge --ff-only "origin/$branch"
chmod +x bin/bitx-bk
bash -n bin/bitx-bk
ok "bash syntax check passed"
new_version=$(bash bin/bitx-bk --version)
ok "bitx-bk v$new_version"
if [[ "$old_version" == "$new_version" ]]; then
 info "Version unchanged ($new_version)."
else
 info "Version: $old_version -> $new_version"
fi
printf '\nDeploy complete.\n'

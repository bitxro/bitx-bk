#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
source <(sed '/^cmd="${1:-}"/,$d' "$root/bin/bitx-bk")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
need_root(){ :; }
restic(){ return 0; }
CONFIG="$work/config"
printf '# no repository\n' > "$CONFIG"
repository_menu > "$work/menu" 2>&1 <<'INPUT'
4

0
INPUT
[[ $(grep -c '^Backup repository$' "$work/menu") == 2 ]]
grep -q 'Configure a Restic repository first.' "$work/menu"
grep -q 'Returning to menu.' "$work/menu"
audits_menu > "$work/audits" 2>&1 <<'INPUT'
5

0
INPUT
grep -q 'Returning to menu.' "$work/audits"

printf 'RESTIC_REPOSITORY=/fixture\n' > "$CONFIG"
restic(){ return 7; }
repository_check_action > "$work/failed" 2>&1
grep -q 'exit 7' "$work/failed"
restic(){ return 0; }
repository_check_action > "$work/success" 2>&1
[[ ! -s "$work/success" ]]
[[ $- == *e* ]]
# CLI behavior must still fail when the repository is absent.
printf '# no repository\n' > "$CONFIG"
set +e
(set -e; repo_cmd check) > "$work/cli" 2>&1
rc=$?
set -e
[[ "$rc" == 1 ]]
printf 'Repository menu tests passed\n'

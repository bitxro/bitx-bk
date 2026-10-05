#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
source <(sed '/^cmd="${1:-}"/,$d' "$root/bin/bitx-bk")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
need_root(){ :; }
recovery_tools(){ return 0; }
recovery_configure(){ error 'Connection/decryption failed'; return 1; }
recovery_restore(){ return 1; }
recovery_snapshots(){ return 1; }
recovery_select(){ RECOVERY_SNAPSHOT=fixture; return 0; }
recovery_inventory(){ [[ "$RECOVERY_SNAPSHOT" == fixture ]]; return 1; }

# Run as a standalone command: a conditional would mask errexit regressions.
recovery_menu > "$work/menu" 2>&1 <<'INPUT'
6

7

1

2

3

4

5

0
INPUT
[[ $(grep -c '^Recovery / Disaster Recovery$' "$work/menu") == 8 ]]
[[ $(grep -c 'Returning to Recovery menu' "$work/menu") == 7 ]]
grep -q 'Connection/decryption failed' "$work/menu"
[[ $- == *e* ]]

# An unexpected failure must stop the action, without exiting the menu.
unexpected_failure(){ false; touch "$work/continued"; }
recovery_menu_action unexpected_failure > "$work/action" 2>&1
[[ ! -e "$work/continued" ]]
grep -q 'Returning to Recovery menu' "$work/action"
fatal_failure(){ die 'fixture error'; }
recovery_menu_action fatal_failure > "$work/fatal" 2>&1
grep -q 'fixture error' "$work/fatal"
[[ $- == *e* ]]
printf 'Recovery menu tests passed\n'

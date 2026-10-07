#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
source <(sed '/^cmd="${1:-}"/,$d' "$root/bin/bitx-bk")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cat > "$work/snapshots.json" <<'JSON'
[
 {"id":"aaaaaaaa11111111","time":"2026-10-07T02:41:40Z","hostname":"cat.bitx.ro","tags":["bitx-bk","bitx-bk-run:first","scope:system"],"paths":["/etc/bitx-bk"]},
 {"id":"bbbbbbbb22222222","time":"2026-10-07T02:43:40Z","hostname":"cat.bitx.ro","tags":["bitx-bk","bitx-bk-run:first","scope:docker","project:nginx"],"paths":["/opt/docker/nginx"]},
 {"id":"cccccccc33333333","time":"2026-10-07T03:00:40Z","hostname":"cat.bitx.ro","tags":["bitx-bk","bitx-bk-run:second","scope:system"],"paths":["/etc/secrets"]}
]
JSON
snapshots_render(){ python3 "$root/lib/snapshot_list.py" "$1" '' '' "${2:-oldest}"; }
repo_cmd(){ [[ "$1" == snapshots && "$2" == --json ]]; cat "$work/snapshots.json"; }
recovery_repo_cmd(){ cat "$work/snapshots.json"; }
need_root(){ :; }
restic_env(){ :; }
RESTIC_REPOSITORY=/fixture
snapshots_list > "$work/list"
[[ $(grep -c '^Backup:' "$work/list") == 2 ]]
grep -q '2) bbbbbbbb  |  DOCKER  |  nginx' "$work/list"
grep -q '^       /opt/docker/nginx$' "$work/list"
grep -q '3 snapshots / 2 backup groups' "$work/list"
repository_status > "$work/status"
grep -q 'backup groups' "$work/status"
recovery_snapshots > "$work/recovery"
grep -q '1) cccccccc' "$work/recovery"
recovery_select <<< '2' > "$work/select"
[[ "$RECOVERY_SNAPSHOT" == bbbbbbbb22222222 ]]
grep -q '2) bbbbbbbb' "$work/select"
printf '[]\n' | snapshots_render - > "$work/empty"
grep -q 'No snapshots found.' "$work/empty"
repo_cmd(){ return 7; }
snapshots_menu_action > "$work/failure"
grep -q 'exit 7' "$work/failure"
printf 'Snapshot formatting tests passed\n'

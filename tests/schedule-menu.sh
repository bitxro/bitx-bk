#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
source <(sed '/^cmd="${1:-}"/,$d' "$root/bin/bitx-bk")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
schedule_require(){ :; }
schedule_installed(){ :; }
schedule_unit_dir(){ printf '%s\n' "$work"; }
restic_env(){ :; }
repo_cmd(){ :; }
restic(){ :; }
systemctl(){ printf '%s\n' "$*" >> "$work/calls"; }
CONFIG='/etc/bitx-bk/test % "config.conf'
schedule_parse_times '10,12,18,00'
[[ "${SCHEDULE_TIMES[*]}" == '10:00 12:00 18:00 00:00' ]]
schedule_parse_times '9,09:00, 18:45'
[[ "${SCHEDULE_TIMES[*]}" == '09:00 18:45' ]]
for bad in '' '24' '10:60' ',10' '10,' '10,,12' '10,garbage'; do
 if schedule_parse_times "$bad"; then printf 'Accepted invalid time: %s\n' "$bad"; exit 1; fi
done
schedule_configure <<< '10,12,18,00' > "$work/output"
[[ $(grep -c '^OnCalendar=' "$work/bitx-bk.timer") == 4 ]]
for time in 10:00 12:00 18:00 00:00; do
 grep -qx "OnCalendar=\*-\*-\* $time:00" "$work/bitx-bk.timer"
 systemd-analyze calendar "*-*-* $time:00" >/dev/null
done
grep -qx 'Persistent=false' "$work/bitx-bk.timer"
grep -qx 'ExecStart=/usr/local/bin/bitx-bk backup' "$work/bitx-bk.service"
grep -Fqx 'Environment="BITX_BK_CONFIG=/etc/bitx-bk/test %% \"config.conf"' "$work/bitx-bk.service"
grep -qx 'enable --now bitx-bk.timer' "$work/calls"
mkdir "$work/verify"
sed 's#ExecStart=/usr/local/bin/bitx-bk backup#ExecStart=/bin/true#' "$work/bitx-bk.service" > "$work/verify/bitx-bk.service"
cp "$work/bitx-bk.timer" "$work/verify/bitx-bk.timer"
systemd-analyze verify "$work/verify/bitx-bk.service" "$work/verify/bitx-bk.timer"
cp "$work/bitx-bk.timer" "$work/original"
schedule_action schedule_configure <<< '24' > "$work/error" 2>&1
cmp "$work/original" "$work/bitx-bk.timer"
grep -q 'Returning to automatic backup menu' "$work/error"
schedule_configure <<< '02:30' > /dev/null
[[ $(grep -c '^OnCalendar=' "$work/bitx-bk.timer") == 1 ]]
schedule_remove > /dev/null
[[ ! -e "$work/bitx-bk.service" && ! -e "$work/bitx-bk.timer" ]]
grep -qx 'disable --now bitx-bk.timer' "$work/calls"
! grep -q '^stop bitx-bk.service' "$work/calls"
printf '# unmanaged\n' > "$work/bitx-bk.service"
schedule_action schedule_configure <<< '12' > "$work/unmanaged" 2>&1
grep -q 'unmanaged unit' "$work/unmanaged"
grep -qx '# unmanaged' "$work/bitx-bk.service"
[[ $- == *e* ]]
schedule_menu > "$work/menu" 2>&1 <<'INPUT'
1
invalid

0
INPUT
[[ $(grep -c '^Automatic backup / service$' "$work/menu") == 2 ]]
printf 'Schedule menu tests passed\n'

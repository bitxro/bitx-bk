#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
source <(sed '/^cmd="${1:-}"/,$d' "$root/bin/bitx-bk")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
need_root(){ :; }
mkdir "$work/project" "$work/networks"
touch "$work/project/compose.yaml"
cat > "$work/config.json" <<'JSON'
{"services":{"app":{"networks":{"alias":{},"proxy":{}}}},"networks":{"alias":{"external":true,"name":"pulse-proxy"},"proxy":{"external":true,"name":"proxy"},"internal":{"name":"project_internal"},"present":{"external":true,"name":"existing"}}}
JSON
touch "$work/networks/existing"
docker(){
 case "$1 ${2:-}" in
  'compose version'|'info ') return 0;;
  'compose config')
   [[ "$PWD" == "$work/project" && -z "${COMPOSE_FILE:-}" ]] || return 98
   [[ "${config_fail:-no}" == no ]] || return 1
   cat "$work/config.json";;
  'network inspect') [[ -f "$work/networks/$3" ]];;
  'network create')
   [[ "$3" == --driver && "$4" == bridge ]] || return 99
   printf '%s\n' "$5" >> "$work/created"
   touch "$work/networks/$5";;
  *) printf 'Unexpected Docker command\n' >&2; return 99;;
 esac
}
export COMPOSE_FILE=/invalid/inherited.yml
recovery_docker_networks "$work/project" > "$work/cancel.log" 2>&1 <<'INPUT'
n
INPUT
[[ ! -e "$work/created" ]]
recovery_docker_networks "$work/project" > "$work/create.log" 2>&1 <<'INPUT'
y
INPUT
[[ $(cat "$work/created") == $'proxy\npulse-proxy' ]]
[[ ! -e "$work/networks/project_internal" ]]
recovery_docker_networks "$work/project" > "$work/repeat.log" 2>&1 < /dev/null
[[ $(wc -l < "$work/created") == 2 ]]
grep -q 'All external networks already exist' "$work/repeat.log"

cat > "$work/config.json" <<'JSON'
{"services":{"app":{"networks":{"static":{"ipv4_address":"172.28.0.10"}}}},"networks":{"static":{"external":true,"name":"static"}}}
JSON
if recovery_docker_networks "$work/project" > "$work/static.log" 2>&1 <<< y; then exit 1; fi
[[ ! -e "$work/networks/static" ]]
grep -q 'original subnet/IPAM' "$work/static.log"
config_fail=yes
if recovery_docker_networks "$work/project" > "$work/invalid.log" 2>&1 <<< y; then exit 1; fi
grep -q 'Compose configuration invalid' "$work/invalid.log"
[[ $(wc -l < "$work/created") == 2 ]]
printf 'Recovery network tests passed\n'

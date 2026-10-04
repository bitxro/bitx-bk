#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
# Load definitions only; never dispatch a live command.
source <(sed '/^cmd="${1:-}"/,$d' "$root/bin/bitx-bk")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
RECOVERY_SNAPSHOT=fixture
recovery_repo_cmd(){
 [[ "$1" == dump ]] || return 99
 case "$3" in
  */packages.txt) printf 'docker-ce\t99.0\nphp99-cli\t99.0\n';;
  */os-release) printf 'PRETTY_NAME="fixture"\n';;
  */uname.txt) printf 'Linux fixture aarch64\n';;
  *) return 1;;
 esac
}
recovery_requirements /opt/docker/app > "$work/docker" 2>&1
grep -q 'docker-ce: source=99.0' "$work/docker"
grep -q 'File recovery does not require' "$work/docker"
! grep -q 'php99-cli: source' "$work/docker"
recovery_requirements /var/lib/bitx-bk/staging/current/virtualmin > "$work/virtualmin" 2>&1
grep -q 'php99-cli: source=99.0' "$work/virtualmin"
grep -q 'separate manual step' "$work/virtualmin"
recovery_repo_cmd(){ return 1; }
recovery_requirements /etc/wireguard > "$work/old" 2>&1
grep -q 'UNKNOWN: source packages.txt inventory absent' "$work/old"
grep -q 'resource-specific runtime requirements' "$work/old"
# Required-tool failure stops restore before repository selection.
recovery_tools(){ return 1; }
recovery_select(){ touch "$work/selected"; }
if recovery_restore; then exit 1; fi
[[ ! -e "$work/selected" ]]
printf 'Recovery preflight tests passed\n'

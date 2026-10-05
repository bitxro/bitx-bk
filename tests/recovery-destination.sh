#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
source <(sed '/^cmd="${1:-}"/,$d' "$root/bin/bitx-bk")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fixture_resource="$work/original/docker/nginx"
recovery_tools(){ return 0; }
recovery_select(){ RECOVERY_SNAPSHOT=fixture; return 0; }
recovery_requirements(){ :; }
recovery_repo_cmd(){
 case "$1" in
  ls) python3 - "$fixture_resource" <<'PY'
import json, sys
print(json.dumps({'struct_type':'snapshot', 'paths':[sys.argv[1]]}))
PY
      ;;
  restore)
   [[ "$3" == --target && "$5" == --include && "$6" == "$fixture_resource" && "$7" == --verify ]]
   mkdir -p "$4$fixture_resource"
   printf 'fixture\n' > "$4$fixture_resource/.env"
   chmod 640 "$4$fixture_resource/.env"
   ln -s .env "$4$fixture_resource/link"
   [[ "${fail_restore:-no}" == no ]] || return 1
   if [[ "${collision:-no}" == yes ]]; then
    mkdir -p "$fixture_resource"
    printf 'existing\n' > "$fixture_resource/keep"
   fi
   ;;
  *) return 99;;
 esac
}
# Default destination: missing parents, hidden files, permissions and symlinks.
recovery_restore > "$work/original.log" 2>&1 <<'INPUT'
1

RESTORE
INPUT
[[ $(cat "$fixture_resource/.env") == fixture ]]
[[ $(stat -c %a "$fixture_resource/.env") == 640 ]]
[[ $(readlink "$fixture_resource/link") == .env ]]
grep -Fq "Restore verified: $fixture_resource" "$work/original.log"

# Existing destinations are refused before restoring.
if recovery_restore > "$work/exists.log" 2>&1 <<'INPUT'
1
1
INPUT
then exit 1; fi
grep -q 'Original destination already exists' "$work/exists.log"
[[ $(cat "$fixture_resource/.env") == fixture ]]

# New-directory mode keeps the previous hierarchy behavior.
recovery_restore > "$work/new.log" 2>&1 <<INPUT
1
2
$work/new
RESTORE
INPUT
[[ $(cat "$work/new$fixture_resource/.env") == fixture ]]

# Failed verification must not publish to the original path.
fixture_resource="$work/failure/nginx"; fail_restore=yes
if recovery_restore > "$work/fail.log" 2>&1 <<'INPUT'
1
1
RESTORE
INPUT
then exit 1; fi
[[ ! -e "$fixture_resource" ]]
grep -q 'partial files kept' "$work/fail.log"

# A destination appearing during restore must not be overwritten.
fixture_resource="$work/collision/nginx"; fail_restore=no; collision=yes
if recovery_restore > "$work/collision.log" 2>&1 <<'INPUT'
1
1
RESTORE
INPUT
then exit 1; fi
[[ $(cat "$fixture_resource/keep") == existing && ! -e "$fixture_resource/.env" ]]
grep -q 'verified files kept' "$work/collision.log"

# A symlink in the destination path is refused.
mkdir "$work/real-parent"
ln -s "$work/real-parent" "$work/alias"
fixture_resource="$work/alias/nginx"
if recovery_restore > "$work/symlink.log" 2>&1 <<'INPUT'
1
1
INPUT
then exit 1; fi
[[ ! -e "$work/real-parent/nginx" ]]
grep -q 'must not traverse symlinks' "$work/symlink.log"
printf 'Recovery destination tests passed\n'

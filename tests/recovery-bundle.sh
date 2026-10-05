#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export BUNDLE_FIXTURE="$work"
mkdir -p "$work/bin" "$work/fixture"
python3 - "$work" <<'PY'
import json,sys
from pathlib import Path
w=Path(sys.argv[1]); project=str(w/'live/bitx-media'); volume=str(w/'old-root/volumes/media/_data')
manifest='/var/lib/bitx-bk/staging/current/docker/project-data/1.json'
data={'project':project,'sources':[project,volume],'volumes':{'media':{'Name':'media','Driver':'local','Options':None,'Mountpoint':volume,'Labels':{}}}}
(w/'manifest.json').write_text(json.dumps(data))
(w/'listing').write_text(json.dumps({'struct_type':'snapshot','paths':[project,volume,manifest]})+'\n')
for path in (project,volume):
 p=w/'fixture'/path.lstrip('/'); p.mkdir(parents=True); (p/'.data').write_text(path)
PY
cat > "$work/bin/docker" <<'DOCKER'
#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
w=Path(os.environ['BUNDLE_FIXTURE']); a=sys.argv[1:]
if a[0]=='info': pass
elif a[0]=='ps': pass
elif a[:2]==['volume','ls']:
 if (w/'registered').exists(): print('media')
elif a[:2]==['volume','create']:
 (w/'new-root/media/_data').mkdir(parents=True); (w/'registered').touch(); print('media')
elif a[:2]==['volume','inspect']:
 print(json.dumps([{'Name':'media','Driver':'local','Options':None,'Mountpoint':str(w/'new-root/media/_data')}]))
else: raise SystemExit('Unexpected Docker invocation: '+repr(a))
DOCKER
chmod +x "$work/bin/docker"
export PATH="$work/bin:$PATH"
source <(sed '/^cmd="${1:-}"/,$d' "$root/bin/bitx-bk" | sed "s@/var/lib/bitx-bk/recovery@$work/recovery@g")
readlink(){ printf '%s\n' "$root/bin/bitx-bk"; }
need_root(){ :; }
recovery_tools(){ return 0; }
recovery_select(){ RECOVERY_SNAPSHOT=fixture; return 0; }
recovery_repo_cmd(){
 case "$1" in
  ls) cat "$work/listing";;
  dump) cat "$work/manifest.json";;
  restore)
   [[ "$3" == --target && "$5" == --verify ]] || return 99
   [[ "${FAIL_RESTORE:-no}" == no ]] || return 1
   cp -a "$work/fixture/." "$4/";;
  *) return 99;;
 esac
}
FAIL_RESTORE=yes
if recovery_docker_restore_all > "$work/failure.log" 2>&1 <<< RESTORE; then exit 1; fi
[[ ! -e "$work/registered" && ! -e "$work/live" ]]
grep -q 'no project data deployed' "$work/failure.log"
FAIL_RESTORE=no
recovery_docker_restore_all > "$work/success.log" 2>&1 <<< RESTORE
[[ -f "$work/live/bitx-media/.data" && -f "$work/new-root/media/_data/.data" ]]
grep -q 'Complete Docker data recovery finished' "$work/success.log"
[[ -f "$work/registered" ]]
printf 'Complete Docker snapshot recovery tests passed\n'

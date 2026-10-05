#!/usr/bin/env bash
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/docker/app" "$work/volumes/db/_data" "$work/bind" "$work/bin"
printf 'persistent\n' > "$work/volumes/db/_data/data.db"
export FIXTURE_WORK="$work"
python3 - "$work" <<'PY'
import json, sys
from pathlib import Path
w = Path(sys.argv[1])
volume = {'Type':'volume','Name':'db','Source':str(w/'volumes/db/_data')}
containers = [
 {'Id':'c1','Name':'/app','Config':{'Image':'nginx','Labels':{'com.docker.compose.project.working_dir':str(w/'docker/app')}},'State':{'Running':True},'Mounts':[volume,{'Type':'bind','Source':str(w/'bind')}]},
 {'Id':'c2','Name':'/shared','Config':{'Image':'nginx','Labels':{}},'State':{'Running':True},'Mounts':[volume]}]
(w/'containers.json').write_text(json.dumps(containers))
(w/'volume.json').write_text(json.dumps([{'Name':'db','Driver':'local','Options':None,'Mountpoint':str(w/'volumes/db/_data')}]))
PY
cat > "$work/bin/docker" <<'DOCKER'
#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
w=Path(os.environ['FIXTURE_WORK']); a=sys.argv[1:]
if a[:2] == ['volume','inspect']:
 print((w/'volume.json').read_text())
elif a[0] == 'inspect':
 if a[1] == '-f':
  template=a[2]; item=next(c for c in json.loads((w/'containers.json').read_text()) if c['Id']==a[3])
  if '.Name' in template: print(item['Name'])
  elif '.Config.Image' in template: print(item['Config']['Image'])
  else: print(item['Config']['Labels'].get('com.docker.compose.project.working_dir','<no value>'))
 else: print((w/'containers.json').read_text())
elif a[0] == 'ps': print('c1\nc2')
elif a[0] in ['stop','start']:
 with (w/'actions').open('a') as f: f.write(' '.join(a)+'\n')
else: pass
DOCKER
chmod +x "$work/bin/docker"
export PATH="$work/bin:$PATH"
# Redirect all collector staging/config paths into the fixture, then load definitions.
source <(sed '/^cmd="${1:-}"/,$d' "$root/bin/bitx-bk" | sed "s@/var/lib/bitx-bk@$work/state@g; s@/etc/bitx-bk@$work/etc@g; s@/etc/secrets@$work/secrets@g")
readlink(){ printf '%s\n' "$root/bin/bitx-bk"; }
need_root(){ :; }
load_config(){ BACKUP_SYSTEM_INFO=no; BACKUP_CRON=no; BACKUP_SYSTEMD=no; BACKUP_VPN=no; BACKUP_DOCKER=yes; }
restic(){ :; }
restic_env(){ RESTIC_REPOSITORY=fixture; }
collect_local_dependencies(){ : > "$1/system/local-dependencies-resolved.txt"; }
repo_cmd(){
 [[ "$1" != backup ]] || {
  python3 - "$work/snapshots" "$@" <<'PY'
import json,sys
with open(sys.argv[1], 'a') as f: f.write(json.dumps(sys.argv[2:])+'\n')
PY
  if [[ "${FAIL_DATA:-no}" == yes && "$*" == *scope:docker* ]]; then return 1; fi
 }
 return 0
}
collect_and_backup > "$work/backup.log" 2>&1
python3 - "$work" <<'PY'
import json,sys
from pathlib import Path
w=Path(sys.argv[1]); rows=[json.loads(x) for x in (w/'snapshots').read_text().splitlines()]
projects=[r for r in rows if 'scope:docker' in r]
assert len(projects)==2, projects
app=next(r for r in projects if 'project:app' in r)
assert all(str(w/p) in app for p in ['docker/app','volumes/db/_data','bind','state/staging/current/system'])
shared=next(r for r in projects if 'project:container:c2' in r)
assert str(w/'volumes/db/_data') in shared
actions=(w/'actions').read_text().splitlines()
assert 'stop c1 c2' in actions and 'start c1 c2' in actions, actions
PY
# A failed data snapshot must still restart all stopped consumers and report failure.
FAIL_DATA=yes
if collect_and_backup > "$work/failed.log" 2>&1; then exit 1; fi
tail -n 1 "$work/actions" | grep -qx 'start c1 c2'
grep -q 'Restic backup failed' "$work/failed.log"
trap - EXIT INT TERM
rm -rf "$work"
printf 'Docker data backup tests passed\n'

#!/bin/bash
set -euo pipefail
[[ $# == 1 && $1 =~ ^[A-F0-9]{4}-[A-F0-9]{4}$ ]]
test "$(findmnt -n -o UUID --mountpoint /mnt/bitx-backup)" = "$1"

for group in bx-nas bx-windows-read bx-windows-write bitx-smb-admin; do
    getent group "$group" >/dev/null || groupadd --system "$group"
done
id bitx-restic >/dev/null 2>&1 || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin bitx-restic
if [[ ! -e /etc/samba/bitx-nas.conf ]]; then
    install -m 0644 /dev/null /etc/samba/bitx-nas.conf
fi
python3 - <<'PY'
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

path = Path("/etc/samba/smb.conf")
before = path.read_text()
prefix, found, suffix = before.partition("\n[home]\n")
if not found:
    raise SystemExit("Expected existing Samba home share.")
home, bracket, tail = suffix.partition("\n[")
home = re.sub(r"(?m)^\s*path\s*=.*$", "    path = /srv/bitx-smb-homes/%u", home)
home = re.sub(r"(?m)^\s*valid users\s*=.*$", "    valid users = bitxwindows @bx-smb @bx-nas", home)
if "path = /srv/bitx-smb-homes/%u" not in home or "valid users = bitxwindows @bx-smb @bx-nas" not in home:
    raise SystemExit("Could not identify existing home settings.")
updated = prefix + found + home + bracket + tail
sections = re.split(r"(?m)(^\[[^\]\n]+\]$)", updated)
windows = sections.index("[windows-backup]") + 1
sections[windows] = re.sub(r"(?m)^[ \t]*(?:valid users|read only|write list|access based share enum)\s*=.*\n", "", sections[windows])
sections[windows] = sections[windows].rstrip() + """
    valid users = bitxwindows @bx-smb @bx-windows-read @bx-windows-write
    read only = yes
    write list = bitxwindows @bx-smb @bx-windows-write
    access based share enum = yes

"""
updated = "".join(sections)
if "[backup-admin]" not in sections:
    updated = updated.rstrip() + """

[backup-admin]
    path = /mnt/bitx-backup/bitX-BACKUP
    valid users = @bitx-smb-admin
    read only = yes
    guest ok = no
    follow symlinks = no
    wide links = no
    access based share enum = yes
"""
include = "    include = /etc/samba/bitx-nas.conf"
if include.strip() not in {line.strip() for line in updated.splitlines()}:
    updated = updated.rstrip() + "\n" + include + "\n"
if updated != before:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        stream.write(updated)
    try:
        subprocess.run(["testparm", "-s", str(temp)], stdout=subprocess.DEVNULL, check=True)
        saved = path.with_suffix(".conf.before-nas")
        if not saved.exists():
            shutil.copy2(path, saved)
        shutil.copymode(path, temp)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
PY

cat >/etc/systemd/system/bitx-storage-guard.service <<'EOF'
[Unit]
Description=Revoke file access when the backup volume is stopped
BindsTo=mnt-bitx\x2dbackup.mount
After=mnt-bitx\x2dbackup.mount

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/true
ExecStop=/usr/bin/python3 /opt/bitx-console/broker.py revoke-storage
EnvironmentFile=/etc/bitx-console/storage.env

[Install]
WantedBy=multi-user.target
EOF
printf 'BITX_DISK_UUID=%s\n' "$1" > /etc/bitx-console/storage.env
systemctl daemon-reload
systemctl enable --now bitx-storage-guard.service

#!/bin/bash
set -euo pipefail
umask 077

if [[ $# != 3 || $EUID != 0 ]]; then
    echo "Usage as root: install.sh <listen IPv4> <allowed IPv4 CIDR> <exFAT UUID>" >&2
    exit 2
fi
address=$1
network=$2
uuid=$3
python3 - "$address" "$network" "$uuid" <<'PY'
import ipaddress
import re
import sys
a, n, uuid = sys.argv[1:]
assert ipaddress.IPv4Address(a).is_private and ipaddress.IPv4Address(a) in ipaddress.IPv4Network(n)
assert re.fullmatch(r"[A-F0-9]{4}-[A-F0-9]{4}", uuid)
PY

cd "$(dirname "$0")"
apt-get install -y --no-install-recommends python3-flask gunicorn openssl
for group in bx-sftp bx-smb; do
    getent group "$group" >/dev/null || groupadd --system "$group"
done
id bitxweb >/dev/null 2>&1 || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin bitxweb
install -d -m 0755 /opt/bitx-console /opt/bitx-console/templates /opt/bitx-console/static
install -m 0644 app.py broker.py nas.py /opt/bitx-console/
install -m 0644 templates/console.html /opt/bitx-console/templates/
install -m 0644 templates/nas.html /opt/bitx-console/templates/
install -m 0644 static/console.css /opt/bitx-console/static/
install -d -m 0700 -o bitxweb -g bitxweb /var/lib/bitx-console
install -d -m 0750 -o root -g bitxweb /etc/bitx-console
install -d -m 0700 /etc/bitx-console/restic
if [[ ! -e /var/lib/bitx-console/session.key ]]; then
    openssl rand -hex 48 > /var/lib/bitx-console/session.key
    chown bitxweb:bitxweb /var/lib/bitx-console/session.key
fi
if [[ ! -e /etc/bitx-console/https.key ]]; then
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 365 -subj /CN=bitx-backup-server -addext "subjectAltName=IP:$address" -keyout /etc/bitx-console/https.key -out /etc/bitx-console/https.crt
    chown root:bitxweb /etc/bitx-console/https.key
    chmod 0640 /etc/bitx-console/https.key
    chmod 0644 /etc/bitx-console/https.crt
fi
install -d -m 0755 /etc/ssh/authorized_keys
if ! grep -q '^# bitX managed SFTP$' /etc/ssh/sshd_config; then
    cp -an /etc/ssh/sshd_config /etc/ssh/sshd_config.before-bitx-console
    cat >> /etc/ssh/sshd_config <<'EOF'

# bitX managed SFTP
Match Group bx-sftp
    AuthorizedKeysFile /etc/ssh/authorized_keys/%u
    ChrootDirectory /srv/bitx-managed-sftp/%u
    ForceCommand internal-sftp
    DisableForwarding yes
    PermitTTY no
EOF
fi
/usr/sbin/sshd -t
bash configure-sftp-auth.sh "${BITX_HEADSCALE_CIDR:-}" "$network"

# Extend the existing share to managed identities, retaining its current accounts.
cp -an /etc/samba/smb.conf /etc/samba/smb.conf.before-bitx-console
python3 - <<'PY'
from pathlib import Path
import re
p = Path("/etc/samba/smb.conf")
before = p.read_text()
parts = re.split(r"(?m)(^\[[^\]\n]+\][^\S\n]*$)", before)
idx = parts.index("[windows-backup]") + 1
pattern = r"(?m)^([ \t]*valid users[ \t]*=[ \t]*)(.*)$"
match = re.search(pattern, parts[idx])
if not match:
    raise SystemExit("windows-backup has no explicit valid users")
if "@bx-smb" not in match[2].split():
    parts[idx] = re.sub(pattern, lambda m: m[0] + " @bx-smb", parts[idx], count=1)
updated = "".join(parts)
if "[home]" not in parts:
    updated += """
[home]
    path = /mnt/bitx-backup/bitX-BACKUP/smb-homes/%u
    valid users = bitxwindows @bx-smb
    read only = no
    guest ok = no
    follow symlinks = no
    wide links = no
"""
include = "    include = /etc/samba/bitx-managed-shares.conf"
managed = Path("/etc/samba/bitx-managed-shares.conf")
if not managed.exists():
    managed.touch(mode=0o644)
if include.strip() not in {line.strip() for line in updated.splitlines()}:
    updated = updated.rstrip() + "\n" + include + "\n"
if updated != before:
    p.write_text(updated)
PY
testparm -s >/dev/null
bash configure-nas.sh "$uuid"

cat > /etc/systemd/system/bitx-broker.service <<EOF
[Unit]
Description=bitX privileged account broker
After=local-fs.target mnt-bitx\\x2dbackup.mount

[Service]
Type=simple
Environment=BITX_DISK_UUID=$uuid
ExecStart=/usr/bin/python3 /opt/bitx-console/broker.py
RuntimeDirectory=bitx-broker
RuntimeDirectoryMode=0755
StateDirectory=bitx-broker
StateDirectoryMode=0700
Restart=on-failure
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/bitx-console.service <<EOF
[Unit]
Description=bitX Backup Server web console
Wants=network-online.target
After=network-online.target bitx-broker.service
Requires=bitx-broker.service

[Service]
Type=exec
User=bitxweb
Group=bitxweb
Environment=BITX_LAN=$network
WorkingDirectory=/opt/bitx-console
ExecStart=/usr/bin/gunicorn --workers 2 --timeout 45 --bind $address:8443 --certfile /etc/bitx-console/https.crt --keyfile /etc/bitx-console/https.key app:app
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/bitx-console
PrivateTmp=yes
RestrictAddressFamilies=AF_UNIX AF_INET
UMask=0077
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
runuser -u bitxweb -- /usr/bin/python3 /opt/bitx-console/app.py initialize
systemctl daemon-reload
systemctl reload ssh smbd
systemctl enable bitx-broker bitx-console
systemctl restart bitx-broker bitx-console

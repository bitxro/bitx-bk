#!/bin/bash
set -euo pipefail

apt-get install -y --no-install-recommends bubblewrap
cd "$(dirname "$0")"
install -m 0644 file_gateway.py /opt/bitx-console/
install -m 0644 templates/files.html /opt/bitx-console/templates/
install -m 0644 static/files.js /opt/bitx-console/static/

for service in bitx-files bitx-restore; do
    extra=
    if [[ $service == bitx-restore ]]; then extra=worker; fi
    cat >"/etc/systemd/system/$service.service" <<EOF
[Unit]
Description=bitX file and backup operation service
After=mnt-bitx\\x2dbackup.mount

[Service]
ExecStart=/usr/bin/python3 /opt/bitx-console/file_gateway.py $extra
EnvironmentFile=/etc/bitx-console/storage.env
StateDirectory=bitx-files
StateDirectoryMode=0700
RuntimeDirectory=$service
RuntimeDirectoryMode=0755
UMask=0077
MemoryMax=256M
CPUQuota=100%
TasksMax=32
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
done
if [[ -f /etc/systemd/system/bitx-console-headscale.service ]]; then
    sed -i 's/--timeout 45/--timeout 220 --threads 3/' /etc/systemd/system/bitx-console-headscale.service
fi
sed -i 's/--timeout 45/--timeout 220 --threads 3/' /etc/systemd/system/bitx-console.service
runuser -u bitxweb -- /usr/bin/python3 /opt/bitx-console/app.py initialize
systemctl daemon-reload
systemctl enable --now bitx-files bitx-restore

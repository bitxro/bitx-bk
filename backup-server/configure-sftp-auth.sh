#!/bin/bash
set -euo pipefail
umask 022

if [[ $EUID != 0 || $# -gt 1 ]]; then
    echo "Usage as root: configure-sftp-auth.sh [HEADSCALE_CIDR]" >&2
    exit 2
fi

overlay=${1:-}
for group in bx-sftp-pw bx-sftp-either; do
    getent group "$group" >/dev/null || groupadd --system "$group"
done
test -x /usr/sbin/faillock
install -d -m 0755 /var/lib/bitx-sftp-faillock

python3 - "$overlay" <<'PY'
import ipaddress
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

overlay = sys.argv[1]
if overlay and not ipaddress.IPv4Network(overlay).subnet_of(ipaddress.IPv4Network("100.64.0.0/10")):
    raise SystemExit("Headscale CIDR must be within 100.64.0.0/10")

ssh = Path("/etc/ssh/sshd_config")
pam = Path("/etc/pam.d/sshd")
auth = Path("/etc/pam.d/bitx-sftp-auth")
lockout = Path("/etc/security/bitx-sftp-faillock.conf")
current = ssh.read_text()
marker = "# bitX managed SFTP\n"
if current.count(marker) != 1:
    raise SystemExit("Expected exactly one bitX SFTP configuration block.")
original, managed = current.split(marker)
overlay_line = "AllowUsers bxsf-*@" + overlay if overlay else ""
original = re.sub(r"(?m)^AllowUsers bxsf-\*@100\.[^\n]* # bitX Headscale SFTP\n", "", original)
if overlay:
    original = overlay_line + " # bitX Headscale SFTP\n" + original
config = original + marker + """
Match Group bx-sftp-pw
    PasswordAuthentication yes
    PubkeyAuthentication no
    AuthenticationMethods password

Match Group bx-sftp-either
    PasswordAuthentication yes
    AuthenticationMethods publickey password

Match Group bx-sftp
    PasswordAuthentication no
    AuthenticationMethods publickey
    AuthorizedKeysFile /etc/ssh/authorized_keys/%u
    ChrootDirectory /srv/bitx-managed-sftp/%u
    ForceCommand internal-sftp
    DisableForwarding yes
    PermitTunnel no
    PermitTTY no
    MaxAuthTries 3
"""
if managed.splitlines() and not managed.lstrip().startswith(("Match Group bx-sftp\n", "Match Group bx-sftp-pw\n")):
    raise SystemExit("Unexpected content in managed SSH block.")
before_pam = pam.read_text()
if "# bitX managed SFTP authentication" not in before_pam and before_pam.count("@include common-auth\n") != 1:
    raise SystemExit("Unexpected SSH PAM configuration.")
after_pam = before_pam.replace("@include common-auth\n", """# bitX managed SFTP authentication
auth [success=2 default=ignore] pam_succeed_if.so quiet user notingroup bx-sftp
auth substack bitx-sftp-auth
auth [success=1 default=ignore] pam_succeed_if.so quiet user ingroup bx-sftp
auth substack common-auth
""")
stack = """auth requisite pam_faillock.so preauth conf=/etc/security/bitx-sftp-faillock.conf
auth [success=1 default=bad] pam_unix.so
auth [default=die] pam_faillock.so authfail conf=/etc/security/bitx-sftp-faillock.conf
auth sufficient pam_faillock.so authsucc conf=/etc/security/bitx-sftp-faillock.conf
auth requisite pam_deny.so
"""
tally = """dir = /var/lib/bitx-sftp-faillock
deny = 5
fail_interval = 900
unlock_time = 900
silent
"""

def publish(path, content):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        tmp = Path(handle.name)
        handle.write(content)
    tmp.chmod(0o644)
    tmp.replace(path)

with tempfile.NamedTemporaryFile(mode="w") as candidate:
    candidate.write(config)
    candidate.flush()
    subprocess.run(["/usr/sbin/sshd", "-t", "-f", candidate.name], check=True)
for path in (ssh, pam):
    saved = path.with_suffix(path.suffix + ".before-bitx-sftp-auth")
    if not saved.exists():
        shutil.copy2(path, saved)
publish(auth, stack)
publish(lockout, tally)
publish(pam, after_pam)
publish(ssh, config)
PY

/usr/sbin/sshd -t

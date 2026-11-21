#!/usr/bin/env bash
# Optional end-to-end test against a REAL OpenSSH server configured like
# bitX Backup Server (ChrootDirectory + ForceCommand internal-sftp, /shares).
# It creates a temporary system user, an sshd on 127.0.0.1 and a disposable
# Restic repository, and removes them afterwards. Opt-in, root only:
#   sudo BITX_BK_INTEGRATION=1 bash tests/sftp-nas-integration.sh
set -Eeuo pipefail
[[ "${BITX_BK_INTEGRATION:-}" == 1 ]] || { echo "SKIP sftp-nas-integration (set BITX_BK_INTEGRATION=1)"; exit 0; }
[[ $EUID -eq 0 ]] || { echo "SKIP sftp-nas-integration (root required)"; exit 0; }
for c in sshd sftp ssh ssh-keygen ssh-keyscan restic useradd; do
 command -v "$c" >/dev/null || [[ -x "/usr/sbin/$c" ]] || { echo "SKIP sftp-nas-integration ($c missing)"; exit 0; }
done
root=$(cd "$(dirname "$0")/.." && pwd)
port=$((20000 + RANDOM % 20000)); user="bxsftp$RANDOM"
srv="/srv/bitx-bk-sftp-test.$$"; work="$srv/work"; chroot="$srv/$user"
cleanup(){
 [[ -f "$work/sshd.pid" ]] && kill "$(cat "$work/sshd.pid")" 2>/dev/null || true
 ssh-keygen -R "[127.0.0.1]:$port" >/dev/null 2>&1 || true
 userdel "$user" 2>/dev/null || true
 rm -rf "$srv" "$work"
}
trap cleanup EXIT

mkdir -p "$work"; chmod 755 /srv "$srv"
useradd -M -d /home -s /usr/sbin/nologin "$user"; usermod -p '*' "$user"
mkdir -p "$chroot/home" "$chroot/shares/restic-b1" "$chroot/shares/private" "$chroot/shares/readonly"
chmod 755 "$srv" "$chroot" "$chroot/shares"; chown "$user" "$chroot/home" "$chroot/shares/restic-b1"
chmod 700 "$chroot/shares/private"; chmod 555 "$chroot/shares/readonly"
ssh-keygen -q -t ed25519 -N "" -f "$work/host_key"
ssh-keygen -q -t ed25519 -N "" -f "$work/id_ed25519"
mkdir -p "$srv/keys"; chmod 755 "$srv/keys"; install -m 644 "$work/id_ed25519.pub" "$srv/keys/authorized_keys"
cat > "$work/sshd_config" <<EOF
Port $port
ListenAddress 127.0.0.1
HostKey $work/host_key
PidFile $work/sshd.pid
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
AuthorizedKeysFile $srv/keys/authorized_keys
Subsystem sftp internal-sftp
Match User $user
  ChrootDirectory $chroot
  ForceCommand internal-sftp
  AllowTcpForwarding no
EOF
chmod 755 "$work"; mkdir -p /run/sshd
"$(command -v sshd || echo /usr/sbin/sshd)" -f "$work/sshd_config" -E "$work/sshd.log"
sleep 1
mkdir -p ~/.ssh; ssh-keyscan -p "$port" 127.0.0.1 2>/dev/null >> ~/.ssh/known_hosts

source <(sed '/^cmd="${1:-}"/,$d' "$root/bin/bitx-bk")
need_root(){ :; }
ensure_restic_password(){ [[ -s "$work/pw" ]] || printf 'integration-test\n' > "$work/pw"; chmod 600 "$work/pw"; printf '%s\n' "$work/pw"; }
SFTP_KEY="$work/id_ed25519"; CONFIG="$work/bitx-bk.conf"; : > "$CONFIG"
fail(){ echo "FAIL: $*"; cat "$work/out" 2>/dev/null; exit 1; }
run(){ printf "$1" | configure_sftp_repo > "$work/out" 2>&1; }

# The old check (remote shell) fails on this server exactly like on the NAS.
if ssh -i "$SFTP_KEY" -p "$port" -o IdentitiesOnly=yes -o BatchMode=yes "$user@127.0.0.1" true > "$work/shell" 2>&1; then fail "shell allowed"; fi
grep -q 'allows sftp connections only' "$work/shell" || fail "server is not SFTP-only"

sftp_discover_shares 127.0.0.1 "$port" "$user" > "$work/shares"
[[ "$(cat "$work/shares")" == $'/shares/readonly\n/shares/restic-b1' ]] || fail "discovery: $(cat "$work/shares")"
if sftp_write_test 127.0.0.1 "$port" "$user" /shares/readonly > "$work/out" 2>&1; then fail "read-only share writable"; fi

run "127.0.0.1\n$port\n$user\n2\n2\n1\ny\n" || fail "configure dedicated share"
grep -q 'SFTP authentication: OK' "$work/out" || fail "auth"
(source "$CONFIG"; [[ "$RESTIC_REPOSITORY" == "sftp:$user@127.0.0.1:/shares/restic-b1" ]]) || fail "saved path"
[[ -f "$chroot/shares/restic-b1/config" ]] || fail "repository not created"
[[ -z "$(find "$chroot/shares/restic-b1" -maxdepth 1 -name '.bitx-bk-write-test-*')" ]] || fail "write test leftover"

repo_cmd backup --tag bitx-bk "$root/README.md" -q
repo_cmd check > /dev/null
repo_cmd restore latest --target "$work/restore" -q
cmp "$root/README.md" "$work/restore$root/README.md" || fail "restore differs"

run "127.0.0.1\n$port\n$user\n2\n2\n1\n\n" || fail "re-run on existing repository"
grep -q 'Existing Restic repository validated' "$work/out" || fail "existing repository not detected"

mv "$srv/keys/authorized_keys" "$srv/keys/authorized_keys.off"
if run "127.0.0.1\n$port\n$user\n2\n0\n"; then fail "accepted rejected key"; fi
grep -q 'Public-key authentication failed' "$work/out" || fail "auth failure message"
echo "sftp-nas-integration: all checks passed (real sshd, ForceCommand internal-sftp, restic $(restic version | awk '{print $2}'))"

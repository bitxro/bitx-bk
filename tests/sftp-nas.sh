#!/usr/bin/env bash
# SFTP-only destinations (bitX Backup Server) without a real server: a fake
# `sftp -b` emulates a chrooted ForceCommand internal-sftp account, `ssh`
# refuses every shell command like the NAS does, and `restic` is a recorder.
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
bin="$work/bin"; mkdir -p "$bin" "$work/key"

cp "$root/tests/fixtures/fake-sftp.py" "$bin/sftp"
cat > "$bin/ssh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_SSH_LOG"
for a in "$@"; do [[ "$a" == -s ]] && { [[ -n "${FAKE_SFTP_KNOWN:-}" ]] && touch "$FAKE_SFTP_KNOWN"; exit 0; }; done
echo "This service allows sftp connections only." >&2
exit 1
EOF
cat > "$bin/ssh-copy-id" <<'EOF'
#!/usr/bin/env bash
printf 'ssh-copy-id %s\n' "$*" >> "$FAKE_SSH_LOG"
echo "This service allows sftp connections only." >&2
exit 1
EOF
cat > "$bin/ssh-keyscan" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$bin/ssh-keygen" <<'EOF'
#!/usr/bin/env bash
case "$1" in
 -y) cat "$3.pub";;
 -lf) echo "256 SHA256:fakefingerprint bitx-bk@b1 (ED25519)";;
 *) exit 1;;
esac
EOF
cat > "$bin/hostname" <<'EOF'
#!/usr/bin/env bash
echo b1
EOF
cat > "$bin/restic" <<'EOF'
#!/usr/bin/env bash
args=""; cmd=""
for a in "$@"; do
 case "$a" in sftp.args=*) args="${a#sftp.args=}";; -o|--no-lock|-q|--compact|--json) ;; *) [[ -z "$cmd" ]] && cmd="$a";; esac
done
printf '%s|%s|%s\n' "$RESTIC_REPOSITORY" "$args" "$*" >> "$FAKE_RESTIC_LOG"
dir="$FAKE_SFTP_ROOT/${RESTIC_REPOSITORY#sftp:*:}"
case "$cmd" in
 init) [[ -e "$dir/config" ]] && exit 1; mkdir -p "$dir"/{data,index,keys,snapshots}; echo "0e0e0e0e0e0e0001" > "$dir/config";;
 cat) [[ -n "${FAKE_RESTIC_BADPW:-}" ]] && { echo "Fatal: wrong password or no key found" >&2; exit 12; }
      [[ -f "$dir/config" ]] || exit 1; printf '{"version":2,"id":"%s"}\n' "$(cat "$dir/config")";;
 snapshots) [[ -f "$dir/config" ]] || exit 1; echo "[]";;
 *) exit 0;;
esac
EOF
chmod +x "$bin"/*
export PATH="$bin:$PATH" FAKE_SSH_LOG="$work/ssh.log" FAKE_RESTIC_LOG="$work/restic.log" FAKE_SFTP_LOG="$work/sftp.log"

source <(sed '/^cmd="${1:-}"/,$d' "$root/bin/bitx-bk")
trap 'echo "ERR at line $LINENO: $BASH_COMMAND" >&2' ERR
need_root(){ :; }
ensure_restic_password(){ printf 'secret\n' > "$work/restic-password"; printf '%s\n' "$work/restic-password"; }
SFTP_KEY="$work/key/id_ed25519"
printf 'PRIVATE-KEY-MATERIAL\n' > "$SFTP_KEY"; printf 'ssh-ed25519 AAAAfakepublic bitx-bk@b1\n' > "$SFTP_KEY.pub"
CONFIG="$work/bitx-bk.conf"
RECOVERY_CONFIG="$work/recovery.conf"

fail(){ echo "FAIL: $*"; echo "----- output"; cat "$work/out" 2>/dev/null; exit 1; }
expect(){ grep -qF -- "$1" "$work/out" || fail "expected output: $1"; }
reject(){ ! grep -qF -- "$1" "$work/out" || fail "unexpected output: $1"; }
setting(){ (source "$CONFIG"; eval "printf '%s' \"\${$1:-}\""); }
reset_nas(){
 rm -rf "$work/nas"; mkdir -p "$work/nas/home" "$work/nas/shares/restic-b1" "$work/nas/shares/restic-b2" "$work/nas/shares/restic-panda"
 export FAKE_SFTP_ROOT="$work/nas" FAKE_SFTP_MODE=ok FAKE_SFTP_DENY="/shares/restic-b2:/shares/restic-panda" FAKE_SFTP_READONLY="" FAKE_SFTP_KNOWN=""
 unset FAKE_SFTP_NORMDIR FAKE_RESTIC_BADPW
 : > "$FAKE_SSH_LOG"; : > "$FAKE_RESTIC_LOG"; : > "$CONFIG"
}
run(){ printf "$1" | configure_sftp_repo > "$work/out" 2>&1; }
host='bitx-backup-server.b.bain.ro'

# 1, 4, 5, 7, 11, 13: SFTP-only server, discovery, dedicated share, empty, cleanup
reset_nas
run "$host\n\nb1\n2\n\n\ny\n" || fail "dedicated share configuration"
expect "SFTP authentication: OK"
expect "1) /shares/restic-b1"
expect "2) Enter path manually"
reject "restic-b2"; reject "restic-panda"
expect "Final Restic repository: sftp:b1@$host:/shares/restic-b1"
expect "Check write permission: OK"
expect "this is a NEW destination"
[[ "$(setting RESTIC_REPOSITORY)" == "sftp:b1@$host:/shares/restic-b1" ]] || fail "repository path"
[[ "$(setting SFTP_REPOSITORY_LAYOUT)" == dedicated && "$(setting SFTP_REPOSITORY_BASE)" == /shares/restic-b1 ]] || fail "layout not stored"
[[ "$(setting RESTIC_SFTP_ARGS)" == *"-i $SFTP_KEY -p 22"*"BatchMode=yes"* ]] || fail "sftp args"
[[ ! -s "$FAKE_SSH_LOG" ]] || fail "a remote shell command was attempted: $(cat "$FAKE_SSH_LOG")"
! grep -q 'StrictHostKeyChecking=no' "$FAKE_SFTP_LOG" || fail "host key checking disabled"
grep -q 'BatchMode=yes' "$FAKE_SFTP_LOG" || fail "non-interactive auth not enforced"
ls -A "$work/nas/shares/restic-b1" | grep -q '^.bitx-bk-write-test' && fail "write test left a temporary directory"
[[ -f "$work/nas/shares/restic-b1/config" ]] || fail "repository not initialized after confirmation"

# 16: backup, snapshots, check and recovery use the chosen path
: > "$FAKE_RESTIC_LOG"
repo_cmd snapshots --json >/dev/null; repo_cmd check; repo_cmd backup --tag bitx-bk /etc/hostname
[[ $(grep -c "^sftp:b1@$host:/shares/restic-b1|-i $SFTP_KEY -p 22" "$FAKE_RESTIC_LOG") == 3 ]] || fail "repo_cmd path/args: $(cat "$FAKE_RESTIC_LOG")"
printf 'RECOVERY_REPOSITORY=%q\nRECOVERY_PASSWORD_FILE=%q\nRECOVERY_SFTP_ARGS=%q\n' "sftp:b1@$host:/shares/restic-b1" "$work/restic-password" "-i $SFTP_KEY -p 22 -o BatchMode=yes" > "$RECOVERY_CONFIG"
recovery_repo_cmd snapshots >/dev/null
tail -1 "$FAKE_RESTIC_LOG" | grep -q "^sftp:b1@$host:/shares/restic-b1|" || fail "recovery path"

# 10: existing repository is detected and validated, never re-initialized
before=$(cat "$CONFIG"); : > "$FAKE_RESTIC_LOG"; : > "$CONFIG"
run "$host\n\nb1\n2\n1\n1\n\n" || fail "existing repository"
expect "Existing Restic repository validated (ID 0e0e0e0e0e0e"
! grep -q '|init' "$FAKE_RESTIC_LOG" || fail "existing repository re-initialized"
reject "Initialize a new"

# Existing repository that the configured password cannot open
: > "$CONFIG"; export FAKE_RESTIC_BADPW=1
if run "$host\n\nb1\n2\n1\n1\n"; then fail "accepted undecryptable repository"; fi
expect "cannot be opened"; [[ ! -s "$CONFIG" ]] || fail "config written after failed validation"
unset FAKE_RESTIC_BADPW

# 17: switching away from a configured destination needs CHANGE; legacy config untouched
printf 'RESTIC_REPOSITORY=%q\nRESTIC_PASSWORD_FILE=%q\nRESTIC_SFTP_ARGS=%q\n' "sftp:b1@old.example:/b1/b1" "$work/restic-password" "-i $SFTP_KEY -p 22 -o IdentitiesOnly=yes -o BatchMode=yes" > "$CONFIG"
legacy=$(cat "$CONFIG")
if run "$host\n\nb1\n2\n1\n1\nno\n"; then fail "destination switched without CHANGE"; fi
expect "Currently configured repository: sftp:b1@old.example:/b1/b1"
expect "Cancelled. Previous settings kept."
[[ "$(cat "$CONFIG")" == "$legacy" ]] || fail "legacy configuration modified"
: > "$FAKE_RESTIC_LOG"; repo_cmd snapshots >/dev/null || true
grep -q '^sftp:b1@old.example:/b1/b1|' "$FAKE_RESTIC_LOG" || fail "legacy repository no longer used"
run "$host\n\nb1\n2\n1\n1\nCHANGE\n\n" || fail "explicit CHANGE"
[[ "$(setting RESTIC_REPOSITORY)" == "sftp:b1@$host:/shares/restic-b1" ]] || fail "switch after CHANGE"

# 2, 15: rejected key; configuration preserved
reset_nas; printf 'RESTIC_REPOSITORY=/keep/me\n' > "$CONFIG"; export FAKE_SFTP_MODE=auth
if run "$host\n\nb1\n2\n0\n"; then fail "auth failure accepted"; fi
expect "Public-key authentication failed for b1@$host"
reject "This service allows sftp connections only"
[[ "$(cat "$CONFIG")" == "RESTIC_REPOSITORY=/keep/me" ]] || fail "config changed after failed auth"

# 3: host key mismatch is fatal, never bypassed
export FAKE_SFTP_MODE=hostkey-changed
if run "$host\n\nb1\n2\n0\n"; then fail "changed host key accepted"; fi
expect "host key of $host has CHANGED"
[[ ! -s "$FAKE_SSH_LOG" ]] || fail "tried to connect despite changed host key"

# Unknown host key: OpenSSH asks (StrictHostKeyChecking=ask) over the sftp subsystem
reset_nas; export FAKE_SFTP_MODE=hostkey-unknown FAKE_SFTP_KNOWN="$work/known"
run "$host\n\nb1\n2\ny\n\n\ny\n" || fail "trusting a new host key"
expect "not trusted yet"; expect "SFTP authentication: OK"
grep -q -- '-s .*StrictHostKeyChecking=ask.*sftp$' "$FAKE_SSH_LOG" || fail "host key prompt did not use the sftp subsystem: $(cat "$FAKE_SSH_LOG")"
! grep -q 'StrictHostKeyChecking=no\|accept-new' "$FAKE_SSH_LOG" "$FAKE_SFTP_LOG" || fail "weakened host key checking"
reset_nas; export FAKE_SFTP_MODE=hostkey-unknown
if run "$host\n\nb1\n2\nn\n0\n"; then fail "unknown host key used without consent"; fi
expect "Host key not accepted."

# 14: unreachable destination
reset_nas; export FAKE_SFTP_MODE=unreachable
if run "nohost.invalid\n\nb1\n2\n0\n"; then fail "unreachable host accepted"; fi
expect "Cannot reach nohost.invalid:22"

# 12, 15: read-only directory rejected, nothing saved
reset_nas; export FAKE_SFTP_READONLY=/shares/restic-b1
if run "$host\n\nb1\n2\n1\n1\n"; then fail "read-only directory accepted"; fi
expect "Check write permission: FAILED"; [[ ! -s "$CONFIG" ]] || fail "config written for read-only directory"

# 13: cleanup failure reports the exact temporary path
reset_nas; export FAKE_SFTP_NORMDIR=1
run "$host\n\nb1\n2\n1\n1\ny\n" || fail "write test with failed cleanup"
expect "could not be removed: /shares/restic-b1/.bitx-bk-write-test-"

# 6, 8: no /shares -> manual path; shared base with hostname subdirectory (created on confirmation)
reset_nas; rm -rf "$work/nas/shares"; mkdir -p "$work/nas/backups"
run "$host\n2222\nb1\n2\n/backups\n\ny\ny\n" || fail "shared base layout"
expect "No accessible /shares directories found"
[[ -d "$work/nas/backups/b1" ]] || fail "hostname subdirectory not created after confirmation"
[[ "$(setting RESTIC_REPOSITORY)" == "sftp:b1@$host:/backups/b1" ]] || fail "hostname subdirectory"
[[ "$(setting SFTP_REPOSITORY_LAYOUT)" == hostname && "$(setting SFTP_REPOSITORY_BASE)" == /backups ]] || fail "shared layout not stored"
[[ "$(setting RESTIC_SFTP_ARGS)" == *"-p 2222"* ]] || fail "custom port"

# Declining creation of the hostname subdirectory saves nothing
reset_nas; mkdir -p "$work/nas/backups"
if run "$host\n\nb1\n2\n2\n/backups\n2\nn\n"; then fail "saved without creating the directory"; fi
[[ ! -e "$work/nas/backups/b1" && ! -s "$CONFIG" ]] || fail "created or saved after declining"

# Missing parents are never created
reset_nas
if run "$host\n\nb1\n2\n2\n/missing/parent\n1\n"; then fail "created missing parents"; fi
expect "is missing or not accessible; it is not created automatically"
[[ ! -e "$work/nas/missing" ]] || fail "parent directory created"

# 9: hostname suffix is not duplicated (/b1/b1)
reset_nas; mkdir -p "$work/nas/b1"
run "$host\n\nb1\n2\n2\n\n\ny\n" || fail "default /b1 path"
expect "already ends with /b1"
[[ "$(setting RESTIC_REPOSITORY)" == "sftp:b1@$host:/b1" ]] || fail "duplicated hostname suffix"

# Non-empty directory that is not a repository is refused
reset_nas; touch "$work/nas/shares/restic-b1/notes.txt"
if run "$host\n\nb1\n2\n1\n1\n"; then fail "initialized a non-empty directory"; fi
expect "not empty and is not a Restic repository"

# 18: spaces and special characters in remote paths
reset_nas; special='/shares/my backups "x" $(touch PWNED) ;&'
mkdir -p "$work/nas$special"
run "$host\n\nb1\n2\n1\n1\ny\n" || fail "discovered special path"
expect "1) $special"
[[ "$(setting RESTIC_REPOSITORY)" == "sftp:b1@$host:$special" ]] || fail "special path stored incorrectly: $(setting RESTIC_REPOSITORY)"
[[ -f "$work/nas$special/config" ]] || fail "special path repository"
reset_nas; manual='/home/sp ace "q" `id` $HOME'
mkdir -p "$work/nas$manual"
run "$host\n\nb1\n2\n2\n$manual\n1\ny\n" || fail "manual special path"
[[ "$(setting RESTIC_REPOSITORY)" == "sftp:b1@$host:$manual" ]] || fail "manual path stored incorrectly: $(setting RESTIC_REPOSITORY)"
[[ ! -e PWNED && ! -e "$work/PWNED" ]] || fail "command injection through the path"
for bad in 'relative/path' '/a/../b' $'/new\nline'; do
 reset_nas
 if run "$host\n\nb1\n2\n2\n$bad\n"; then fail "accepted unsafe path $bad"; fi
done
reset_nas
if run 'bad;host\n\nb1\n'; then fail "accepted unsafe host"; fi
expect "Invalid host"
if run "$host\n\n-oProxyCommand=x\n"; then fail "accepted unsafe user"; fi
expect "Invalid SSH user"

# Public key display and the SFTP-only ssh-copy-id fallback
reset_nas
if run "$host\n\nb1\n3\n1\n0\n"; then fail "cancel returned success"; fi
expect "ssh-ed25519 AAAAfakepublic bitx-bk@b1"
expect "SHA256:fakefingerprint"
reject "PRIVATE-KEY-MATERIAL"
expect "Automatic installation failed"
expect "No SFTP repository settings were saved."

# Disconnect also forgets the stored layout
printf 'RESTIC_REPOSITORY=x\nSFTP_REPOSITORY_LAYOUT=dedicated\nSFTP_REPOSITORY_BASE=/s\nCOLOR=never\n' > "$CONFIG"
disconnect_repository <<< y > "$work/out" 2>&1
[[ "$(cat "$CONFIG")" == "COLOR=never" ]] || fail "disconnect left settings: $(cat "$CONFIG")"

# Atomic settings update keeps unrelated keys and mode 600
printf 'COLOR=never\nRESTIC_REPOSITORY=old\n' > "$CONFIG"
save_settings RESTIC_REPOSITORY 'new value' EXTRA 1
[[ "$(cat "$CONFIG")" == $'COLOR=never\nRESTIC_REPOSITORY=new\\ value\nEXTRA=1' ]] || fail "save_settings: $(cat "$CONFIG")"
[[ "$(stat -c %a "$CONFIG")" == 600 ]] || fail "config mode"
echo "sftp-nas: all checks passed"

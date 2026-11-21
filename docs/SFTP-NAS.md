# SFTP-only destinations and bitX Backup Server

From v0.14.11, `bitx-bk repo-config-sftp` supports SFTP accounts that cannot
run a shell, for example bitX Backup Server users (OpenSSH
`ForceCommand internal-sftp` with a chroot). Nothing has to change on the
server: shell access stays disabled.

## Why v0.14.10 failed

Version 0.14.10 checked the key with `ssh user@host true`, which runs a remote
shell command. An SFTP-only server refuses that with
`This service allows sftp connections only.` even though the key was accepted,
so bitx-bk reported "Key authentication failed". Backups were never the
problem: Restic's SFTP backend already uses the SFTP subsystem.

v0.14.11 runs every check with `sftp -b` (batch mode) through the SFTP
subsystem:
- `BatchMode=yes`, `IdentitiesOnly=yes` and the bitx-bk key
  `/etc/bitx-bk/ssh/id_ed25519`;
- a 15 s connect timeout and a 60 s limit per check;
- password and keyboard-interactive login disabled.

Host keys are always verified. bitx-bk never uses
`StrictHostKeyChecking=no` or `accept-new`.

## What the messages mean

| Message | Meaning | Fix |
|---------|---------|-----|
| `Public-key authentication failed` | The server rejected the bitx-bk key. | Authorize the key from option 3 (`sudo bitx-bk sftp-public-key`) for that user. |
| `host key ... is not trusted yet` | First connection. | Check the fingerprint with the server administrator, then answer `y`. OpenSSH asks once, over the SFTP subsystem. |
| `host key ... has CHANGED` | The server identity differs from `known_hosts`. | Do not continue until the administrator confirms a reinstall. Then remove the old entry: `ssh-keygen -R '[host]:port'`. |
| `Cannot reach host:port` | DNS, port, firewall or VPN. | Check Headscale/Tailscale and the port. |
| `SFTP subsystem is not available` | The account has no SFTP, or prints a banner that breaks SFTP. | Fix the account on the server. |
| `Check write permission: FAILED` | The directory is read-only for this user. | Grant write access (bitX: Shares → share → Drepturi), or pick another directory. |
| `could not be removed: <path>` | The write test worked but its empty temporary directory stayed. | Remove that exact path; it was created by bitx-bk. |
| `not empty and is not a Restic repository` | The directory has other files. | Pick an empty directory or the hostname layout. Nothing is initialized there. |
| `cannot be opened with ...restic-password` | A repository exists, but your password does not open it. | Use the matching password file; nothing was modified. |

## Configuring b1 on bitX Backup Server

```
sudo bitx-bk repo-config-sftp
SFTP host/IP: bitx-backup-server.b.bain.ro
SSH port [22]:
SSH user: b1
SFTP authentication -> 2) Key is already authorized - test SFTP
Available remote directories -> 1) /shares/restic-b1
Repository layout -> 1) Use selected directory directly
Final Restic repository: sftp:b1@bitx-backup-server.b.bain.ro:/shares/restic-b1
```

If b1 already backs up somewhere else, bitx-bk shows that destination and
requires `CHANGE` before switching. The old snapshots are **not** copied. To
keep them, migrate first (below), or keep the old destination configured until
you decide.

The saved settings in `/etc/bitx-bk/bitx-bk.conf`:
- `RESTIC_REPOSITORY`: the final `sftp:` URL.
- `RESTIC_SFTP_ARGS`: the ssh options Restic uses.
- `SFTP_REPOSITORY_LAYOUT`: `dedicated` or `hostname`.
- `SFTP_REPOSITORY_BASE`: the directory chosen before the layout.

Backup, snapshots, check, retention and the schedule all use
`RESTIC_REPOSITORY`, so they use the same path. Configurations written by older
versions have no layout keys and keep working unchanged.

## Updating b1

```bash
cd /opt/bitx-bk && sudo ./deploy.sh      # or: sudo bitx-bk update
bitx-bk --version                        # 0.14.11
sudo bitx-bk repo-config-sftp            # only if you want to (re)configure the destination
```

Updating does not modify the existing configuration, repository, key or
password file.

## Moving an existing repository without losing snapshots

Restic files are encrypted and content-addressed. A byte-for-byte copy keeps
the same repository ID and every snapshot. Never run `restic init` on the
destination for a migration. Steps 1–7 do not change bitx-bk's configuration:
the old destination stays active until step 8.

1. **Pause backups and record the source.**
   ```bash
   sudo systemctl stop bitx-bk.timer
   sudo mkdir -m 700 -p /root/bitx-migration && cd /root/bitx-migration
   sudo bash -c 'set -a; . /etc/bitx-bk/bitx-bk.conf; set +a
     restic -o "sftp.args=${RESTIC_SFTP_ARGS:-}" cat config > config-before.json
     restic -o "sftp.args=${RESTIC_SFTP_ARGS:-}" snapshots --json > snapshots-before.json
     restic -o "sftp.args=${RESTIC_SFTP_ARGS:-}" check'
   ```
2. **Check that the destination is empty and writable.** Run
   `sudo bitx-bk repo-config-sftp`, choose the new directory and read the
   checks (`Check write permission: OK`, `NEW destination`). Answer anything
   other than `CHANGE` when asked: nothing is saved.
3. **Copy the repository files unchanged.** Use the tool that fits the source:
   - Local repository, e.g. `/backup/bitx-bk`:
     ```bash
     cd /backup/bitx-bk && sudo sftp -i /etc/bitx-bk/ssh/id_ed25519 -o IdentitiesOnly=yes \
       b1@bitx-backup-server.b.bain.ro <<'EOF'
     cd /shares/restic-b1
     put -r .
     EOF
     ```
   - Repository on another server: copy it to a local staging directory first
     (`sftp -r` / `rsync -a` from the old server), check the file count, then
     upload it as above.

   The source is only read. Do not copy `locks/` contents; Restic recreates
   them.
4. **Compare the files.** On both sides, compare the number of files in `data/`,
   `index/`, `snapshots/`, `keys/` and the size of `config`. The bitX console
   File Manager or `ls` in `sftp` shows them.
5. **Open the copy with the same password, without saving anything.**
   ```bash
   sudo bash -c 'export RESTIC_REPOSITORY="sftp:b1@bitx-backup-server.b.bain.ro:/shares/restic-b1"
     export RESTIC_PASSWORD_FILE=/etc/bitx-bk/restic-password
     A="-i /etc/bitx-bk/ssh/id_ed25519 -p 22 -o IdentitiesOnly=yes -o BatchMode=yes"
     restic -o "sftp.args=$A" cat config > /root/bitx-migration/config-after.json
     restic -o "sftp.args=$A" snapshots --json > /root/bitx-migration/snapshots-after.json
     restic -o "sftp.args=$A" check --read-data-subset=5%'
   ```
6. **Compare the IDs.** They must be identical:
   ```bash
   cd /root/bitx-migration
   diff <(python3 -c 'import json;print(json.load(open("config-before.json"))["id"])') \
        <(python3 -c 'import json;print(json.load(open("config-after.json"))["id"])')
   diff <(python3 -c 'import json;[print(s["id"]) for s in json.load(open("snapshots-before.json"))]' | sort) \
        <(python3 -c 'import json;[print(s["id"]) for s in json.load(open("snapshots-after.json"))]' | sort)
   ```
7. **Test a restore** of a small path from the copy into a new directory, for
   example
   `restic ... restore latest --include /etc/hostname --target /root/bitx-migration/restore-test`,
   and compare it with the original.
8. **Switch.** Run `sudo bitx-bk repo-config-sftp` and choose the new
   directory. bitx-bk detects the existing repository and validates it. When
   the repository IDs match, it reports "Same repository ID" and only asks to
   save. Then restart backups with `sudo systemctl start bitx-bk.timer`.
9. **Keep the old copy** until several new backups and a `bitx-bk check` have
   succeeded at the new destination. Remove it only afterwards, by hand.

If any check fails, stop. The old destination is still configured and intact.

## Tests

- `tests/sftp-nas.sh` runs everywhere. It uses a simulated SFTP-only server and
  covers authentication, host keys, discovery, layouts, write and cleanup
  checks, repository detection, special paths and config preservation.
- `tests/sftp-nas-integration.sh` runs against a real OpenSSH server with
  `ForceCommand internal-sftp` and Restic. It is opt-in and needs root:
  `sudo BITX_BK_INTEGRATION=1 bash tests/sftp-nas-integration.sh`. It creates
  and removes a temporary user, an sshd on 127.0.0.1 and a disposable
  repository.

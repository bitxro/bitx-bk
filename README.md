# bitx-bk

Linux backup and disaster-recovery helper built around **restic**.

> Early development release. Test recovery before relying on it in production.

## Features

- cron discovery, including referenced absolute-path scripts/executables
- systemd service and timer inventory
- Docker inventory: containers, inspect data, images, networks, volumes, bind mounts and Compose project metadata/files
- VPN discovery: WireGuard, OpenVPN and WGDashboard
- RustDesk Server discovery (hbbs/hbbr)
- manual include paths
- encrypted, deduplicated snapshots via restic
- doctor, discover, backup, snapshots and check commands
- dry-run mode

## Install

```bash
mkdir -p /tmp/bitx-bk
cd /tmp/bitx-bk
git clone https://github.com/bitxro/bitx-bk.git
cd bitx-bk
sudo ./install.sh
bitx-bk --version
```

The installer keeps a Git clone in `/opt/bitx-bk`, links `bitx-bk` into `/usr/local/bin`, and creates a config file if absent. It does not overwrite an existing installation or config. Install `restic` separately if needed.
The `/tmp/bitx-bk` copy is only used for installation and can be removed afterward. All later updates run from `/opt/bitx-bk`.

Configure the repository using the interactive menu (`bitx-bk` → Settings → Repository) or:

```bash
sudo bitx-bk repo-config-sftp
# alternatively: sudo bitx-bk repo-config-local
```

The configuration is stored at:

```
/etc/bitx-bk/bitx-bk.conf
```

The repository setup creates a password file and initializes a new repository when needed. Keep the password and SFTP key available for disaster recovery.

## First safe test

```bash
sudo bitx-bk doctor
sudo bitx-bk discover
sudo bitx-bk backup --plan
```

If the report looks correct:

```bash
sudo bitx-bk backup
sudo bitx-bk snapshots
sudo bitx-bk check
```

## Security

Restic encrypts repository contents. bitx-bk never intentionally prints WireGuard private keys, OpenVPN private material, RustDesk private keys, Docker environment values, or the restic password.

The staging directory is root-only and is removed after each run.

## Docker / disaster recovery

bitx-bk stores Docker metadata and discovered Compose project directories. Files within those directories, including `.env` and bind-mounted data located there, are included. External bind mounts, named-volume data and Docker image binaries are not automatically captured by the current collector. Image inventory is not an image export. Check these dependencies before treating a backup as a complete application recovery point.

### Selective Recovery from an existing SFTP repository (v0.13.0)

Install bitx-bk on the recovery machine, then open **4) Recovery -> 6) Connect existing SFTP repository** (or run `sudo bitx-bk recovery-config-sftp`). Enter the host, SSH port, user and **exact absolute repository path**, including the original server's folder. Hostnames and IPv4 addresses are supported by this setup flow.

The wizard generates a dedicated Ed25519 key automatically and reuses it on subsequent connections. It displays the public key and offers `ssh-copy-id` password-based authorization or manual authorization. Confirm the storage server fingerprint independently. A restricted SFTP-only account is supported: no remote shell command is used to test the connection. Key authorization initially modifies the SSH account's authorized keys; repository access afterward can be read-only.

Enter the **original Restic encryption password**, not the SSH login password. A wrong password or inaccessible repository fails without initializing anything or replacing the previous Recovery configuration. The root-only Recovery configuration and password files are stored separately under `/etc/bitx-bk`; backup settings remain unchanged. Password files for previous connections are retained, so reconnecting does not destroy an existing recovery credential.

All Recovery repository commands use `--no-lock`. Use a storage account restricted to read-only repository permissions to enforce this at the server. Do not run concurrent `prune`, deletion or repository maintenance while a restore is reading without a lock.

Use options 1-5 to list snapshots, inspect data and audit it. Recovery lists all snapshots, including snapshots without bitx-bk tags. **7) Restore selected resource to new directory** (or `sudo bitx-bk recovery-restore`) lets you choose a snapshot and an available root, Docker project or Virtualmin archive directory. Provide a new absolute local destination whose parent already exists, then type `RESTORE`. Existing destination directories are refused. Original path hierarchy is preserved: restoring `/opt/docker/app` into `/srv/recovery-test` produces `/srv/recovery-test/opt/docker/app`.

Restic restores only the selected path and runs `--verify`. On failure, the command reports failure and retains partial files for inspection. A successful file verification does not prove application/database consistency. Restore does not install Docker, import databases, deploy applications, load images or start services. Review Compose files, external volumes, local images, permissions, architecture and port conflicts before manually deploying a restored project. For complete Docker migration, missing external dependencies must be backed up separately. Native Virtualmin archives can be recovered here and subsequently imported using Virtualmin's native restore workflow.

## Updates

```bash
cd /opt/bitx-bk
sudo ./deploy.sh
```

## License

GPL-3.0

### Recovery requirements (v0.14.0)

Recovery checks local access tools at entry and before restoring. Option 8 or
`sudo bitx-bk recovery-doctor` reruns this check without needing backup configuration.
Missing required tools stop restoration; the report suggests explicit installation
commands. Nothing is installed automatically.

After resource selection, and again after verified recovery, a report checks Docker
Engine/Compose/daemon or Virtualmin/Webmin and relevant service commands. Missing
application runtimes do not block file recovery. Virtualmin services are candidates:
the archived domain features must be reviewed to decide which are actually required.
No recovered configuration is executed and no service is started.

The report reads source OS, architecture and package inventory from the selected
snapshot using read-only Restic dump. Inventory absent from old or project-only
snapshots is explicitly UNKNOWN; it is never replaced by information from a
different snapshot. New system backups include OS metadata and corrected Debian
package inventory. Full package lists are evidence, not an automatic install plan.
Exact PHP extensions, database compatibility, external volumes, local images and
resource-specific service requirements still require review when metadata is
insufficient. Free destination space is displayed before restoration, but this is
not a guaranteed capacity estimate. Native Virtualmin domain restoration remains
a separate manual operation after recovering the archives.

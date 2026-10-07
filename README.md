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

## Automatic backups

Open **Settings → Automatic backup / service → Add service / set backup times**.
Enter daily hours separated by commas, for example `10,12,18,00`, or include
minutes: `10:30,18:45`. Times use the server's timezone. Repeating a time does
not create an extra run. Updating the times replaces the previous schedule.

The application verifies repository access, then installs `bitx-bk.service`
and enables `bitx-bk.timer`, including after reboot. The service runs the same
backup command and Telegram notifications as a manual backup. The menu shows
status, the next run and recent service logs. A running service cannot start
again concurrently; a scheduled occurrence during that run is skipped. Missed
runs while the server is offline are skipped, so startup does not trigger an
unexpected backup. Existing cron jobs are not modified; remove any separate
bitx-bk cron schedule before enabling this timer to avoid duplicate runs.

**Remove service / disable automatic backups** removes the managed service
and timer, preserving configuration, snapshots and an in-progress backup.
Manually created units with the same names are left untouched.

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

From v0.14.5, each discovered Compose project snapshot includes its directory, persistent bind-mount sources outside that directory and the data mountpoints of attached local Docker volumes (named and anonymous). Containers without Compose labels get separate snapshots for their attached persistent mounts. Compose projects outside `/opt/docker` are also covered. Volume inspect metadata and source system inventory are included as well. Storage paths come from Docker inspection, supporting custom Docker data roots rather than assuming `/var/lib/docker/volumes`. Backup plan lists these sources; **5) Audits & diagnostics -> 2) Docker recovery audit** checks every container against the current collector and flags unsupported storage. This audit does not certify older snapshots. Running containers that use the same data, including consumers from other projects, are stopped for the snapshot and restarted afterward. Runtime sockets are excluded. Missing data, unsupported special files and volumes with non-local drivers or driver options abort the backup rather than being silently skipped; those storage types need a dedicated backup strategy.

Earlier snapshots cannot gain missing data: update the source server and run a new backup. Unattached volumes, container writable layers and Docker image binaries remain outside this project backup. Image inventory is not an image export. Host processes writing into the same bind mounts are not stopped automatically. Review these dependencies before treating a backup as a complete application recovery point.

Recovery option 7 lists the data roots saved in each new snapshot for selective recovery. **10) Restore complete Docker snapshot (project + volumes + bind mounts)** recovers a project and its attached data together from a v0.14.5+ Docker snapshot. It restores/verifies the entire snapshot into a protected recovery directory, then registers named local volumes through Docker and populates their actual destination mountpoints (including when Docker's data root differs). Volume labels are preserved. Bind mounts and the project are restored to their original paths. Existing identical project/bind data is retained; different existing data and nonempty volumes are refused. Running containers using the data block deployment. Source inventory and snapshot manifests remain in the recovery directory, without replacing live bitx-bk staging metadata. Docker must already be installed; no containers start automatically. Anonymous volumes and storage with custom driver/options still require selective recovery and explicit remapping. The verified recovery directory is retained even after success; if deployment fails after copying some data, those copies are retained too and the command reports failure.

### Selective Recovery from an existing SFTP repository (v0.13.0)

Install bitx-bk on the recovery machine, then open **4) Recovery -> 6) Connect existing SFTP repository** (or run `sudo bitx-bk recovery-config-sftp`). Enter the host, SSH port, user and **exact absolute repository path**, including the original server's folder. Hostnames and IPv4 addresses are supported by this setup flow.

The wizard generates a dedicated Ed25519 key automatically and reuses it on subsequent connections. It displays the public key and offers `ssh-copy-id` password-based authorization or manual authorization. Confirm the storage server fingerprint independently. A restricted SFTP-only account is supported: no remote shell command is used to test the connection. Key authorization initially modifies the SSH account's authorized keys; repository access afterward can be read-only.

Enter the **original Restic encryption password**, not the SSH login password. A wrong password or inaccessible repository fails without initializing anything or replacing the previous Recovery configuration. The root-only Recovery configuration and password files are stored separately under `/etc/bitx-bk`; backup settings remain unchanged. Password files for previous connections are retained, so reconnecting does not destroy an existing recovery credential.

All Recovery repository commands use `--no-lock`. Use a storage account restricted to read-only repository permissions to enforce this at the server. Do not run concurrent `prune`, deletion or repository maintenance while a restore is reading without a lock.

Use options 1-5 to list snapshots, inspect data and audit it. Recovery lists all snapshots, including snapshots without bitx-bk tags. **7) Restore selected resource (original path or new directory)** (or `sudo bitx-bk recovery-restore`) lets you choose a snapshot and an available root, Docker project or Virtualmin archive directory, then choose a destination mode:

- **1) Original path (default):** `/opt/docker/nginx` is recovered directly to `/opt/docker/nginx`. The destination must not exist, and its path must not traverse symlinks. Missing parent directories are created after confirmation. Data is first restored and verified in a temporary directory, then moved to the original path without replacing existing data. Failed restores retain their temporary files for inspection.
- **2) New directory:** provide a new absolute local destination whose parent already exists. Original hierarchy is preserved: restoring `/opt/docker/app` into `/srv/recovery-test` produces `/srv/recovery-test/opt/docker/app`.

Both modes require typing `RESTORE`. Existing destinations are refused; no services start automatically.

**9) Prepare external Docker networks for recovered project** reads the resolved Compose configuration in an existing project directory (for example `/opt/docker/nginx`). It lists external networks, reuses existing networks and, after confirmation, creates all missing ones as default bridge networks with Docker-assigned subnets. This includes custom/interpolated network names; Compose-managed networks remain Compose's responsibility. Docker Engine, Compose and Python 3 must already be installed. External network declarations do not contain the original driver/subnet: this option is a default bridge fallback, not a restoration of the source network configuration. Missing networks used with static container addresses are refused and require their original subnet/IPAM settings. No containers are started; afterward run `docker compose up -d` in the project directory.

Restic restores only the selected path and runs `--verify`. On failure, the command reports failure and retains partial files for inspection. A successful file verification does not prove application/database consistency. Restore does not install Docker, import databases, deploy applications, load images or start services. Review Compose files, external volumes, local images, permissions, architecture and port conflicts before manually deploying a restored project. For complete Docker migration, dependencies outside the captured project sources must be backed up separately. Native Virtualmin archives can be recovered here and subsequently imported using Virtualmin's native restore workflow.

## Updates

Choose **7) Update application** in the main menu. It runs `deploy.sh`, checks for local changes, fetches the current branch and applies only a fast-forward update. After success, the menu reloads the updated application. Failed updates report the error without closing the menu.

From the command line, use `sudo bitx-bk update`, or:

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

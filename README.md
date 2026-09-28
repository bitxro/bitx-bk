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

bitx-bk stores Docker metadata plus persistent volume/bind-mount data. Compose files and associated `.env` files are discovered from Docker Compose labels when possible. Docker image export is optional because registry images can normally be pulled again.

Recovery provides read-only snapshot inspection and audits. Automated restore is not enabled.

## Updates

```bash
cd /opt/bitx-bk
sudo ./deploy.sh
```

## License

GPL-3.0

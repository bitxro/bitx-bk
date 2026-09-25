# bitx-bk

Linux backup and disaster-recovery helper built around **restic**.

> Early development release. Test recovery before relying on it in production.

## v0.1.0 scope

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
git clone https://github.com/bitxro/bitx-bk.git
cd bitx-bk
sudo ./install.sh
```

Then edit:

```
/etc/bitx-bk/bitx-bk.conf
```

Create a root-readable restic password file, for example:

```bash
sudo install -m 600 /dev/null /root/.config/bitx-bk-restic-password
sudo nano /root/.config/bitx-bk-restic-password
```

Set `RESTIC_REPOSITORY` and `RESTIC_PASSWORD_FILE` in the config.

Initialize a new repository once:

```bash
sudo bitx-bk init
```

## First safe test

```bash
sudo bitx-bk doctor
sudo bitx-bk discover
sudo bitx-bk backup --dry-run
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

A full automated destructive recovery command is deliberately not enabled in v0.1.0. Recovery will be added after backup/discovery output has been tested on real systems.

## License

GPL-3.0

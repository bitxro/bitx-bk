# Changelog

## 0.14.11

### Fixed
- SFTP repository setup failed on SFTP-only accounts
  (`ForceCommand internal-sftp`, e.g. bitX Backup Server) with "Key
  authentication failed". The key check ran a remote shell command
  (`ssh … true`). All checks now use the SFTP subsystem (`sftp -b`), with
  strict host-key verification and timeouts.
- An unreadable remote directory is no longer treated as accessible. OpenSSH
  `sftp` exits 0 when `ls` cannot read a directory, so the error output is now
  checked as well.

### Added
- SFTP authentication menu:
  - automatic key installation;
  - an SFTP test of an already authorized key;
  - display of the public key (never the private key).
- New `bitx-bk sftp-public-key` command.
- When a host key is unknown, OpenSSH asks once to trust the fingerprint
  (`StrictHostKeyChecking=ask`, over the SFTP subsystem). A changed host key
  stops the setup.
- Discovery of readable directories under `/shares`, with manual entry always
  available.
- Repository layouts: a dedicated directory (used directly) or a hostname
  subdirectory of a shared base. A trailing hostname is not doubled
  (`/b1/b1`). The layout is stored as `SFTP_REPOSITORY_LAYOUT` and
  `SFTP_REPOSITORY_BASE`.
- Destination checks before saving:
  - the directory exists (missing directories are created only on
    confirmation, parents never);
  - a temporary write test, removing only its own directory;
  - empty, existing Restic repository (validated with the configured
    password) or another non-empty directory (refused).
- `restic init` runs only after explicit confirmation for an empty directory.
- Switching from an already configured destination requires typing `CHANGE`;
  repository IDs are compared when possible.
- Configuration updates are atomic (temporary file + rename). Failed
  validation keeps the previous settings.
- `docs/SFTP-NAS.md`: error guide, b1 setup and a repository migration
  procedure that preserves snapshots.
- Tests: `tests/sftp-nas.sh` (simulated SFTP-only server) and the opt-in
  `tests/sftp-nas-integration.sh` (real sshd + Restic).

### Compatibility
- Existing configurations, keys, password files and repositories are not
  modified. Backups, snapshots, check, recovery and scheduling keep using
  `RESTIC_REPOSITORY` and `RESTIC_SFTP_ARGS` as before.
- New SFTP configurations add `-o ConnectTimeout=15` to `RESTIC_SFTP_ARGS`.

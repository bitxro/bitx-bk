# File Manager and Backup Explorer

File Manager is part of the HTTPS console. In **File Manager**, an administrator
enables web access for an existing NAS file identity and sets its web
password. Log in as **Cont de fișiere** to browse. Web passwords use the
existing password hashing, session expiry, rate limiting and CSRF protection.
They do not change SMB or SFTP credentials. The web administrator does not
receive automatic access to personal files or decrypted backups.

**Files** lists the user's home and permitted unified shares. Share grants are
checked on each request. Upload and download stream in chunks. Copy and move
accept a writable destination root, and file publication uses no-replace
renames. Deletion requires typing the exact file or directory name. Existing
Restic associations are protected from ordinary file mutations. File
previews support small plain-text files and selected raster image formats.
PDF files are available as downloads.

## Repository access

Associate a repository in **Repositories**, then explicitly assign
**Poate explora și restaura backupuri** in the administrative File Manager.
An SMB or SFTP share grant alone does not confer decryption access. Grants
are tied to file identity and repository association generations so
recreating a name does not restore its previous grants.

Restic 0.18 is launched as a dedicated unprivileged identity in a
Bubblewrap sandbox with no external network and only the selected repository
bound read-only. Provision the root-owned mode-0600 credential file at
`/etc/bitx-console/restic/NAME.password`. The password stays on the VM.
The worker accepts fixed Restic operations and does not offer repository
maintenance commands.

Browse snapshots and use search, file download, a directory TAR, a TAR of
selected files, or diff. A multi-file archive is limited to 20 regular files
and 8 GiB. Metadata, search and command execution have time and size limits.
Portable filename restrictions apply to paths exposed by both explorers.
Symbolic links and special files are excluded from directory restoration.
FUSE is not used.

## Restore

Select a file or directory and choose a writable root and a new directory
name. The broker checks the disk, free space and access, then queues the job.
The worker rechecks permissions at job start and publishes regular files
without replacing existing targets. Restored files preserve the selected
snapshot hierarchy. This does not start or import Docker or Virtualmin
services. Review recovered configuration and data before application use.

A single background worker processes a persistent queue. The console shows
status and restored bytes. Queued jobs can start after a reboot. An interrupted
job remains visible and requires a new restore into a new directory, keeping
the partial directory for inspection. A single Restic command is limited to
180 seconds. Large jobs can require the existing offline recovery procedure.

## Disk portability

All storage operations require the configured backup UUID. exFAT lacks POSIX
ACLs. Windows direct access to the USB drive bypasses application rules.
For offline recovery, connect the disk to a trusted computer and use the
separately stored Restic password with the official Restic executable.
See [BACKUP-SERVER.md](BACKUP-SERVER.md) and [NAS-MIGRATION.md](NAS-MIGRATION.md).

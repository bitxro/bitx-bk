# Unified NAS deployment and migration

Unified file users have one local Unix identity, one directory under
`bitX-BACKUP/homes`, and independent SMB and SFTP switches. Protocol passwords
are installed in the local account and Samba databases as needed. Only hashes
are retained by those services. The web administrator stays in the web
database and gets no automatic file access.

Shares live under `bitX-BACKUP/shares`. A new share has no access grants.
Renaming it changes the published SMB and SFTP name while the physical path
stays stable. Disabling a share removes publication and leaves its files and
Restic associations in place. Changing a grant closes affected SFTP sessions
and restarts Samba, so clients must reconnect.

SMB uses a per-account `home` and signed, encrypted authenticated shares.
SFTP exposes writable `/home` plus `/shares/<name>` within a root-owned,
non-writable chroot. Read-only grants are read-only Linux bind mounts for
SFTP and Samba-enforced share permissions for SMB. Root owns the mount
anchors. Samba disallows symlink traversal on managed shares. Legacy
administrative visibility must be assigned explicitly. The storage guard
revokes managed mounts and sessions when the configured volume's mount unit
stops. After reconnecting a disk, verify its UUID and restart the broker,
storage guard and Samba to reapply publication.

exFAT has no per-file POSIX ACLs. Access separation is enforced by the
running services and mounts. Anyone with physical access to the USB drive,
root access or a separately authorized administrative export can bypass
protocol permissions. Do not expose the volume root to regular file users.

## Existing deployment and compatibility

The upgrade leaves `smb-homes`, `sftp-homes`, `smb-shares`, `sftp-files`,
`restic` and existing Windows storage in place. Samba's existing `home` name
uses a bind mount to route each legacy account to its original directory.
Legacy SFTP retains the original chroot paths and authentication. Legacy
`windows-backup` and the explicitly authorized administrative export keep
their original backing paths. Existing identities remain editable in Users
while new file identities use the unified model.

## Migrate after approval

1. Record the verified USB UUID, exported Samba names, effective group
   grants, chroot mounts, local UIDs, repository associations and each
   client's configured host, username and path. Use a privileged session to
   back up the broker state and native credential databases to protected
   storage. Keep credentials outside Git.
2. Identify the exact source and new destination for each account or share.
   Keep the same Unix UID and Samba SID. Do not recreate existing accounts
   with different credentials. Pause writers and verify there are no active
   sessions on the paths being transitioned.
3. Copy data to a new staging directory on the correct volume, keeping the
   original. Check available capacity beforehand. Compare file count, sizes
   and checksums. Convert one account or share at a time and use the same
   SMB name or SFTP compatibility path until clients are approved to change.
   An SFTP chroot can bind the same target under a legacy name during cutover
   only if both mount paths enforce identical permissions.
4. Verify the same user can log in to the expected protocols, check allowed
   and denied paths and test a representative restore. For Restic, compare
   full snapshot IDs and use a controlled integrity check. Resume the pilot's
   writers only after validation. If validation fails, restore the backed-up
   state and export configuration and resume against the original copy.
5. Retain the original directories until the agreed rollback period ends.
   Remove originals only under a separate approved cleanup.

## Retained personal directories

Deleting a unified account revokes SMB and SFTP and keeps its home on the
HDD. An orphan directory blocks automatic reuse of the same name. To recover
data, an authorized file administrator can copy it from the retained
directory using a trusted administration channel. To reclaim a username,
verify the previous owner's identity, back up the orphan directory and move
it to a protected archive after approval. Create the account normally, then
copy the approved files to the new home. Record the ownership decision.

## Repository associations

Repositories associates a share, a single subdirectory and a client label.
It can create a missing empty directory. It never initializes or deletes a
Restic repository. Removing an association deletes only its metadata.
An authorized administrator may provision a repository-specific mode-0600
root-owned password file under `/etc/bitx-console/restic`. Snapshot lookup
and integrity checks are explicit operations, run by a dedicated
unprivileged identity. They use read-only Restic operations without locks
or cache. The interactive check is bounded. Run long or full-data checks
using the separately authorized operational recovery procedure when writers
are paused.

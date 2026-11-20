# Backup server and offline recovery

Deploy a minimal Debian guest with a separate virtual system disk, VirtIO networking and QEMU Guest Agent. Attach the external backup disk through USB passthrough. Verify the selected disk by its model, capacity and serial before attaching it. Use `lsblk` and `blkid` to inspect the existing filesystems. Check an unmounted exFAT filesystem with `fsck.exfat -n` before mounting. Mount the intended partition by UUID at `/mnt/bitx-backup`. Leave other partitions intact.

Use distinct directories for Restic repositories, Windows backups and recovery instructions. Bind only the Restic directory into a root-owned SFTP chroot. Give backup clients distinct keys and chroots. Restrict SSH to approved private networks. Publish the Windows directory through an authenticated SMB 3 share with encryption and guest access disabled. Make the share depend on the backup mount, with inaccessible local directories underneath each mount. Verify SSH, authenticated SMB, guest rejection, SFTP and both mounts after a reboot. Reserve the guest address in DHCP before enrolling clients.

## Migrate existing repositories

1. Inventory the destination, repository ID, complete snapshot IDs, latest backup and scheduler for each source. Measure the physical repository trees at the storage backend. Restic raw-data stats measure referenced data and are an estimate for transfer planning.
2. Verify available destination space and reserve room for growth. Prepare a dedicated SFTP key and isolated directory per source. Store the source configurations and Restic passwords in the credential vault.
3. Obtain approval before transfer or destination changes. Pause all writers and let current backups finish. Run `restic check` at each source and capture the final snapshot inventory.
4. Copy the entire encrypted repository tree into a new destination directory. Preserve the original. Compare the full snapshot IDs and run `restic check --read-data` at the destination. Restore representative data and, where applicable, a Compose project into a separate test location.
5. Switch one source first. Verify a new backup and a restore before switching other sources. To roll back, pause the affected writer, restore its saved configuration and resume backups to the original. Keep both copies through validation and retain the originals through the agreed retention period.

exFAT lacks journaling and per-file POSIX permissions. Use a clean shutdown before unplugging, avoid simultaneous mounts, verify repository integrity after unexpected disconnects and keep an independent backup copy.

## Recover without the host

Connect the disk to a Windows computer and open the backup volume. Install Restic from its official distribution. Retrieve the Restic password from the credential vault and enter it at the prompt. Substitute the drive letter, source directory and snapshot ID.

```powershell
restic -r 'E:\bitX-BACKUP\restic\SERVER' snapshots
restic -r 'E:\bitX-BACKUP\restic\SERVER' check
restic -r 'E:\bitX-BACKUP\restic\SERVER' restore SNAPSHOT_ID --target 'C:\bitx-restore'
```

On Linux, substitute the mount path and use forward slashes. Copy Windows backup files directly from the Windows directory. For application or Docker recovery, connect bitx-bk to the existing repository in the Recovery menu, then verify the restored files and services before returning them to production. Store passwords and private keys separately from the disk and from this repository.

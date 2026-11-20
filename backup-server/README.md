# bitX Backup Server management console

The console runs under the unprivileged `bitxweb` account. It uses HTTPS and
server-side, revocable sessions with a 30-minute lifetime. Browser mutations
require a CSRF token. Login attempts are rate limited. Administrative events
appear in Activity and in the system journal.

The root-owned broker accepts a bounded JSON protocol on a Unix socket. It
checks peer credentials, accepts requests only from the console's service UID,
and exposes fixed account and inventory operations. It never accepts a command
line or a filesystem path from a user. A compromise of the web process can
exercise the broker's account operations, so restrict access to the console and
keep both processes patched.

## Install on an existing backup VM

Prerequisites are Debian, the mounted exFAT backup volume, the pre-existing
`windows-backup` Samba share, working OpenSSH and the `bitxdata` storage group.
Install from a trusted, root-reviewed checkout. Set the IPv4 address, allowed
private CIDR and exFAT UUID to match the deployment.

```sh
sudo bash backup-server/install.sh BACKUP_IP PRIVATE_CIDR EXFAT_UUID
sudo -u bitxweb /usr/bin/python3 /opt/bitx-console/app.py bootstrap bitx-admin
```

Enter a unique password at the prompts. The password is never echoed. The
bootstrap command works only while there are zero web administrators. The
web-only administrator has no Unix account or SSH access.

Open `https://BACKUP_IP:8443` from the approved LAN. The installer generates a
self-signed certificate for the given IP. Obtain the public certificate at
`/etc/bitx-console/https.crt` over a trusted channel, verify its fingerprint
and trust it on the intended client, or replace it with a certificate from
your LAN CA. Never distribute `https.key`. Reserve the address in DHCP and
limit access at the network firewall. Keep the console off the public internet.

```sh
sudo openssl x509 -in /etc/bitx-console/https.crt -noout -sha256 -fingerprint
sudo journalctl -u bitx-console -u bitx-broker
```

## Accounts and storage

- **Web administrator** resides only in the console database. Resetting or
  disabling the account revokes its sessions.
- **SFTP** can use explicitly selected directories under the
  mounted Restic area, or ordinary file destinations. With no destination
  selected, the account receives a private folder under
  `bitX-BACKUP/sftp-homes` and starts in `/home`. Account deletion retains
  that folder. Choose **Cheie SSH**, **Parolă**, or **Cheie SSH sau
  parolă**. The last option accepts either credential independently. Existing
  accounts without a configured mode keep key-only access. Upload only the
  client-generated Ed25519 public key. Passwords must be at least 14 characters,
  are set via the local privileged socket, and are hashed by the OS. The
  console never stores or displays them. To reset a password, edit the account
  and submit a new password and confirmation. Deactivation locks the local
  password, removes the key, and terminates active sessions.
- Connect using the displayed `bxsf-` login. Every login has a separate
  root-owned chroot, and each assignment is a separate bind mount. The broker
  restores enabled assignments on boot after the storage mount. Managed accounts
  have no interactive shell, tunnel, or forwarding. OpenSSH allows three
  authentication attempts per connection. PAM locks a managed password for
  15 minutes after five failed attempts within 15 minutes. Key authentication
  remains available in mixed mode when only the password is locked.
- **SMB** uses the displayed `bxsm-` login and the existing authenticated
  `windows-backup` share and the private `home` share. Every SMB account
  gets its own directory under `bitX-BACKUP/smb-homes`. Connecting to
  `\\BACKUP-HOST\home` opens only the authenticated account's directory.
  Account removal keeps that directory and prevents automatic reuse of its
  login. The password is passed through standard input to
  Samba and never retained in the console's database or logs. Reset and
  disable operations restart Samba to invalidate already authenticated sessions,
  briefly interrupting other SMB clients.
- Existing manually configured SFTP and SMB accounts keep their configuration.
  The web console manages identities it creates. Removing a managed identity
  removes access while keeping repository and share contents.

The dashboard reads the mounted volume capacity, systemd service state and
local directories. It distinguishes initialized Restic repositories from
ordinary directories. When snapshot visibility is needed, provision one
root-owned mode-0600 file per repository in
`/etc/bitx-console/restic/REPOSITORY.password` from the credential vault.
Restic reads that repository without locks or cache; credentials are never sent
to the web application. With no credential the dashboard explicitly marks
snapshot information unavailable. The console does not initialize, copy or
delete repository data.

The offline recovery and migration procedures are in
[BACKUP-SERVER.md](../docs/BACKUP-SERVER.md).

On **Shares**, create an SMB share backed by its own directory under
`bitX-BACKUP/smb-shares`. Select **No access**, **Read only**, or **Read and
write** for each Samba account. Changing permissions disconnects current SMB
sessions so cached access is revoked. Existing shares and personal homes
remain separate. Use **Add SFTP directory** on the same page for ordinary
file backups. Assign the resulting `/files-NAME` destination in **Users**.
It stays separate from Restic and is never initialized as a repository.

To allow managed SFTP accounts from an existing Headscale IPv4 network, set
`BITX_HEADSCALE_CIDR` to the approved subnet when installing, or run
`sudo bash backup-server/configure-sftp-auth.sh HEADSCALE_CIDR` followed by
`sudo systemctl reload ssh`. The script validates the candidate configuration
with `sshd -t`. It keeps the LAN restrictions and administrator authentication
rules. Headscale access is additionally governed by the existing tailnet ACL.

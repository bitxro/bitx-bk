"""Unified storage and file identities. Called only by the local root broker."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


class Nas:
    def __init__(self, backend):
        self.b = backend
        self.root = backend.DISK / "bitX-BACKUP"
        self.homes = self.root / "homes"
        self.shares = self.root / "shares"
        self.smb_homes = Path("/srv/bitx-smb-homes")
        self.config = Path("/etc/samba/bitx-nas.conf")
        self.state = backend.STATE / "nas.json"

    def load(self):
        return json.loads(self.state.read_text()) if self.state.exists() else {
            "users": {}, "shares": {}, "repositories": {},
        }

    def save(self, state):
        self.b.atomic_write(self.state, json.dumps(state, indent=2))

    def valid_name(self, value):
        if not isinstance(value, str) or not self.b.NAME.fullmatch(value):
            raise ValueError("Folosește până la 20 de litere mici, cifre sau cratime.")
        return value

    def require_disk(self):
        if not self.b.mounted() or self.root.resolve() != self.root:
            raise ValueError("HDD-ul de backup nu este montat.")
        flags = os.statvfs(self.root).f_flag
        if flags & os.ST_RDONLY:
            raise ValueError("Volumul de backup este montat doar în citire.")

    def folder(self, path):
        if path.resolve() != path or not path.is_dir():
            raise ValueError("Directorul de stocare este indisponibil.")
        return path

    def bind(self, source, target, readonly=False):
        self.folder(source)
        target.mkdir(mode=0o700)
        self.b.run("mount", "--bind", str(source), str(target))
        self.b.run("mount", "-o", "remount,bind,ro,nosuid,nodev,noexec" if readonly
                   else "remount,bind,rw,nosuid,nodev,noexec", str(target))

    def unmount_home(self, name):
        target = self.smb_homes / name
        if target.is_dir():
            if subprocess.run(["mountpoint", "-q", str(target)], check=False).returncode == 0:
                self.b.run("umount", str(target))
            target.rmdir()

    def revoke(self, name, home=True):
        jail = self.b.JAILS / name
        if jail.is_dir():
            group = jail / "shares"
            if group.is_dir():
                (self.b.KEYS / name).unlink(missing_ok=True)
                self.b.set_sftp_group(name, "key")
                self.b.run("usermod", "--lock", name)
                uid = self.b.get_local_user(name).pw_uid
                subprocess.run(["pkill", "-KILL", "-u", str(uid)], check=False)
                for target in group.iterdir():
                    if subprocess.run(["mountpoint", "-q", str(target)], check=False).returncode == 0:
                        self.b.run("umount", str(target))
                    target.rmdir()
                group.rmdir()
            self.b.revoke_sftp(name)
        if home:
            self.unmount_home(name)

    def expose(self, name, user, state, password=""):
        if not user["enabled"] or not self.b.mounted():
            return
        home = self.folder(self.homes / name)
        if user["smb"]:
            target = self.smb_homes / name
            if not target.is_dir():
                self.bind(home, target)
        if not user["sftp"]:
            return
        jail = self.b.JAILS / name
        jail.mkdir(mode=0o755, exist_ok=True)
        jail.chmod(0o755)
        self.bind(home, jail / "home")
        group = jail / "shares"
        group.mkdir()
        group.chmod(0o755)
        for share in state["shares"].values():
            right = share["permissions"].get(name)
            if share["enabled"] and share["sftp"] and right:
                self.bind(self.folder(self.shares / share["directory"]), group / share["name"],
                          readonly=right == "read")
        mode = user["auth"]
        if mode in self.b.PASSWORD_GROUPS:
            if password:
                self.b.run("chpasswd", secret=f"{name}:{password}\n")
                self.b.run("faillock", "--conf", self.b.FAILLOCK, "--user", name, "--reset")
            else:
                self.b.run("usermod", "--unlock", name)
            self.b.set_sftp_group(name, mode)
        if mode in ("key", "either"):
            self.b.atomic_write(self.b.KEYS / name, user["key"], 0o644)

    def samba_config(self, state):
        parts = []
        for share in state["shares"].values():
            name = share["name"]
            allowed = {user: right for user, right in share["permissions"].items()
                       if state["users"].get(user, {}).get("enabled")
                       and state["users"][user]["smb"]}
            if not (share["enabled"] and share["smb"] and allowed):
                continue
            writers = sorted(user for user, right in allowed.items() if right == "write")
            parts.extend((
                f"[{name}]", f"    path = {self.shares / share['directory']}",
                "    read only = yes", "    guest ok = no", "    access based share enum = yes",
                "    follow symlinks = no", "    wide links = no",
                "    valid users = " + " ".join(sorted(allowed)),
                "    write list = " + " ".join(writers), "",
            ))
        return "\n".join(parts)

    def publish(self, state, restart=False):
        original = self.config.read_text()
        candidate = self.samba_config(state)
        if original != candidate:
            self.b.atomic_write(self.config, candidate, 0o644)
            try:
                self.b.run("testparm", "-s")
                self.b.run("systemctl", "restart", "smbd") if restart else self.b.run("smbcontrol", "all", "reload-config")
            except (OSError, subprocess.TimeoutExpired):
                self.b.atomic_write(self.config, original, 0o644)
                self.b.run("smbcontrol", "all", "reload-config")
                raise
        elif restart:
            self.b.run("systemctl", "restart", "smbd")

    def restore(self):
        if not self.config.is_file():
            return
        self.smb_homes.mkdir(exist_ok=True)
        self.smb_homes.chmod(0o755)
        state = self.load()
        if not self.b.mounted():
            for name in state["users"]:
                self.revoke(name)
            return
        legacy = ["bitxwindows"] + [
            row["login"] for row in self.b.load_accounts().values() if row["role"] == "smb"
        ]
        for name in legacy:
            source = self.b.SMB_HOMES / name
            if source.is_dir() and not (self.smb_homes / name).is_dir():
                self.bind(source, self.smb_homes / name)
        for name, user in state["users"].items():
            try:
                self.revoke(name, home=False)
                self.expose(name, user, state)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.revoke(name, home=False)
                print(f"NAS restore failed user={name}", flush=True)

    def update_user(self, data, state, name):
        users = state["users"]
        previous = users.get(name)
        action = data["action"]
        if action == "nas_user_delete":
            if not previous:
                raise ValueError("Utilizatorul nu există.")
            if previous.get("smb_created"):
                self.b.run("smbpasswd", "-d", name)
            self.publish(state, restart=bool(previous.get("smb_created")))
            self.revoke(name)
            if previous.get("smb_created"):
                self.b.run("pdbedit", "-x", "-u", name)
            self.b.run("userdel", name)
            users.pop(name)
            for share in state["shares"].values():
                share["permissions"].pop(name, None)
            self.publish(state)
            self.save(state)
            return
        if action == "nas_user_create":
            if previous or self.b.get_local_user(name):
                raise ValueError("Numele este deja folosit, inclusiv de un cont anterior.")
        elif not previous:
            raise ValueError("Utilizatorul nu există.")
        smb, sftp, enabled = (data.get(option) is True for option in ("smb", "sftp", "enabled"))
        mode = data.get("auth", "key")
        if mode not in self.b.SFTP_MODES:
            raise ValueError("Metodă SFTP invalidă.")
        display = data.get("display", "")
        key = data.get("key", "")
        password = data.get("password", "")
        if not isinstance(display, str) or len(display) > 80 or not isinstance(key, str) or len(key) > 1024:
            raise ValueError("Date de utilizator invalide.")
        old = previous or {}
        key = self.b.key_line(key) if key.strip() and sftp and mode != "password" else old.get("key", "")
        if sftp and mode != "password" and not key:
            raise ValueError("Cheia publică Ed25519 este obligatorie.")
        needs_os_password = sftp and mode in self.b.PASSWORD_GROUPS
        if password or (smb and not old.get("smb_created")) or (needs_os_password and not old.get("os_password")):
            self.b.validate_password(password)
        user = dict(name=name, display=display, smb=smb, sftp=sftp, enabled=enabled,
                    auth=mode, key=key, smb_created=old.get("smb_created", False),
                    os_password=old.get("os_password", False))
        if not previous:
            home = self.b.storage_folder(self.homes, name)
            try:
                self.b.run("useradd", "--system", "--no-create-home", "--home-dir", "/home",
                           "--shell", "/usr/sbin/nologin", "--groups", "bitxdata,bx-sftp,bx-nas", name)
            except (OSError, subprocess.TimeoutExpired):
                home.rmdir()
                raise
        try:
            if old.get("smb_created"):
                self.b.run("smbpasswd", "-d", name)
                self.b.run("systemctl", "restart", "smbd")
            self.revoke(name)
            if password and (needs_os_password or old.get("os_password")):
                self.b.run("chpasswd", secret=f"{name}:{password}\n")
                self.b.run("usermod", "--lock", name)
                self.b.run("faillock", "--conf", self.b.FAILLOCK, "--user", name, "--reset")
                user["os_password"] = True
            if password and (smb or old.get("smb_created")):
                self.b.smb_secret(name, password, create=not user["smb_created"])
                user["smb_created"] = True
            users[name] = user
            self.expose(name, user, state)
            if user["smb_created"]:
                self.b.run("smbpasswd", "-e" if smb and enabled else "-d", name)
            self.publish(state)
            self.save(state)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.revoke(name)
            if user["smb_created"]:
                self.b.run("smbpasswd", "-d", name)
            user["enabled"] = False
            users[name] = user
            self.save(state)
            raise

    def update_share(self, data, state, name):
        shares = state["shares"]
        old = shares.get(name)
        new = self.valid_name(data.get("new_name") or name)
        if (new != name or old is None) and (
            new in shares or new in self.b.load_shares()
            or new in {"global", "homes", "home", "printers", "backup-admin", "windows-backup", "ipc"}
            or f"[{new}]" in self.b.run("testparm", "-s").lower().splitlines()
        ):
            raise ValueError("Numele share-ului este deja folosit sau rezervat.")
        if data["action"] == "nas_share_edit" and not old:
            raise ValueError("Share-ul nu există.")
        if data["action"] == "nas_share_create" and old:
            raise ValueError("Share-ul există deja.")
        permissions = data.get("permissions", {})
        if not isinstance(permissions, dict) or any(
            user not in state["users"] or value not in ("read", "write") for user, value in permissions.items()
        ):
            raise ValueError("Permisiuni invalide.")
        description = data.get("description", "")
        if not isinstance(description, str) or len(description) > 160:
            raise ValueError("Descrierea poate avea maximum 160 de caractere.")
        if not old:
            self.b.storage_folder(self.shares, new)
        else:
            self.folder(self.shares / old["directory"])
            del shares[name]
        shares[new] = dict(name=new, directory=old["directory"] if old else new,
                           description=description, permissions=permissions if old else {},
                           **{key: data.get(key) is True for key in ("sftp", "smb", "enabled")})
        affected = (set(old["permissions"]) if old else set()) | set(permissions)
        for user in sorted(affected):
            self.revoke(user, home=False)
        self.publish(state, restart=bool(old))
        self.save(state)
        for user in sorted(affected):
            self.expose(user, state["users"][user], state)

    def repository(self, data, state, name):
        if data["action"] == "nas_repository_remove":
            state["repositories"].pop(name, None)
            self.save(state)
            return
        share = next((item for item in state["shares"].values()
                      if item["directory"] == data.get("share")), None)
        if not share:
            raise ValueError("Alege un share existent.")
        directory = data.get("directory", "")
        if not isinstance(directory, str) or not self.b.REPO.fullmatch(directory) or directory in (".", ".."):
            raise ValueError("Alege un singur subdirector valid.")
        client = data.get("client", "")
        if not isinstance(client, str) or len(client) > 80:
            raise ValueError("Nume client invalid.")
        path = self.folder(self.shares / share["directory"]) / directory
        if not path.exists() and data.get("create") is True:
            path.mkdir()
        self.folder(path)
        if any(other != name and row["share"] == share["directory"] and row["directory"] == directory
               for other, row in state["repositories"].items()):
            raise ValueError("Directorul este deja asociat unui repository.")
        state["repositories"][name] = dict(name=name, share=share["directory"], directory=directory, client=client)
        self.save(state)

    def restic(self, row, command):
        path = self.folder(self.shares / row["share"] / row["directory"])
        credential = Path("/etc/bitx-console/restic") / (row["name"] + ".password")
        try:
            fd = os.open(credential, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as error:
            raise ValueError("Lipsește credențialul Restic autorizat.") from error
        try:
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_mode & 0o077 or not credential.is_file():
                raise ValueError("Fișierul de credențiale Restic trebuie să fie root-only.")
            secret = os.read(fd, 4097).decode().rstrip("\r\n")
            if not secret or len(secret) > 4096 or "\x00" in secret:
                raise ValueError("Credențial Restic invalid.")
            result = subprocess.run(
                ["/usr/bin/restic", "--no-lock", "--no-cache", "-r", str(path), *command],
                user="bitx-restic", group="bitx-restic", extra_groups=["bitxdata"],
                env={"PATH": "/usr/bin:/bin", "RESTIC_PASSWORD": secret},
                capture_output=True, text=True, timeout=20,
            )
            if result.returncode:
                raise ValueError("Restic a raportat o eroare. Verifică repository-ul și credențialul.")
            return result.stdout
        finally:
            os.close(fd)

    def size(self, path):
        try:
            self.folder(path)
            output = subprocess.run(["du", "-s", "-B1", "--", str(path)], timeout=2, capture_output=True, text=True, check=True)
            return int(output.stdout.split()[0])
        except (ValueError, OSError, subprocess.SubprocessError):
            return None

    def view(self, detail=""):
        state = self.load()
        mounted = self.b.mounted()
        if detail == "storage" and mounted:
            device = self.b.run("findmnt", "-n", "-o", "SOURCE", "--mountpoint", str(self.b.DISK))
            filesystem = self.b.run("findmnt", "-n", "-o", "FSTYPE", "--mountpoint", str(self.b.DISK))
            parent = self.b.run("lsblk", "-n", "-o", "PKNAME", device)
            state["volume"] = {"device": device, "filesystem": filesystem, "smart": "Indisponibil"}
            if parent and shutil.which("smartctl"):
                try:
                    health = self.b.run("smartctl", "-j", "-H", f"/dev/{parent}")
                    passed = json.loads(health).get("smart_status", {}).get("passed")
                    if isinstance(passed, bool):
                        state["volume"]["smart"] = "OK" if passed else "Problemă raportată"
                except (ValueError, OSError, subprocess.TimeoutExpired):
                    pass
        if detail == "settings":
            state["listeners"] = self.b.run("ss", "-H", "-ltn", "( sport = :22 or sport = :445 or sport = :8443 )")
        for user in state["users"].values():
            user.pop("key", None)
            user["access"] = {name: share["permissions"][user["name"]] for name, share in state["shares"].items()
                              if user["name"] in share["permissions"]}
        for share in state["shares"].values():
            path = self.shares / share["directory"]
            share["path"] = str(path)
            if mounted and detail == "shares":
                share["bytes"] = self.size(path)
        for row in state["repositories"].values():
            share = next((s for s in state["shares"].values() if s["directory"] == row["share"]), {})
            row["share_name"] = share.get("name", row["share"])
            row["access"] = {
                name: right for name, right in share.get("permissions", {}).items()
                if share.get("enabled") and state["users"].get(name, {}).get("enabled")
                and any(share.get(protocol) and state["users"][name].get(protocol) for protocol in ("smb", "sftp"))
            }
            if mounted and detail == "repositories":
                path = self.shares / row["share"] / row["directory"]
                row["bytes"] = self.size(path)
                row["initialized"] = (path / "config").is_file() and all(
                    (path / part).is_dir() for part in ("data", "keys", "index", "snapshots"))
        return state

    def action(self, data):
        self.require_disk()
        name = self.valid_name(data.get("name"))
        state = self.load()
        action = data["action"]
        if action.startswith("nas_user_"):
            self.update_user(data, state, name)
        elif action.startswith("nas_share_"):
            self.update_share(data, state, name)
        elif action == "nas_repository_check" or action == "nas_repository_snapshots":
            row = state["repositories"][name]
            command = ["check"] if action.endswith("check") else ["snapshots", "--latest", "1", "--json"]
            try:
                output = self.restic(row, command)
                if action.endswith("check"):
                    row["check"] = "Integritate verificată"
                else:
                    row["latest"] = max((entry["time"] for entry in json.loads(output)), default=None)
            except (ValueError, OSError, subprocess.TimeoutExpired):
                row["check" if action.endswith("check") else "snapshot_error"] = "Verificarea a eșuat sau lipsește credențialul."
                self.save(state)
                raise
            self.save(state)
        else:
            self.repository(data, state, name)

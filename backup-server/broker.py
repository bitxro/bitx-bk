"""Root-only account operations for the bitX web console."""

import base64
import json
import os
import pwd
import re
import socket
import socketserver
import stat
import struct
import subprocess
from pathlib import Path


STATE = Path("/var/lib/bitx-broker")
DISK = Path("/mnt/bitx-backup")
REPOS = DISK / "bitX-BACKUP/restic"
JAILS = Path("/srv/bitx-managed-sftp")
KEYS = Path("/etc/ssh/authorized_keys")
SOCKET = Path("/run/bitx-broker/control")
UUID = os.environ["BITX_DISK_UUID"]
NAME = re.compile(r"[a-z][a-z0-9-]{0,19}\Z")
REPO = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}\Z")
ROOT_DIR_MODE = 0o755


def run(*args, secret=None):
    result = subprocess.run(
        args, input=secret, text=True, capture_output=True, timeout=20,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )
    if result.returncode:
        raise OSError(f"{Path(args[0]).name} failed ({result.returncode})")
    return result.stdout.strip()


def mounted():
    try:
        return run("findmnt", "-n", "-o", "UUID", "--mountpoint", str(DISK)) == UUID
    except OSError:
        return False


def repos():
    if not mounted() or not REPOS.is_dir() or REPOS.resolve() != REPOS:
        return {}
    found = {}
    for entry in sorted(REPOS.iterdir()):
        if not REPO.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
            continue
        found[entry.name] = entry
    return found


def inventory():
    disks = None
    if mounted():
        info = os.statvfs(DISK)
        disks = {
            "total": info.f_blocks * info.f_frsize,
            "used": (info.f_blocks - info.f_bfree) * info.f_frsize,
            "free": info.f_bavail * info.f_frsize,
        }
    services = {}
    for name in ("ssh", "smbd", "qemu-guest-agent", "bitx-console", "bitx-broker"):
        try:
            services[name] = run("systemctl", "is-active", name)
        except OSError:
            services[name] = "inactive"
    repository_rows = []
    for name, path in repos().items():
        initialized = (path / "config").is_file() and (path / "snapshots").is_dir()
        latest = None
        password = Path("/etc/bitx-console/restic") / f"{name}.password"
        if not initialized:
            status = "Director fără repository Restic inițializat"
        elif not password.is_file() or password.is_symlink() or password.stat().st_uid != 0 or stat.S_IMODE(password.stat().st_mode) & 0o077:
            status = "Snapshoturi indisponibile fără credențial local"
        else:
            try:
                output = run(
                    "restic", "--no-lock", "--no-cache", "--repo", str(path),
                    "--password-file", str(password), "snapshots", "--latest", "1", "--json",
                )
                latest = max((snapshot["time"] for snapshot in json.loads(output)), default=None)
                status = "Verificat" if latest else "Fără snapshoturi"
            except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
                status = "Citirea snapshoturilor a eșuat"
        repository_rows.append({"name": name, "latest": latest, "status": status})
    return {"disk": disks, "services": services, "repositories": repository_rows}


def key_line(value):
    parts = value.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519" or len(parts[1]) > 96:
        raise ValueError("Este necesară o cheie publică SSH Ed25519.")
    data = base64.b64decode(parts[1], validate=True)
    if len(data) != 51 or data[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20":
        raise ValueError("Cheia publică SSH este invalidă.")
    return "restrict ssh-ed25519 " + base64.b64encode(data).decode("ascii") + "\n"


def atomic_write(path, value, mode=0o600):
    temp = path.with_suffix(".pending")
    with temp.open("w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), mode)
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


def load_accounts():
    path = STATE / "accounts.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save(accounts):
    atomic_write(STATE / "accounts.json", json.dumps(accounts, indent=2))


def os_name(name, role):
    return ("bxsf-" if role == "sftp" else "bxsm-") + name


def revoke_sftp(login, terminate=True):
    (KEYS / login).unlink(missing_ok=True)
    if terminate:
        try:
            run("pkill", "-KILL", "-u", str(pwd.getpwnam(login).pw_uid))
        except (KeyError, OSError):
            pass
    jail = JAILS / login
    if jail.is_dir():
        for target in jail.iterdir():
            if os.path.ismount(target) or subprocess.run(
                ["mountpoint", "-q", str(target)], check=False,
            ).returncode == 0:
                run("umount", str(target))
            target.rmdir()


def authorize_sftp(login, account):
    allowed = repos()
    if not account["repositories"] or not set(account["repositories"]) <= allowed.keys():
        raise ValueError("Selectează doar directoare disponibile pe HDD.")
    jail = JAILS / login
    jail.mkdir(mode=ROOT_DIR_MODE, exist_ok=True)
    jail.chmod(ROOT_DIR_MODE)
    for name in account["repositories"]:
        target = jail / name
        target.mkdir(mode=0o700)
        run("mount", "--bind", str(allowed[name]), str(target))
    atomic_write(KEYS / login, account["key"], 0o644)


def smb_secret(login, password, create):
    validate_password(password)
    args = ["smbpasswd", "-s"]
    if create:
        args.append("-a")
    run(*args, login, secret=f"{password}\n{password}\n")


def revoke_smb(login):
    run("smbpasswd", "-d", login)
    # Reset sessions for this service so an already authenticated client is disconnected.
    run("systemctl", "restart", "smbd")


def account_change(data):
    name, action = data.get("name", ""), data.get("action")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("Nume invalid. Folosește 1–20 de litere mici, cifre sau cratime.")
    accounts = load_accounts()
    old = accounts.get(name)
    if action == "create":
        role = data.get("role")
        if old or role not in ("sftp", "smb"):
            raise ValueError("Cont deja existent sau tip de acces invalid.")
        login = os_name(name, role)
        if any(entry.pw_name == login for entry in pwd.getpwall()):
            raise ValueError("Identitatea sistemului este deja folosită.")
        old = {"name": name, "login": login, "role": role, "enabled": False}
        if role == "sftp":
            configure_sftp(data, old)
        else:
            validate_password(data.get("password"))
        group = "bx-sftp" if role == "sftp" else "bx-smb"
        run("useradd", "--system", "--no-create-home", "--home-dir", "/", "--shell",
            "/usr/sbin/nologin", "--groups", f"bitxdata,{group}", login)
        try:
            if role == "sftp":
                authorize_sftp(login, old)
            else:
                smb_secret(login, data["password"], create=True)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            if role == "sftp":
                revoke_sftp(login)
            run("userdel", login)
            raise
        old["enabled"] = True
        accounts[name] = old
    elif old and old["role"] == data.get("role"):
        login, role = old["login"], old["role"]
        if action in ("disable", "delete"):
            if role == "sftp":
                revoke_sftp(login)
            elif old["enabled"]:
                revoke_smb(login)
            old["enabled"] = False
            save(accounts)
            if action == "delete":
                if role == "smb":
                    run("pdbedit", "-x", "-u", login)
                run("userdel", login)
                if role == "sftp":
                    (JAILS / login).rmdir()
                del accounts[name]
        elif action == "edit":
            if role == "sftp":
                changed = dict(old)
                configure_sftp(data, changed)
                revoke_sftp(login)
                old["enabled"] = False
                save(accounts)
                try:
                    authorize_sftp(login, changed)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    revoke_sftp(login)
                    raise
                changed["enabled"] = True
                accounts[name] = changed
            else:
                validate_password(data.get("password"))
                smb_secret(login, data["password"], create=False)
                run("smbpasswd", "-e", login)
                run("systemctl", "restart", "smbd")
                old["enabled"] = True
        else:
            raise ValueError("Operațiune necunoscută.")
    else:
        raise ValueError("Contul nu există.")
    save(accounts)


def validate_password(password):
    if not isinstance(password, str) or not 14 <= len(password) <= 128 or any(
        ch in password for ch in "\r\n\x00"
    ):
        raise ValueError("Parola SMB trebuie să aibă între 14 și 128 de caractere.")


def configure_sftp(data, account):
    names = data.get("repositories")
    if not isinstance(names, list) or not 1 <= len(names) <= 32 or any(
        not isinstance(name, str) or not REPO.fullmatch(name) for name in names
    ) or not set(names) <= repos().keys():
        raise ValueError("Selectează repository-uri din lista disponibilă.")
    value = data.get("key", "")
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError("Cheia publică SSH este invalidă.")
    account["key"] = key_line(value) if value.strip() else account.get("key")
    if not account["key"]:
        raise ValueError("Cheia publică este obligatorie.")
    account["repositories"] = sorted(set(names))


def restore():
    for entry in load_accounts().values():
        if entry["role"] != "sftp":
            continue
        login = entry["login"]
        try:
            revoke_sftp(login)
            if entry["enabled"]:
                authorize_sftp(login, entry)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            revoke_sftp(login)
            print(f"restore failed login={login}", flush=True)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        _, uid, _ = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != pwd.getpwnam("bitxweb").pw_uid:
            return
        self.request.settimeout(5)
        actor = action = name = "-"
        try:
            line = self.rfile.readline(16385)
            if len(line) > 16384:
                raise ValueError("Cerere prea mare.")
            data = json.loads(line)
            if not isinstance(data, dict):
                raise ValueError("Cerere invalidă.")
            actor, action, name = (data.get(field, "-") for field in ("actor", "action", "name"))
            if not isinstance(actor, str) or not NAME.fullmatch(actor):
                raise ValueError("Administrator invalid.")
            if action == "inventory":
                result = inventory()
            elif action == "list":
                result = [{k: v for k, v in row.items() if k != "key"} for row in load_accounts().values()]
            elif action in ("create", "edit", "disable", "delete"):
                account_change(data)
                result = None
                print(json.dumps({"actor": actor, "action": action, "target": name, "outcome": "ok"}), flush=True)
            else:
                raise ValueError("Operațiune neautorizată.")
            reply = {"ok": True, "data": result}
        except (ValueError, TypeError, OSError, KeyError, subprocess.TimeoutExpired) as error:
            if not isinstance(error, (ValueError, TypeError)):
                print(f"operation error type={type(error).__name__}", flush=True)
            print(json.dumps({"actor": str(actor)[:20], "action": str(action)[:20], "outcome": "denied"}), flush=True)
            message = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else "Operațiunea a fost refuzată sau nu a putut fi efectuată."
            reply = {"ok": False, "error": message}
        self.wfile.write(json.dumps(reply).encode("utf-8") + b"\n")


if __name__ == "__main__":
    STATE.mkdir(mode=0o700, exist_ok=True)
    JAILS.mkdir(mode=ROOT_DIR_MODE, exist_ok=True)
    JAILS.chmod(ROOT_DIR_MODE)
    restore()
    SOCKET.unlink(missing_ok=True)
    with socketserver.UnixStreamServer(str(SOCKET), Handler) as server:
        os.chown(SOCKET, 0, pwd.getpwnam("bitxweb").pw_gid)
        os.chmod(SOCKET, 0o660)
        server.serve_forever()

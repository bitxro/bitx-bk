"""Root-only account operations for the bitX web console."""

import base64
import grp
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
SMB_HOMES = DISK / "bitX-BACKUP/smb-homes"
SFTP_FILES = DISK / "bitX-BACKUP/sftp-files"
SFTP_HOMES = DISK / "bitX-BACKUP/sftp-homes"
SMB_SHARES = DISK / "bitX-BACKUP/smb-shares"
SHARES_CONFIG = Path("/etc/samba/bitx-managed-shares.conf")
SHARES_STATE = STATE / "shares.json"
JAILS = Path("/srv/bitx-managed-sftp")
KEYS = Path("/etc/ssh/authorized_keys")
SOCKET = Path("/run/bitx-broker/control")
UUID = os.environ["BITX_DISK_UUID"]
NAME = re.compile(r"[a-z][a-z0-9-]{0,19}\Z")
REPO = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}\Z")
SFTP_MODES = {"key", "password", "either"}
PASSWORD_GROUPS = {"password": "bx-sftp-pw", "either": "bx-sftp-either"}
FAILLOCK = "/etc/security/bitx-sftp-faillock.conf"
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


def file_destinations():
    if not mounted() or not SFTP_FILES.is_dir() or SFTP_FILES.resolve() != SFTP_FILES:
        return {}
    return {
        f"files-{entry.name}": entry for entry in sorted(SFTP_FILES.iterdir())
        if NAME.fullmatch(entry.name) and entry.is_dir() and not entry.is_symlink()
    }


def sftp_destinations():
    return file_destinations() | repos()


def load_shares():
    return json.loads(SHARES_STATE.read_text()) if SHARES_STATE.exists() else {}


def storage_folder(root, name):
    if not mounted() or root.parent.resolve() != root.parent:
        raise ValueError("Volumul de backup este indisponibil.")
    root.mkdir(exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Calea de stocare este invalidă.")
    target = root / name
    if target.exists() or target.is_symlink():
        raise ValueError("Directorul există deja.")
    target.mkdir()
    return target


def publish_shares(shares, disconnect=False):
    if not SHARES_CONFIG.is_file():
        raise OSError("Managed Samba include has not been installed.")
    previous = SHARES_CONFIG.read_text()
    sections = []
    for name, share in sorted(shares.items()):
        readers = share["readers"]
        writers = share["writers"]
        users = sorted(set(readers + writers))
        sections.extend((
            f"[{name}]", f"    path = {SMB_SHARES / name}",
            "    access based share enum = yes", "    guest ok = no",
            "    read only = yes", "    follow symlinks = no", "    wide links = no",
            "    valid users = " + " ".join(users) if users else "    available = no",
            "    write list = " + " ".join(writers), "",
        ))
    candidate = "\n".join(sections)
    try:
        atomic_write(SHARES_CONFIG, candidate, 0o644)
        run("testparm", "-s")
        if disconnect and candidate != previous:
            run("systemctl", "restart", "smbd")
        else:
            run("smbcontrol", "all", "reload-config")
        atomic_write(SHARES_STATE, json.dumps(shares, indent=2))
    except (OSError, subprocess.TimeoutExpired):
        atomic_write(SHARES_CONFIG, previous, 0o644)
        run("systemctl", "restart", "smbd")
        raise


def change_destination(data):
    name, action = data.get("name"), data.get("action")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("Numele trebuie să aibă maximum 20 de litere mici, cifre sau cratime.")
    if action == "sftp_directory":
        if f"files-{name}" in repos():
            raise ValueError("Un director Restic folosește deja acest nume.")
        storage_folder(SFTP_FILES, name)
        return
    shares = load_shares()
    known_logins = {row["login"] for row in load_accounts().values() if row["role"] == "smb"}
    if get_local_user("bitxwindows"):
        known_logins.add("bitxwindows")
    permissions = {}
    for scope in ("readers", "writers"):
        values = data.get(scope, [])
        if not isinstance(values, list) or any(not isinstance(v, str) or v not in known_logins for v in values):
            raise ValueError("Selectează numai conturi Samba existente.")
        permissions[scope] = sorted(set(values))
    if set(permissions["readers"]) & set(permissions["writers"]):
        raise ValueError("Alege un singur nivel de acces pentru fiecare cont.")
    target = None
    if action == "smb_share":
        defined = {match.lower() for match in re.findall(r"(?m)^\[([^\]]+)\]", run("testparm", "-s"))}
        if name in defined or name in shares or name in {"global", "homes", "printers"}:
            raise ValueError("Există deja un share cu acest nume.")
        target = storage_folder(SMB_SHARES, name)
    elif name not in shares:
        raise ValueError("Share-ul nu este gestionat de această pagină.")
    shares[name] = permissions
    try:
        publish_shares(shares, disconnect=action == "smb_share_edit")
    except (OSError, subprocess.TimeoutExpired):
        if target is not None:
            target.rmdir()
        raise


def get_local_user(login):
    try:
        return pwd.getpwnam(login)
    except KeyError:
        return None


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
    return {"disk": disks, "services": services, "repositories": repository_rows,
            "sftp_destinations": list(sftp_destinations()),
            "file_destinations": list(file_destinations()), "shares": load_shares()}


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


def set_sftp_group(login, mode):
    entry = pwd.getpwnam(login)
    groups = {grp.getgrgid(gid).gr_name for gid in os.getgrouplist(login, entry.pw_gid)}
    wanted = PASSWORD_GROUPS.get(mode)
    for group in set(PASSWORD_GROUPS.values()) & groups:
        if group != wanted:
            run("gpasswd", "-d", login, group)
    if wanted and wanted not in groups:
        run("usermod", "-aG", wanted, login)


def revoke_sftp(login):
    (KEYS / login).unlink(missing_ok=True)
    set_sftp_group(login, "key")
    run("usermod", "--lock", login)
    killed = subprocess.run(["pkill", "-KILL", "-u", str(pwd.getpwnam(login).pw_uid)], check=False)
    if killed.returncode not in (0, 1):
        raise OSError("Failed to terminate managed SFTP sessions.")
    jail = JAILS / login
    if jail.is_dir():
        for target in jail.iterdir():
            if os.path.ismount(target) or subprocess.run(
                ["mountpoint", "-q", str(target)], check=False,
            ).returncode == 0:
                run("umount", str(target))
            target.rmdir()


def authorize_sftp(login, account, password=""):
    allowed = sftp_destinations()
    selected = account["repositories"]
    if not set(selected) <= allowed.keys():
        raise ValueError("Selectează doar directoare disponibile pe HDD.")
    if selected:
        assignments = [(name, allowed[name]) for name in selected]
    else:
        home = SFTP_HOMES / login
        if account.get("home_provisioned"):
            if not mounted() or not home.is_dir() or home.resolve() != home:
                raise ValueError("Directorul personal SFTP este indisponibil.")
        else:
            home = storage_folder(SFTP_HOMES, login)
            account["home_provisioned"] = True
        assignments = [("home", home)]
    jail = JAILS / login
    jail.mkdir(mode=ROOT_DIR_MODE, exist_ok=True)
    jail.chmod(ROOT_DIR_MODE)
    for name, source in assignments:
        target = jail / name
        target.mkdir(mode=0o700)
        run("mount", "--bind", str(source), str(target))
    start = "/" if selected else "/home"
    if pwd.getpwnam(login).pw_dir != start:
        run("usermod", "--home", start, login)
    mode = account.get("auth", "key")
    if mode in PASSWORD_GROUPS:
        if password:
            run("chpasswd", secret=f"{login}:{password}\n")
            run("faillock", "--conf", FAILLOCK, "--user", login, "--reset")
        else:
            run("usermod", "--unlock", login)
        set_sftp_group(login, mode)
    if mode in ("key", "either"):
        atomic_write(KEYS / login, account["key"], 0o644)


def smb_secret(login, password, create):
    validate_password(password)
    args = ["smbpasswd", "-s"]
    if create:
        args.append("-a")
    run(*args, login, secret=f"{password}\n{password}\n")


def smb_home(login, existing=False):
    if not NAME.fullmatch(login) and (
        not login.startswith("bxsm-") or not NAME.fullmatch(login[5:])
    ):
        raise ValueError("Identitate SMB invalidă.")
    if not mounted() or SMB_HOMES.parent.resolve() != SMB_HOMES.parent:
        raise ValueError("Volumul pentru folderele personale este indisponibil.")
    SMB_HOMES.mkdir(exist_ok=True)
    if SMB_HOMES.is_symlink() or not SMB_HOMES.is_dir():
        raise ValueError("Calea pentru folderele personale este invalidă.")
    home = SMB_HOMES / login
    if home.is_symlink() or (home.exists() and not home.is_dir()):
        raise ValueError("Calea folderului personal este invalidă.")
    if home.exists() and not existing:
        raise ValueError("Numele are deja un folder personal păstrat. Alege alt nume.")
    home.mkdir(exist_ok=existing)
    return home


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
        login = name
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
        new_home = None
        try:
            if role == "sftp":
                authorize_sftp(login, old, data.get("password", ""))
            else:
                new_home = smb_home(login)
                smb_secret(login, data["password"], create=True)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            if role == "sftp":
                revoke_sftp(login)
            run("userdel", login)
            if new_home is not None:
                new_home.rmdir()
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
                    shares = load_shares()
                    changed = False
                    for share in shares.values():
                        for scope in ("readers", "writers"):
                            if login in share[scope]:
                                share[scope].remove(login)
                                changed = True
                    if changed:
                        publish_shares(shares, disconnect=True)
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
                    authorize_sftp(login, changed, data.get("password", ""))
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
    if not isinstance(names, list) or len(names) > 32 or any(
        not isinstance(name, str) or not REPO.fullmatch(name) for name in names
    ) or not set(names) <= sftp_destinations().keys():
        raise ValueError("Selectează destinații SFTP din lista disponibilă.")
    value = data.get("key", "")
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError("Cheia publică SSH este invalidă.")
    previous = account.get("auth", "key")
    mode = data.get("auth") or previous
    if mode not in SFTP_MODES:
        raise ValueError("Metodă de autentificare invalidă.")
    password = data.get("password", "")
    if mode in PASSWORD_GROUPS and (password or previous == "key" or not account["enabled"]):
        validate_password(password)
    if mode == "password":
        account.pop("key", None)
    else:
        account["key"] = key_line(value) if value.strip() else account.get("key")
        if not account["key"]:
            raise ValueError("Cheia publică este obligatorie.")
    account["auth"] = mode
    account["repositories"] = sorted(set(names))


def restore():
    if mounted():
        homes = ["bitxwindows"]
        homes.extend(entry["login"] for entry in load_accounts().values() if entry["role"] == "smb")
        for login in homes:
            if any(entry.pw_name == login for entry in pwd.getpwall()):
                try:
                    smb_home(login, existing=True)
                except (OSError, ValueError):
                    print(f"SMB home unavailable login={login}", flush=True)
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
            elif action in ("sftp_directory", "smb_share", "smb_share_edit"):
                change_destination(data)
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

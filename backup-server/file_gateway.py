"""Isolated local gateway for NAS file operations and read-only Restic jobs."""

from contextlib import contextmanager
import ctypes
import fcntl
import itertools
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import secrets
import shutil
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import tarfile
import threading
import time

import broker


NAS = broker.NAS
GATEWAY = Path("/run/bitx-files/control")
STATE = Path("/var/lib/bitx-files")
GRANTS = STATE / "grants.json"
JOBS = STATE / "jobs"
PAGE = 100
LIMIT = 8 * 2**30
NOFOLLOW = os.O_NOFOLLOW | os.O_CLOEXEC
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW
LOCK = threading.Lock()
RESTIC = threading.BoundedSemaphore(1)
RENAME = ctypes.CDLL(None, use_errno=True).renameat2


def parts(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Cale invalidă.")
    if value == "":
        return []
    result = value.split("/")
    if len(result) > 64 or any(
        not name or name in (".", "..") or name.startswith(".bitx-transfer-")
        or re.search(r'[\\:\x00-\x1f\x7f*?"<>|]', name)
        or len(os.fsencode(name)) > 255
        for name in result
    ):
        raise ValueError("Cale invalidă.")
    return result


def leaf(value):
    parsed = parts(value)
    if len(parsed) != 1:
        raise ValueError("Introdu un singur nume de fișier sau director.")
    return parsed[0]


def checked_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Snapshot invalid.")
    return value


def json_file(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, value):
    broker.atomic_write(path, json.dumps(value, ensure_ascii=False))


def principal(actor, identity):
    state = NAS.load()
    user = state["users"].get(actor)
    if not user or not user["enabled"] or identity != user.get("file_identity", "existing"):
        raise ValueError("Contul de fișiere este indisponibil.")
    return state


def roots(actor, state):
    result = {"home": (NAS.homes / actor, "write")}
    for name, share in state["shares"].items():
        access = share["permissions"].get(actor)
        if share["enabled"] and access in ("read", "write"):
            result[f"share:{name}"] = (NAS.shares / share["directory"], access)
    return result


def protected(state, base, names):
    path = base.joinpath(*names)
    for row in state["repositories"].values():
        repo = NAS.shares / row["share"] / row["directory"]
        if path == repo or path in repo.parents or repo in path.parents:
            return True
    return False


def volume():
    NAS.require_disk()
    return NAS.root.stat().st_dev


@contextmanager
def directory(base, items):
    dev = volume()
    if base.resolve() != base:
        raise ValueError("Directorul nu este disponibil.")
    fd = os.open(base, DIRECTORY)
    try:
        for name in items:
            if os.fstat(fd).st_dev != dev:
                raise ValueError("Directorul nu aparține volumului de backup.")
            previous = fd
            fd = os.open(name, DIRECTORY, dir_fd=previous)
            os.close(previous)
        if os.fstat(fd).st_dev != dev:
            raise ValueError("Directorul nu aparține volumului de backup.")
        yield fd
    finally:
        os.close(fd)


def metadata(info, name):
    kind = "dir" if stat.S_ISDIR(info.st_mode) else "file"
    return dict(name=name, type=kind, size=info.st_size if kind == "file" else None, mtime=info.st_mtime)


def list_directory(fd, offset, term, sort):
    entries = []
    for entry in os.scandir(fd):
        if entry.name.startswith(".bitx-transfer-") or term not in entry.name.casefold():
            continue
        info = entry.stat(follow_symlinks=False)
        if info.st_dev == os.fstat(fd).st_dev and (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            entries.append(metadata(info, entry.name))
        if len(entries) > 25000:
            raise ValueError("Director prea mare pentru sortare. Restrânge căutarea.")
    key = {"name": lambda x: x["name"].casefold(), "size": lambda x: x["size"] or 0,
           "mtime": lambda x: x["mtime"], "type": lambda x: x["type"]}[sort]
    entries.sort(key=lambda x: (x["type"] != "dir", key(x), x["name"].casefold()))
    return dict(entries=entries[offset:offset + PAGE], total=len(entries))


def regular(fd, name):
    file = os.open(name, os.O_RDONLY | os.O_NONBLOCK | NOFOLLOW, dir_fd=fd)
    info = os.fstat(file)
    if not stat.S_ISREG(info.st_mode) or info.st_dev != os.fstat(fd).st_dev:
        os.close(file)
        raise ValueError("Fișierul nu poate fi deschis.")
    return os.fdopen(file, "rb"), info


def restic_directory(fd):
    for name in ("config", "data", "keys", "index", "snapshots"):
        try:
            if stat.S_ISLNK(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                return False
        except FileNotFoundError:
            return False
    return True


def safe_subtree(fd, name):
    scanned = 0
    for _, dirs, files, child in os.fwalk(name, dir_fd=fd, follow_symlinks=False):
        scanned += len(dirs) + len(files)
        if scanned > 25000 or os.fstat(child).st_dev != os.fstat(fd).st_dev:
            raise ValueError("Directorul este prea mare sau conține o montare separată.")
        if restic_directory(child):
            raise ValueError("Directorul conține un repository Restic.")


def copy_tree(source, target, remaining):
    if restic_directory(source):
        raise ValueError("Directorul conține un repository Restic.")
    for item in os.scandir(source):
        name = leaf(item.name)
        info = item.stat(follow_symlinks=False)
        if info.st_dev != os.fstat(source).st_dev:
            raise ValueError("Directorul conține o montare separată.")
        if stat.S_ISDIR(info.st_mode):
            os.mkdir(name, dir_fd=target)
            src = os.open(name, DIRECTORY, dir_fd=source)
            dst = os.open(name, DIRECTORY, dir_fd=target)
            try:
                remaining = copy_tree(src, dst, remaining)
            finally:
                os.close(src)
                os.close(dst)
        elif stat.S_ISREG(info.st_mode):
            if info.st_size > remaining:
                raise ValueError("Copierea depășește limita de 8 GiB.")
            handle, opened = regular(source, name)
            with handle, incoming(target, name) as output:
                transfer(handle, output, opened.st_size)
            remaining -= opened.st_size
        else:
            raise ValueError("Directorul conține un tip de fișier neacceptat.")
    return remaining


def rename(fd, name, dest, new):
    if RENAME(fd, os.fsencode(name), dest, os.fsencode(new), 1):
        raise OSError(ctypes.get_errno(), "Could not publish without replacing an existing target")


@contextmanager
def incoming(fd, target):
    name = ".bitx-transfer-" + secrets.token_hex(16)
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(handle, "wb") as output:
            yield output
            output.flush()
            os.fsync(output.fileno())
        rename(fd, name, fd, target)
    finally:
        try:
            os.unlink(name, dir_fd=fd)
        except FileNotFoundError:
            pass


def transfer(source, target, remaining):
    while remaining:
        chunk = source.read(min(1024 * 1024, remaining))
        if not chunk:
            raise ValueError("Transferul a fost întrerupt.")
        target.write(chunk)
        remaining -= len(chunk)


def root_for(actor, state, key, write=False):
    path, access = roots(actor, state).get(key, (None, None))
    if path is None or (write and access != "write"):
        raise ValueError("Acces refuzat pentru această destinație.")
    return path


def reply(handler, data):
    handler.wfile.write(json.dumps({"ok": True, "data": data}, ensure_ascii=True).encode() + b"\n")


def stream_reply(handler, data):
    reply(handler, data)
    handler.streaming = True


def file_action(handler, data, who, state):
    action = data["action"]
    if action == "roots":
        volume()
        return [{"id": key, "access": value[1]} for key, value in roots(who, state).items()]
    write = action not in ("list", "download", "copy")
    base = root_for(who, state, data.get("root"), write)
    location = parts(data.get("path", ""))
    with directory(base, location) as fd:
        if action == "list":
            offset = max(0, min(25000, int(data.get("offset", 0))))
            sort = data.get("sort", "name")
            if sort not in ("name", "size", "mtime", "type"):
                raise ValueError("Sortare invalidă.")
            return list_directory(fd, offset, str(data.get("q", ""))[:100].casefold(), sort)
        name = leaf(data.get("name"))
        if write and protected(state, base, location + [name]):
            raise ValueError("Destinația intersectează o asociere Restic protejată.")
        if write:
            for index in range(len(location) + 1):
                with directory(base, location[:index]) as parent:
                    if restic_directory(parent):
                        raise ValueError("Directorul aparține unui repository Restic.")
        if action == "download":
            source, info = regular(fd, name)
            with source:
                stream_reply(handler, metadata(info, name))
                transfer(source, handler.wfile, info.st_size)
            return None
        if action == "mkdir":
            os.mkdir(name, dir_fd=fd)
        elif action == "upload":
            size = int(data.get("size", -1))
            disk = os.fstatvfs(fd)
            if not 0 <= size <= LIMIT or size > disk.f_bavail * disk.f_frsize:
                raise ValueError("Fișier prea mare sau spațiu insuficient.")
            reply(handler, {"continue": True})
            with incoming(fd, name) as output:
                transfer(handler.rfile, output, size)
        elif action == "delete":
            if data.get("confirm") != name:
                raise ValueError("Confirmă numele exact înainte de ștergere.")
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                safe_subtree(fd, name)
                shutil.rmtree(name, dir_fd=fd)
            elif stat.S_ISREG(info.st_mode):
                os.unlink(name, dir_fd=fd)
            else:
                raise ValueError("Tip de fișier neacceptat.")
        elif action in ("rename", "copy", "move"):
            target = leaf(data.get("target"))
            dest = base if action == "rename" else root_for(who, state, data.get("dest_root"), True)
            extra = location if action == "rename" else parts(data.get("dest", ""))
            if protected(state, dest, extra + [target]):
                raise ValueError("Destinația intersectează o asociere Restic protejată.")
            with directory(dest, extra) as dest_fd:
                for index in range(len(extra) + 1):
                    with directory(dest, extra[:index]) as parent:
                        if restic_directory(parent):
                            raise ValueError("Directorul aparține unui repository Restic.")
                if action in ("rename", "move"):
                    if stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                        safe_subtree(fd, name)
                    rename(fd, name, dest_fd, target)
                elif stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                    original = base.joinpath(*location, name)
                    destination = dest.joinpath(*extra)
                    if original == destination or original in destination.parents or protected(state, base, location + [name]):
                        raise ValueError("Directorul nu poate fi copiat în el însuși sau dintr-un repository.")
                    temp = ".bitx-transfer-" + secrets.token_hex(16)
                    os.mkdir(temp, dir_fd=dest_fd)
                    try:
                        with directory(base, location + [name]) as source:
                            target_fd = os.open(temp, DIRECTORY, dir_fd=dest_fd)
                            try:
                                copy_tree(source, target_fd, LIMIT)
                            finally:
                                os.close(target_fd)
                        rename(dest_fd, temp, dest_fd, target)
                    finally:
                        if os.path.lexists(destination / temp):
                            shutil.rmtree(temp, dir_fd=dest_fd)
                else:
                    source, info = regular(fd, name)
                    with source, incoming(dest_fd, target) as target_file:
                        transfer(source, target_file, info.st_size)
        else:
            raise ValueError("Operațiune necunoscută.")
    return {"changed": True}


def allowed_repo(actor, state, name):
    grants = json_file(GRANTS, {})
    row = state["repositories"].get(name)
    if not row or grants.get(name, {}).get("repo") != row.get("file_identity", "existing") or (
        grants[name]["users"].get(actor) != state["users"][actor].get("file_identity", "existing")
    ):
        raise ValueError("Acces Backup Explorer refuzat.")
    return row


@contextmanager
def restic(row, args):
    volume()
    with RESTIC, open(STATE / "restic.lock", "ab") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Restic rulează deja o operațiune. Reîncearcă.") from error
        source = NAS.folder(NAS.shares / row["share"] / row["directory"])
        credential = Path("/etc/bitx-console/restic") / (row["name"] + ".password")
        try:
            fd = os.open(credential, os.O_RDONLY | NOFOLLOW)
        except OSError as error:
            raise ValueError("Credențialul Restic lipsește.") from error
        with os.fdopen(fd, "rb") as password_file:
            info = os.fstat(password_file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                raise ValueError("Credențialul Restic trebuie păstrat root-only.")
            try:
                password = password_file.read(4097).decode().rstrip("\r\n")
            except UnicodeError as error:
                raise ValueError("Credențial Restic invalid.") from error
        if not password or len(password) > 4096 or "\x00" in password:
            raise ValueError("Credențial Restic invalid.")
        uid = pwd.getpwnam("bitx-restic").pw_uid
        import grp
        gid = grp.getgrnam("bitxdata").gr_gid
        command = [
            "/usr/bin/timeout", "--kill-after=5", "180", "/usr/bin/bwrap", "--unshare-all",
            "--uid", str(uid), "--gid", str(gid), "--die-with-parent",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64", "--dev", "/dev", "--proc", "/proc",
            "--ro-bind", str(source), "/repo", "--chdir", "/",
            "/usr/bin/restic", "--no-lock", "--no-cache", "-r", "/repo", *args,
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env={"PATH": "/usr/bin:/bin", "RESTIC_PASSWORD": password,
                                        "GOMEMLIMIT": "128MiB", "GOMAXPROCS": "2"})
        try:
            yield process.stdout
            if process.wait():
                raise ValueError("Restic nu a putut citi repository-ul. Verifică starea și credențialul.")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=8)


def read_limited(stream, size=2 * 1024 * 1024):
    content = stream.read(size + 1)
    if len(content) > size:
        raise ValueError("Rezultatul depășește limita de metadate.")
    return content


def restic_tree(row, snapshot, path, offset, query):
    command = ["ls", "--json"]
    if query:
        command.append("--recursive")
    command.extend(("--", snapshot))
    if path:
        command.append("/" + path)
    records = []
    with restic(row, command) as stream:
        for line in itertools.islice(stream, 200002):
            if len(line) > 65536:
                raise ValueError("Metadate Restic prea mari.")
            entry = json.loads(line)
            name = entry.get("name", "")
            if entry.get("type") not in ("dir", "file") or (query and query not in name.casefold()):
                continue
            raw_path = entry["path"].lstrip("/")
            try:
                parts(raw_path)
            except ValueError:
                continue
            if raw_path != path and (query or raw_path.rpartition("/")[0] == path):
                records.append({k: entry.get(k) for k in ("name", "path", "mtime", "size", "type")})
            if len(records) >= offset + PAGE + 1:
                break
        else:
            return {"entries": records[offset:], "more": False}
        # Drain the remaining output without keeping it in memory.
        for _ in stream:
            pass
    return {"entries": records[offset:offset + PAGE], "more": len(records) > offset + PAGE}


def job_path(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Job invalid.")
    return JOBS / (value + ".json")


def restic_action(handler, data, actor, state):
    action, name = data["action"], data.get("repo")
    if action == "repositories":
        grants = json_file(GRANTS, {})
        return [{"name": name, "share": row["share"], "client": row["client"],
                 "latest": row.get("latest"), "state": row.get("check"),
                 "size": NAS.size(NAS.shares / row["share"] / row["directory"])}
                for name, row in state["repositories"].items()
                if grants.get(name, {}).get("repo") == row.get("file_identity", "existing")
                and grants[name]["users"].get(actor) == state["users"][actor].get("file_identity", "existing")]
    if action in ("job", "jobs"):
        fields = ("id", "status", "message", "target", "restored")
        if action == "jobs":
            identity = state["users"][actor].get("file_identity", "existing")
            return [{key: job[key] for key in fields} for path in sorted(JOBS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:300]
                    if (job := json_file(path, {})).get("actor") == actor and job.get("identity") == identity][:30]
        job = json_file(job_path(data.get("id")), {})
        if job.get("actor") != actor or job.get("identity") != state["users"][actor].get("file_identity", "existing"):
            raise ValueError("Job indisponibil.")
        return {key: job[key] for key in fields}
    row = allowed_repo(actor, state, name)
    if action == "snapshots":
        with restic(row, ["snapshots", "--json"]) as stream:
            snapshots = json.loads(read_limited(stream))
        return [{"id": item["id"], "time": item["time"], "hostname": item.get("hostname")}
                for item in snapshots]
    snapshot = checked_id(data.get("snapshot"))
    path = "/".join(parts(data.get("path", "")))
    if action == "tree":
        return restic_tree(row, snapshot, path, max(0, min(10000, int(data.get("offset", 0)))),
                           str(data.get("q", ""))[:100].casefold())
    if action == "diff":
        other = checked_id(data.get("other"))
        with restic(row, ["diff", "--", snapshot, other]) as stream:
            return read_limited(stream, 128 * 1024).decode(errors="replace")
    if action == "archive":
        names = data.get("paths")
        if not isinstance(names, list) or not 2 <= len(names) <= 20:
            raise ValueError("Selectează între 2 și 20 de fișiere pentru arhivă.")
        requested = {"/".join(parts(name)) for name in names}
        found = {}
        with restic(row, ["ls", "--json", "--", snapshot]) as stream:
            for line in stream:
                entry = json.loads(line)
                filename = entry.get("path", "").lstrip("/")
                if filename in requested and entry.get("type") == "file":
                    found[filename] = int(entry["size"])
        if len(found) != len(requested) or sum(found.values()) > LIMIT:
            raise ValueError("Arhiva multiplă acceptă doar fișiere, în limita a 8 GiB.")
        stream_reply(handler, {"name": "snapshot-" + snapshot[:8] + ".tar"})
        with tarfile.open(fileobj=handler.wfile, mode="w|") as archive:
            for filename, size in sorted(found.items()):
                entry = tarfile.TarInfo(filename)
                entry.size = size
                with restic(row, ["dump", "--", snapshot, "/" + filename]) as stream:
                    archive.addfile(entry, stream)
        return None
    if not path:
        raise ValueError("Selectează un fișier sau un director.")
    if action == "dump":
        filename = leaf(PurePosixPath(path).name)
        with restic(row, ["dump", "--", snapshot, "/" + path]) as stream:
            first = stream.read(65536)
            if first:
                stream_reply(handler, {"name": filename + (".tar" if data.get("kind") == "dir" else "")})
                handler.wfile.write(first)
                shutil.copyfileobj(stream, handler.wfile, 1024 * 1024)
        if not first:
            stream_reply(handler, {"name": filename, "size": 0})
        return None
    if action == "restore":
        target = leaf(data.get("target"))
        if data.get("confirm") != target:
            raise ValueError("Confirmă numele directorului nou.")
        base = root_for(actor, state, data.get("root"), True)
        if protected(state, base, [target]):
            raise ValueError("Destinație Restic protejată.")
        with directory(base, []) as fd:
            disk = os.fstatvfs(fd)
            if disk.f_bavail * disk.f_frsize < 64 * 2**20 or os.path.lexists(base / target):
                raise ValueError("Spațiu insuficient sau directorul există deja.")
        with LOCK:
            active = sum(json_file(path, {}).get("status") in ("queued", "running") for path in JOBS.glob("*.json"))
            if active >= 4:
                raise ValueError("Coada de restaurări este plină.")
            ident = secrets.token_hex(16)
            job = dict(id=ident, actor=actor, identity=state["users"][actor].get("file_identity", "existing"),
                       repo=name, snapshot=snapshot, path=path,
                       target=target, root=data["root"], kind=data.get("kind"),
                       status="queued", restored=0, message="În așteptare")
            save(job_path(ident), job)
        return {"id": ident}
    raise ValueError("Operațiune Backup Explorer invalidă.")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        _, uid, _ = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != pwd.getpwnam("bitxweb").pw_uid:
            return
        self.request.settimeout(20)
        self.streaming = False
        action = actor = "-"
        try:
            data = json.loads(self.rfile.readline(16385))
            action, actor = data["action"], data["actor"]
            if not broker.NAME.fullmatch(actor):
                raise ValueError("Cont invalid.")
            if action in ("grants", "set_grants", "identity"):
                if data.get("role") != "admin":
                    raise ValueError("Acces administrativ necesar.")
                with LOCK:
                    grants = json_file(GRANTS, {})
                    state = NAS.load()
                    if action == "identity":
                        user = state["users"].get(data.get("name"))
                        if not user or not user["enabled"]:
                            raise ValueError("Contul NAS nu este activ.")
                        reply(self, user.get("file_identity", "existing"))
                        return
                    if action == "set_grants":
                        repo, users = data["repo"], data["users"]
                        if repo not in state["repositories"] or not isinstance(users, list) or not set(users) <= state["users"].keys():
                            raise ValueError("Asociere sau utilizator invalid.")
                        grants[repo] = {"repo": state["repositories"][repo].get("file_identity", "existing"),
                                        "users": {u: state["users"][u].get("file_identity", "existing") for u in set(users)}}
                        save(GRANTS, grants)
                    reply(self, {repo: list(value["users"]) for repo, value in grants.items()
                                 if repo in state["repositories"] and value["repo"] == state["repositories"][repo].get("file_identity", "existing")})
            else:
                if data.get("role") != "files":
                    raise ValueError("Conectează-te cu un cont de fișiere autorizat.")
                state = principal(actor, data.get("identity"))
                method = file_action if action in {"roots", "list", "mkdir", "delete", "upload", "download", "copy", "move", "rename"} else restic_action
                result = method(self, data, actor, state)
                if result is not None:
                    reply(self, result)
            print(json.dumps({"actor": actor, "action": action, "result": "ok"}), flush=True)
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
            print(json.dumps({"actor": actor, "action": action, "result": "denied"}), flush=True)
            error = sys.exc_info()[1]
            message = str(error) if type(error) is ValueError else "Operațiunea a eșuat: acces, cale, destinație existentă sau serviciu indisponibil."
            try:
                if self.streaming:
                    return
                self.wfile.write(json.dumps({"ok": False, "error": message}).encode() + b"\n")
            except OSError:
                pass


def worker():
    for path in JOBS.glob("*.json"):
        job = json_file(path, {})
        if job.get("status") == "running":
            job.update(status="interrupted", message="Întrerupt. Pornește o restaurare nouă.")
            save(path, job)
    while True:
        for path in sorted(JOBS.glob("*.json")):
            job = json_file(path, {})
            if job.get("status") == "queued":
                run_job(path, job)
        time.sleep(1)


def run_job(record, job):
    job.update(status="running", message="Restaurare în curs")
    save(record, job)
    try:
        state = principal(job["actor"], job["identity"])
        row = allowed_repo(job["actor"], state, job["repo"])
        base = root_for(job["actor"], state, job["root"], True)
        if protected(state, base, [job["target"]]):
            raise ValueError("Destinație Restic protejată.")
        with directory(base, []) as root:
            os.mkdir(job["target"], dir_fd=root)
        restore_root = base / job["target"]
        path = job["path"]
        with restic(row, ["dump", "--", job["snapshot"], "/" + path]) as stream:
            if job["kind"] == "dir":
                with tarfile.open(fileobj=stream, mode="r|*") as archive:
                    for item in archive:
                        name = item.name.removeprefix("./").rstrip("/")
                        if not name or name == ".":
                            continue
                        selected = parts(path)
                        member = parts(name)
                        if member[:len(selected)] == selected:
                            entries = member
                        elif member[0] == selected[-1]:
                            entries = selected[:-1] + member
                        else:
                            raise ValueError("Arhiva conține o cale în afara directorului selectat.")
                        if not (item.isfile() or item.isdir()):
                            continue
                        write_restored(restore_root, entries, archive.extractfile(item) if item.isfile() else None,
                                       item.size, job, record)
            else:
                entries = parts(path)
                with mkdirs(restore_root, entries[:-1]) as fd, incoming(fd, entries[-1]) as output:
                    while chunk := stream.read(1024 * 1024):
                        output.write(chunk)
                        job["restored"] += len(chunk)
                        if job["restored"] % (64 * 2**20) == 0:
                            save(record, job)
        job.update(status="complete", message="Restaurare finalizată")
    except (ValueError, OSError, KeyError, tarfile.TarError, subprocess.SubprocessError):
        job.update(status="failed", message="Restaurarea a eșuat. Verifică permisiunile, spațiul și credențialul. Un director parțial poate rămâne.")
    save(record, job)


@contextmanager
def mkdirs(base, dirs):
    for index, name in enumerate(dirs):
        with directory(base, dirs[:index]) as parent:
            try:
                os.mkdir(name, dir_fd=parent)
            except FileExistsError:
                pass
    with directory(base, dirs) as out:
        yield out


def write_restored(base, path, source, size, job, record):
    if source is None:
        with mkdirs(base, path):
            pass
    else:
        with source, mkdirs(base, path[:-1]) as fd, incoming(fd, path[-1]) as output:
            disk = os.fstatvfs(fd)
            if size > disk.f_bavail * disk.f_frsize:
                raise ValueError("Spațiu insuficient.")
            transfer(source, output, size)
        job["restored"] += size
        save(record, job)


class Gateway(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    slots = threading.BoundedSemaphore(5)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


if __name__ == "__main__":
    JOBS.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sys.argv[1:] == ["worker"]:
        worker()
    else:
        GATEWAY.unlink(missing_ok=True)
        with Gateway(str(GATEWAY), Handler) as server:
            os.chown(GATEWAY, 0, pwd.getpwnam("bitxweb").pw_gid)
            os.chmod(GATEWAY, 0o660)
            server.serve_forever()

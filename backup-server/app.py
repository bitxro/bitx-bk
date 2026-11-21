"""Unprivileged HTTPS application for bitX Backup Server."""
import argparse
import getpass
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import quote

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.http import dump_options_header
from werkzeug.security import check_password_hash, generate_password_hash


ROOT = "/var/lib/bitx-console"
NAME = re.compile(r"[a-z][a-z0-9-]{0,19}\Z")
LAN = ipaddress.IPv4Network(os.environ.get("BITX_LAN", "127.0.0.0/8"))
TOKEN_TTL = 1800
PAGES = ("Dashboard", "Storage", "Repositories", "Users", "Shares", "Activity", "Settings", "File Manager")
app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=16384, SECRET_KEY=open(f"{ROOT}/session.key", "rb").read(),
    SESSION_COOKIE_NAME="__Host-bitx", SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
)
DUMMY_PASSWORD = generate_password_hash(secrets.token_urlsafe())


@contextmanager
def database():
    db = sqlite3.connect(f"{ROOT}/console.sqlite")
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize():
    with database() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS admins (
              name TEXT PRIMARY KEY, password TEXT NOT NULL, enabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY, name TEXT NOT NULL, expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (
              address TEXT NOT NULL, name TEXT NOT NULL, at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (
              at TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL,
              target TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS file_logins (
              name TEXT PRIMARY KEY, password TEXT NOT NULL, enabled INTEGER NOT NULL, identity TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS file_sessions (token TEXT PRIMARY KEY);
        """)


def audit(db, actor, event, target, result):
    db.execute("INSERT INTO audit VALUES (?,?,?,?,?)", (
        datetime.now(timezone.utc).isoformat(timespec="seconds"), actor, event, target, result,
    ))
    app.logger.warning("audit %s", json.dumps({
        "actor": actor, "event": event, "target": target, "result": result,
    }))


def digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def validate_password(password):
    if not 14 <= len(password) <= 128 or any(ch in password for ch in "\r\n\x00"):
        raise ValueError("Parola trebuie să aibă între 14 și 128 de caractere.")


@app.before_request
def guard():
    try:
        allowed = ipaddress.ip_address(request.remote_addr) in LAN
    except ValueError:
        allowed = False
    if not allowed:
        abort(403)
    g.user = None
    g.role = None
    g.identity = None
    if "token" in session:
        with database() as db:
            token = digest(session["token"])
            file_session = db.execute("SELECT 1 FROM file_sessions WHERE token=?", (token,)).fetchone()
            if file_session:
                row = db.execute("""SELECT name, identity FROM sessions JOIN file_logins USING(name)
                    WHERE token=? AND expires>? AND enabled=1""", (token, int(time.time()))).fetchone()
                if row:
                    g.identity = row["identity"]
                    g.role = "files"
            else:
                row = db.execute("""SELECT name FROM sessions JOIN admins USING(name)
                    WHERE token=? AND expires>? AND enabled=1""", (token, int(time.time()))).fetchone()
                if row:
                    g.role = "admin"
        if row:
            g.user = row["name"]
    if request.method == "POST":
        csrf = session.get("csrf")
        provided = request.headers.get("X-CSRF-Token", "") if request.endpoint in ("file_api", "file_upload") else request.form.get("csrf", "")
        if not csrf or not secrets.compare_digest(csrf, provided):
            if request.endpoint == "login" and not g.user:
                session["csrf"] = secrets.token_urlsafe()
                return render_template(
                    "console.html", page="Login",
                    error="Formularul de autentificare a expirat. Introdu din nou utilizatorul și parola.",
                ), 400
            abort(403)
    if request.endpoint not in ("login", "static") and not g.user:
        return redirect(url_for("login"))
    if g.role == "files" and request.endpoint not in (
        "login", "logout", "static", "file_manager", "file_api", "file_upload", "file_download",
    ):
        return redirect(url_for("file_manager"))
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe()


@app.after_request
def protect(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self'; img-src 'self' blob:; script-src 'self'; connect-src 'self'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("file_manager") if g.role == "files" else url_for("page", name="dashboard"))
    if request.method == "POST":
        supplied = request.form.get("name", "")
        name = supplied if NAME.fullmatch(supplied) else "-"
        password = request.form.get("password", "")
        role = "files" if request.form.get("role") == "files" else "admin"
        address = request.remote_addr
        now = int(time.time())
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE at < ?", (now - 900,))
            attempts = db.execute(
                "SELECT count(*) FROM attempts WHERE address=? OR name=?", (address, name)
            ).fetchone()[0]
            if attempts >= 8:
                return render_template("console.html", page="Login", error="Prea multe încercări. Reîncearcă peste 15 minute."), 429
            table = "file_logins" if role == "files" else "admins"
            row = db.execute(f"SELECT password, enabled FROM {table} WHERE name=?", (name,)).fetchone()
            verified = len(password) <= 128 and check_password_hash(row["password"] if row else DUMMY_PASSWORD, password)
            if not (verified and row and row["enabled"]):
                db.execute("INSERT INTO attempts VALUES (?,?,?)", (address, name, now))
                audit(db, name, "login", "-", "denied")
                return render_template("console.html", page="Login", error="Autentificare nereușită."), 401
            db.execute("DELETE FROM attempts WHERE address=? AND name=?", (address, name))
            db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
            db.execute("DELETE FROM file_sessions WHERE token NOT IN (SELECT token FROM sessions)")
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (digest(token), name, now + TOKEN_TTL))
            if role == "files":
                db.execute("INSERT INTO file_sessions VALUES (?)", (digest(token),))
            audit(db, name, "login", "-", "ok")
        session.clear()
        session.update(token=token, csrf=secrets.token_urlsafe())
        return redirect(url_for("file_manager") if role == "files" else url_for("page", name="dashboard"))
    return render_template("console.html", page="Login")


@app.post("/logout")
def logout():
    with database() as db:
        db.execute("DELETE FROM file_sessions WHERE token=?", (digest(session["token"]),))
        db.execute("DELETE FROM sessions WHERE token=?", (digest(session["token"]),))
        audit(db, g.user, "logout", "-", "ok")
    session.clear()
    return redirect(url_for("login"))


def broker(action, **data):
    with socket.socket(socket.AF_UNIX) as channel:
        channel.settimeout(28)
        channel.connect("/run/bitx-broker/control")
        channel.sendall(json.dumps(dict(data, actor=g.user, action=action)).encode() + b"\n")
        with channel.makefile("rb") as stream:
            reply = json.loads(stream.readline(1048576))
    if not reply["ok"]:
        raise ValueError(reply["error"])
    return reply["data"]


@contextmanager
def files_channel(action, **data):
    with socket.socket(socket.AF_UNIX) as channel:
        channel.settimeout(195)
        channel.connect("/run/bitx-files/control")
        data.update(actor=g.user, role=g.role, identity=g.identity, action=action)
        header = json.dumps(data, ensure_ascii=True).encode()
        if len(header) > 16000:
            raise ValueError("Cerere prea mare.")
        channel.sendall(header + b"\n")
        with channel.makefile("rb") as stream:
            yield channel, stream


def file_reply(stream):
    result = json.loads(stream.readline(8 * 1024 * 1024))
    if not result["ok"]:
        raise ValueError(result["error"])
    return result["data"]


def file_query(action, **data):
    with files_channel(action, **data) as (_, stream):
        return file_reply(stream)


def file_audit(action, target, result):
    with database() as db:
        audit(db, g.user, "files." + action, str(target)[:120], result)


@app.get("/file-manager")
def file_manager():
    context = {"page": "File Manager", "pages": PAGES if g.role == "admin" else ("File Manager",)}
    if g.role == "admin":
        try:
            context["nas"] = broker("nas_view")
            context["grants"] = file_query("grants")
        except (ValueError, OSError):
            context["error"] = "Serviciul File Manager este indisponibil."
    return render_template("console.html", **context)


@app.post("/file-manager/access")
def file_access():
    if g.role != "admin":
        abort(403)
    action = request.form.get("action")
    name = request.form.get("name", "")
    try:
        if action == "set_grants":
            file_query("set_grants", repo=name, users=request.form.getlist("users"))
        elif action in ("enable", "disable") and NAME.fullmatch(name):
            with database() as db:
                if action == "enable":
                    password = request.form.get("password", "")
                    validate_password(password)
                    if password != request.form.get("confirmation"):
                        raise ValueError("Parolele nu coincid.")
                    identity = file_query("identity", name=name)
                    db.execute("""INSERT INTO file_logins VALUES (?,?,1,?)
                        ON CONFLICT(name) DO UPDATE SET password=excluded.password, enabled=1, identity=excluded.identity""",
                               (name, generate_password_hash(password), identity))
                else:
                    db.execute("UPDATE file_logins SET enabled=0 WHERE name=?", (name,))
                db.execute("DELETE FROM sessions WHERE name=? AND token IN (SELECT token FROM file_sessions)", (name,))
        else:
            abort(400)
        file_audit(action, name, "ok")
        flash("Accesul a fost salvat.", "ok")
    except (ValueError, OSError) as error:
        flash(str(error) if isinstance(error, ValueError) else "Serviciul File Manager nu răspunde.", "error")
        file_audit(action, name, "denied")
    return redirect(url_for("file_manager"))


@app.post("/file-manager/api")
def file_api():
    if g.role != "files":
        abort(403)
    data = request.get_json()
    if not isinstance(data, dict):
        abort(400)
    action = data.pop("action", "")
    if action not in ("roots", "list", "mkdir", "delete", "copy", "move", "rename",
                      "repositories", "snapshots", "tree", "diff", "restore", "job", "jobs"):
        abort(400)
    target = data.get("name", data.get("repo", "-"))
    try:
        value = file_query(action, **data)
        file_audit(action, target, "ok")
        return jsonify(value)
    except (ValueError, TypeError, OSError) as error:
        file_audit(action, target, "denied")
        return jsonify(error=str(error) if isinstance(error, ValueError) else "Serviciul nu răspunde."), 400


@app.post("/file-manager/upload")
def file_upload():
    if g.role != "files":
        abort(403)
    request.max_content_length = 8 * 2**30
    size = request.content_length
    if size is None or size > request.max_content_length:
        abort(413)
    name = request.args.get("name", "")
    try:
        with files_channel("upload", root=request.args.get("root"), path=request.args.get("path", ""),
                           name=name, size=size) as (channel, stream):
            file_reply(stream)
            remaining = size
            while remaining:
                chunk = request.stream.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise ValueError("Upload întrerupt.")
                channel.sendall(chunk)
                remaining -= len(chunk)
            value = file_reply(stream)
        file_audit("upload", name, "ok")
        return jsonify(value)
    except (ValueError, OSError) as error:
        file_audit("upload", name, "denied")
        return jsonify(error=str(error) if isinstance(error, ValueError) else "Upload întrerupt."), 400


@app.get("/file-manager/download")
def file_download():
    if g.role != "files":
        abort(403)
    action = "archive" if request.args.get("paths") else ("dump" if request.args.get("repo") else "download")
    name = request.args.get("name", "")
    fields = {k: request.args[k] for k in ("name", "root", "path", "repo", "snapshot", "kind") if k in request.args}
    if action == "archive":
        try:
            fields["paths"] = json.loads(request.args["paths"])
        except ValueError:
            abort(400)
    context = files_channel(action, **fields)
    try:
        _, stream = context.__enter__()
        info = file_reply(stream)
    except (ValueError, OSError):
        context.__exit__(*sys.exc_info())
        file_audit("download", name, "denied")
        abort(404)
    file_audit("download", info["name"], "ok")

    def body():
        try:
            while chunk := stream.read(256 * 1024):
                yield chunk
        finally:
            context.__exit__(None, None, None)

    response = Response(body(), mimetype="application/octet-stream")
    response.headers["Content-Disposition"] = dump_options_header("attachment", {
        "filename": "download", "filename*": "UTF-8''" + quote(info["name"], safe=""),
    })
    if info.get("size") is not None:
        response.content_length = info["size"]
    return response


def change_admin(action, name, password):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT enabled FROM admins WHERE name=?", (name,)).fetchone()
        if action in ("edit", "create"):
            validate_password(password)
            if (action == "create") == bool(existing):
                raise ValueError("Contul nu se află în starea cerută de operațiune.")
            password_hash = generate_password_hash(password)
            if existing:
                db.execute("UPDATE admins SET password=?,enabled=1 WHERE name=?", (password_hash, name))
            else:
                db.execute("INSERT INTO admins VALUES (?,?,1)", (name, password_hash))
        else:
            if not existing or name == g.user:
                raise ValueError("Sesiunea curentă nu își poate revoca propriul cont.")
            if action == "disable":
                db.execute("UPDATE admins SET enabled=0 WHERE name=?", (name,))
            else:
                db.execute("DELETE FROM admins WHERE name=?", (name,))
        db.execute("DELETE FROM sessions WHERE name=?", (name,))


@app.post("/users")
def users():
    action = request.form.get("action")
    role = request.form.get("role")
    name = request.form.get("name", "")
    if action not in ("create", "edit", "disable", "delete") or role not in ("admin", "sftp", "smb") or not NAME.fullmatch(name):
        abort(400)
    try:
        password = request.form.get("password", "")
        if role == "admin":
            change_admin(action, name, password)
        else:
            auth = request.form.get("auth", "")
            if role == "sftp" and action in ("create", "edit"):
                if auth not in ("key", "password", "either"):
                    raise ValueError("Selectează o metodă de autentificare.")
                if auth != "key" and password != request.form.get("password_confirm", ""):
                    raise ValueError("Parolele nu coincid.")
            broker(action, name=name, role=role, auth=auth,
                   delete_data=request.form.get("delete_data") == "on",
                   confirm_delete=request.form.get("confirm_delete", ""),
                   password=password if role == "smb" or (role == "sftp" and auth in ("password", "either")) else "",
                   key=request.form.get("key", ""), repositories=request.form.getlist("repositories"))
        result = "ok"
        flash("Operațiunea a fost salvată.", "ok")
    except (ValueError, OSError) as error:
        result = "denied"
        flash(str(error) if isinstance(error, ValueError) else "Serviciul local este indisponibil.", "error")
    with database() as db:
        audit(db, g.user, f"{role}.{action}", name, result)
    return redirect(url_for("page", name="users"))


@app.post("/shares")
def change_share():
    action = request.form.get("action")
    name = request.form.get("name", "")
    if action not in ("sftp_directory", "restic_directory", "smb_share", "smb_share_edit", "smb_share_delete") or not NAME.fullmatch(name):
        abort(400)
    permissions = {"readers": [], "writers": []}
    for field, value in request.form.items():
        if field.startswith("smb_") and value in permissions:
            permissions[value].append(field.removeprefix("smb_"))
    try:
        broker(action, name=name, **permissions)
        result = "ok"
        flash("Destinația a fost salvată.", "ok")
    except (ValueError, OSError) as error:
        result = "denied"
        flash(str(error) if isinstance(error, ValueError) else "Serviciul local este indisponibil.", "error")
    with database() as db:
        audit(db, g.user, action, name, result)
    return redirect(url_for("page", name="shares"))


@app.get("/")
@app.get("/<name>")
def page(name="dashboard"):
    selected = next((item for item in PAGES if name == item.lower()), None)
    if not selected:
        abort(404)
    context = {"page": selected, "pages": PAGES}
    try:
        if selected in ("Dashboard", "Storage", "Repositories", "Users", "Shares"):
            context.update(broker("inventory"))
        if selected in ("Users", "Shares"):
            context["accounts"] = broker("list")
        if selected in ("Dashboard", "Storage", "Users", "Shares", "Repositories", "Settings"):
            context["nas"] = broker("nas_view", detail=selected.lower())
    except (ValueError, OSError):
        context["error"] = "Serviciul local este indisponibil."
    with database() as db:
        if selected == "Users":
            context["admins"] = db.execute("SELECT name,enabled FROM admins ORDER BY name").fetchall()
        if selected == "Activity":
            context["events"] = db.execute("SELECT * FROM audit ORDER BY rowid DESC LIMIT 100").fetchall()
    return render_template("console.html", **context)


@app.post("/nas")
def nas_change():
    action = request.form.get("action", "")
    name = request.form.get("name", "")
    targets = {
        "nas_user_create": "users", "nas_user_edit": "users", "nas_user_delete": "users",
        "nas_share_create": "shares", "nas_share_edit": "shares", "nas_share_delete": "shares", "nas_builtin_edit": "shares",
        "nas_repository_set": "repositories", "nas_repository_remove": "repositories",
        "nas_repository_check": "repositories", "nas_repository_snapshots": "repositories",
    }
    if action not in targets or not NAME.fullmatch(name):
        abort(400)
    fields = {field: request.form.get(field, "") for field in (
        "display", "description", "new_name", "auth", "key", "password", "share", "directory", "client", "confirm_delete",
    )}
    fields.update({field: request.form.get(field) == "on" for field in ("enabled", "sftp", "smb", "create", "delete_data")})
    fields["permissions"] = {field[5:]: value for field, value in request.form.items()
                             if field.startswith("perm_") and value in ("read", "write")}
    try:
        if fields["password"] != request.form.get("password_confirm", ""):
            raise ValueError("Parolele nu coincid.")
        broker(action, name=name, **fields)
        flash("Modificarea a fost salvată.", "ok")
        result = "ok"
    except (ValueError, OSError) as error:
        flash(str(error) if isinstance(error, ValueError) else "Serviciul local este indisponibil.", "error")
        result = "denied"
    with database() as db:
        audit(db, g.user, action, name, result)
    return redirect(url_for("page", name=targets[action]))


@app.template_filter("size")
def size(value):
    return f"{value / 2**30:,.2f} GiB"


def bootstrap(name):
    if not NAME.fullmatch(name):
        raise SystemExit("Invalid username.")
    initialize()
    password = getpass.getpass("Parolă nouă pentru administratorul web: ")
    if password != getpass.getpass("Confirmă parola: "):
        raise SystemExit("Parolele diferă.")
    validate_password(password)
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM admins LIMIT 1").fetchone():
            raise SystemExit("Administratorul inițial a fost deja configurat.")
        db.execute("INSERT INTO admins VALUES (?,?,1)", (name, generate_password_hash(password)))
        audit(db, "console", "bootstrap", name, "ok")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("initialize", "bootstrap"))
    parser.add_argument("name", nargs="?", default="bitx-admin")
    args = parser.parse_args()
    if args.command == "bootstrap":
        bootstrap(args.name)
    else:
        initialize()

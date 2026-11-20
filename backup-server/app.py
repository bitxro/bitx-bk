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
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


ROOT = "/var/lib/bitx-console"
NAME = re.compile(r"[a-z][a-z0-9-]{0,19}\Z")
LAN = ipaddress.IPv4Network(os.environ.get("BITX_LAN", "127.0.0.0/8"))
TOKEN_TTL = 1800
PAGES = ("Dashboard", "Storage", "Repositories", "Users", "Shares", "Activity", "Settings")
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
    if "token" in session:
        with database() as db:
            row = db.execute("""
                SELECT admins.name FROM sessions JOIN admins USING(name)
                WHERE token=? AND expires>? AND enabled=1
            """, (digest(session["token"]), int(time.time()))).fetchone()
        if row:
            g.user = row["name"]
    if request.method == "POST":
        csrf = session.get("csrf")
        if not csrf or not secrets.compare_digest(csrf, request.form.get("csrf", "")):
            abort(403)
    if request.endpoint not in ("login", "static") and not g.user:
        return redirect(url_for("login"))
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe()


@app.after_request
def protect(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self'; img-src 'self'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("page", name="dashboard"))
    if request.method == "POST":
        supplied = request.form.get("name", "")
        name = supplied if NAME.fullmatch(supplied) else "-"
        password = request.form.get("password", "")
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
            row = db.execute("SELECT password, enabled FROM admins WHERE name=?", (name,)).fetchone()
            verified = len(password) <= 128 and check_password_hash(row["password"] if row else DUMMY_PASSWORD, password)
            if not (verified and row and row["enabled"]):
                db.execute("INSERT INTO attempts VALUES (?,?,?)", (address, name, now))
                audit(db, name, "login", "-", "denied")
                return render_template("console.html", page="Login", error="Autentificare nereușită."), 401
            db.execute("DELETE FROM attempts WHERE address=? AND name=?", (address, name))
            db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (digest(token), name, now + TOKEN_TTL))
            audit(db, name, "login", "-", "ok")
        session.clear()
        session.update(token=token, csrf=secrets.token_urlsafe())
        return redirect(url_for("page", name="dashboard"))
    return render_template("console.html", page="Login")


@app.post("/logout")
def logout():
    with database() as db:
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
        if selected == "Users":
            context["accounts"] = broker("list")
    except (ValueError, OSError):
        context["error"] = "Serviciul local este indisponibil."
    with database() as db:
        if selected == "Users":
            context["admins"] = db.execute("SELECT name,enabled FROM admins ORDER BY name").fetchall()
        if selected == "Activity":
            context["events"] = db.execute("SELECT * FROM audit ORDER BY rowid DESC LIMIT 100").fetchall()
    return render_template("console.html", **context)


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

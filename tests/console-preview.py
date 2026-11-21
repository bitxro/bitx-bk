#!/usr/bin/env python3
"""Offline preview and template contract check for the bitX console UI.

Renders the real Jinja templates with SIMULATED fixture data. Nothing here
talks to the broker, the file gateway, Restic, Samba or SFTP.

  python3 tests/console-preview.py check            # render every page, verify form contract
  python3 tests/console-preview.py contract > FILE  # print the current form contract
  python3 tests/console-preview.py serve [PORT]     # interactive preview (default 8765)

Preview query parameters: ?as=files (file-account session), ?state=empty,
?state=error, ?state=offline (no volume).
"""
import json
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

from flask import Flask, Response, flash, g, jsonify, redirect, render_template, request, session, url_for

ROOT = Path(__file__).resolve().parent.parent / "backup-server"
CONTRACT = Path(__file__).resolve().parent / "console-forms.json"
PAGES = ("Dashboard", "Storage", "Repositories", "Users", "Shares", "Activity", "Settings", "File Manager")
GiB = 2**30

app = Flask("preview", template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
app.secret_key = "preview-only"


@app.template_filter("size")
def size(value):
    return f"{value / 2**30:,.2f} GiB"


def fixture(state=""):
    empty = state == "empty"
    users = {} if empty else {
        "ana": {"name": "ana", "display": "Ana Popescu", "enabled": True, "smb": True, "sftp": True, "auth": "either",
                "access": {"proiecte": "write", "foto": "read"}},
        "mihai": {"name": "mihai", "display": "Mihai Ionescu", "enabled": True, "smb": True, "sftp": False, "auth": "key",
                  "access": {"proiecte": "read"}},
        "contabil": {"name": "contabil", "display": "", "enabled": False, "smb": False, "sftp": True, "auth": "password",
                     "access": {}},
    }
    shares = {} if empty else {
        "proiecte": {"name": "proiecte", "directory": "proiecte", "description": "Documente de proiect", "enabled": True,
                     "smb": True, "sftp": True, "permissions": {"ana": "write", "mihai": "read"},
                     "path": "/mnt/bitx-backup/bitX-BACKUP/shares/proiecte", "bytes": 182 * GiB},
        "foto": {"name": "foto", "directory": "foto", "description": "Arhivă foto", "enabled": True,
                 "smb": True, "sftp": False, "permissions": {"ana": "read"},
                 "path": "/mnt/bitx-backup/bitX-BACKUP/shares/foto", "bytes": 640 * GiB},
        "vechi": {"name": "vechi", "directory": "vechi", "description": "", "enabled": False,
                  "smb": False, "sftp": False, "permissions": {},
                  "path": "/mnt/bitx-backup/bitX-BACKUP/shares/vechi", "bytes": None},
    }
    repositories = {} if empty else {
        "srv-web": {"name": "srv-web", "share": "proiecte", "share_name": "proiecte", "directory": "restic-web",
                    "client": "web01.bitx.ro", "latest": "2026-11-20T02:41:40Z", "initialized": True,
                    "bytes": 94 * GiB, "access": {"ana": "write", "mihai": "read"},
                    "check": "Verificare reușită 2026-11-19", "snapshot_error": ""},
        "laptop-ana": {"name": "laptop-ana", "share": "foto", "share_name": "foto", "directory": "restic-laptop",
                       "client": "", "latest": None, "initialized": False, "bytes": None, "access": {},
                       "check": "", "snapshot_error": "Credențial local lipsă"},
    }
    nas = {
        "users": users, "shares": shares, "repositories": repositories,
        "smb_accounts": [] if empty else ["ana", "bitxwindows", "mihai", "office"],
        "builtin_shares": {"windows-backup": {} if empty else {"ana": "write", "office": "read"},
                           "backup-admin": {} if empty else {"mihai": "read"}},
        "legacy_windows": ["bitxwindows"] + ([] if empty else ["office"]),
        "volume": {"device": "/dev/sdb1", "filesystem": "exfat", "smart": "OK"},
        "listeners": "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\nLISTEN 0 50 0.0.0.0:445 0.0.0.0:*\nLISTEN 0 128 100.64.0.7:8443 0.0.0.0:*",
    }
    inventory = {
        "disk": None if state == "offline" else {"total": 3726 * GiB, "used": 2458 * GiB, "free": 1268 * GiB},
        "services": {"ssh": "active", "smbd": "active", "qemu-guest-agent": "active", "bitx-console": "active",
                     "bitx-broker": "inactive" if state == "offline" else "active"},
        "repositories": [] if empty else [
            {"name": "windows-pc", "latest": "2026-11-21T01:00:12Z", "status": "Verificat"},
            {"name": "nas-legacy", "latest": None, "status": "Snapshoturi indisponibile fără credențial local"},
        ],
        "sftp_destinations": [] if empty else ["nas-legacy", "windows-pc"],
        "file_destinations": [],
        "shares": {} if empty else {"office-docs": {"readers": ["office"], "writers": ["bitxwindows"]}},
    }
    accounts = [] if empty else [
        {"name": "backup-web", "login": "backup-web", "role": "sftp", "enabled": True, "auth": "key",
         "repositories": ["windows-pc"], "home_provisioned": True},
        {"name": "office", "login": "office", "role": "smb", "enabled": True},
    ]
    admins = [{"name": "bitx-admin", "enabled": 1}] + ([] if empty else [{"name": "operator", "enabled": 0}])
    events = [] if empty else [
        {"at": f"2026-11-21T09:{59 - i:02d}:00+00:00", "actor": actor, "event": event, "target": target, "result": result}
        for i, (actor, event, target, result) in enumerate([
            ("bitx-admin", "login", "-", "ok"), ("ana", "files.restore", "srv-web", "ok"),
            ("bitx-admin", "nas_share_edit", "proiecte", "ok"), ("-", "login", "-", "denied"),
            ("ana", "files.download", "raport.pdf", "ok"), ("bitx-admin", "nas_repository_check", "srv-web", "denied"),
        ])
    ]
    grants = {} if empty else {"srv-web": ["ana"]}
    return nas, inventory, accounts, admins, events, grants


def context_for(page, state=""):
    nas, inventory, accounts, admins, events, grants = fixture(state)
    context = {"page": page, "pages": PAGES if g.role == "admin" else ("File Manager",)}
    if state == "error":
        context["error"] = "Serviciul local este indisponibil."
        return context
    if page in ("Dashboard", "Storage", "Repositories", "Users", "Shares"):
        context.update(inventory)
    if page in ("Users", "Shares"):
        context["accounts"] = accounts
    if page in ("Dashboard", "Storage", "Users", "Shares", "Repositories", "Settings", "File Manager") and g.role == "admin":
        context["nas"] = nas
    if page == "Users":
        context["admins"] = admins
    if page == "Activity":
        context["events"] = events
    if page == "File Manager" and g.role == "admin":
        context["grants"] = grants
    return context


@app.before_request
def fake_session():
    role = request.args.get("as") or request.cookies.get("as") or "admin"
    g.role = "files" if role == "files" else "admin"
    g.user = "ana" if g.role == "files" else "bitx-admin"
    g.identity = "preview"
    session.setdefault("csrf", "preview-csrf-token")


def render(page):
    response = Response(render_template("console.html", **context_for(page, request.args.get("state", ""))))
    if "as" in request.args:
        response.set_cookie("as", request.args["as"])
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        return redirect(url_for("page", name="dashboard"))
    error = request.args.get("error")
    return render_template("console.html", page="Login", error=error)


@app.post("/logout")
def logout():
    return redirect(url_for("login"))


@app.get("/file-manager")
def file_manager():
    return render("File Manager")


@app.post("/file-manager/access")
def file_access():
    flash("Accesul a fost salvat. (simulat)", "ok")
    return redirect(url_for("file_manager"))


@app.post("/users")
def users():
    flash("Operațiunea a fost salvată. (simulat)", "ok")
    return redirect(url_for("page", name="users"))


@app.post("/shares")
def change_share():
    flash("Destinația a fost salvată. (simulat)", "ok")
    return redirect(url_for("page", name="shares"))


@app.post("/nas")
def nas_change():
    flash("Modificarea a fost salvată. (simulat)", "error" if request.form.get("action") == "nas_repository_check" else "ok")
    target = {"nas_user": "users", "nas_share": "shares", "nas_built": "shares"}.get(request.form.get("action", "")[:9], "repositories")
    return redirect(url_for("page", name=target))


@app.get("/")
@app.get("/<name>")
def page(name="dashboard"):
    selected = next((item for item in PAGES if name == item.lower()), None)
    if not selected:
        return "Not found", 404
    return render(selected)


# ---- simulated File Manager API -------------------------------------------------
NOW = time.time()
TREE = {
    "home": {"": [("Documente", "dir", None), ("Poze", "dir", None), ("note.txt", "file", 1840),
                  ("raport-anual-2025.pdf", "file", 4_812_330), ("buget.csv", "file", 22_118)],
             "Documente": [("contracte", "dir", None), ("oferta.docx", "file", 88_412), ("readme.md", "file", 912)],
             "Poze": [("vacanta.jpg", "file", 3_201_554), ("logo.png", "file", 41_220)]},
    "share:proiecte": {"": [("2026", "dir", None), ("specificatii.md", "file", 14_220), ("arhiva.zip", "file", 1_442_118_224)],
                       "2026": [("plan.txt", "file", 2_200)]},
    "share:foto": {"": [("2019", "dir", None), ("2020", "dir", None), ("index.json", "file", 640)]},
}
SNAP_TREE = {
    "": [("etc", "dir", None), ("var", "dir", None), ("home", "dir", None)],
    "etc": [("nginx", "dir", None), ("hosts", "file", 412), ("fstab", "file", 980)],
    "etc/nginx": [("nginx.conf", "file", 2_948), ("sites-enabled", "dir", None)],
    "var": [("www", "dir", None), ("log", "dir", None)],
    "home": [("deploy", "dir", None)],
}
JOBS = [
    {"id": "a" * 32, "status": "running", "message": "Restaurare în curs", "target": "home/restore-2026-11-21", "restored": 734_003_200},
    {"id": "b" * 32, "status": "complete", "message": "Restaurare finalizată", "target": "share:proiecte/recuperare-nginx", "restored": 18_221},
    {"id": "c" * 32, "status": "failed", "message": "Restaurarea a eșuat. Verifică permisiunile, spațiul și credențialul.", "target": "home/test", "restored": 0},
]


def entries(rows, prefix=""):
    out = []
    for i, (name, kind, sz) in enumerate(rows):
        item = {"name": name, "type": kind, "size": sz, "mtime": NOW - 3600 * (i * 7 + 2)}
        if prefix is not None:
            item["path"] = "/".join(filter(None, [prefix, name]))
        out.append(item)
    return out


@app.post("/file-manager/api")
def file_api():
    if request.headers.get("X-CSRF-Token") != session.get("csrf"):
        return jsonify(error="CSRF"), 403
    data = request.get_json()
    action = data.get("action")
    state = request.cookies.get("fmstate", "")
    if action == "roots":
        return jsonify([{"id": "home", "access": "write"}, {"id": "share:proiecte", "access": "write"},
                        {"id": "share:foto", "access": "read"}])
    if action == "list":
        rows = TREE.get(data["root"], {}).get(data.get("path", ""), [])
        if state == "empty":
            rows = []
        q = data.get("q", "").casefold()
        rows = [r for r in rows if q in r[0].casefold()]
        items = [{k: v for k, v in e.items() if k != "path"} for e in entries(rows)]
        return jsonify({"entries": items, "total": len(items)})
    if action == "repositories":
        return jsonify([
            {"name": "srv-web", "share": "proiecte", "client": "web01.bitx.ro", "latest": "2026-11-20T02:41:40Z",
             "state": "Verificare reușită", "size": 94 * GiB},
            {"name": "srv-mail", "share": "proiecte", "client": "mail.bitx.ro", "latest": None, "state": None, "size": None},
        ])
    if action == "snapshots":
        if data.get("repo") == "srv-mail":
            return jsonify([])
        return jsonify([{"id": f"{c * 8}{i:056d}", "time": f"2026-11-{20 - i:02d}T02:41:40.123456789Z", "hostname": "web01.bitx.ro"}
                        for i, c in enumerate("abcdef")])
    if action == "tree":
        path = data.get("path", "")
        q = data.get("q", "").casefold()
        rows = [r for r in SNAP_TREE.get(path, [("fisier.txt", "file", 100)]) if q in r[0].casefold()]
        return jsonify({"entries": entries(rows, path), "more": False})
    if action == "jobs":
        return jsonify(JOBS)
    if action == "diff":
        return jsonify("comparing snapshot aaaaaaaa to bbbbbbbb:\n\nM    /etc/nginx/nginx.conf\n+    /var/www/index.html\n-    /etc/old.conf\n\nFiles:           1 new,     1 removed,     1 changed\n")
    if action in ("mkdir", "delete", "copy", "move", "rename"):
        return jsonify(None)
    if action == "restore":
        return jsonify({"id": "d" * 32})
    return jsonify(error="Operațiune necunoscută (simulat)."), 400


@app.post("/file-manager/upload")
def file_upload():
    request.get_data()
    return jsonify(None)


@app.get("/file-manager/download")
def file_download():
    name = request.args.get("name") or request.args.get("path", "fisier").rpartition("/")[2]
    if name.endswith(".png") or name.endswith(".jpg"):
        png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                            "1f15c4890000000d49444154789c6360f8cfc00000030101009a8c3c8f0000000049454e44ae426082")
        return Response(png, mimetype="application/octet-stream")
    body = f"Conținut simulat pentru {name}.\nLinia 2\n"
    return Response(body, mimetype="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename=download"})


# ---- contract -----------------------------------------------------------------
class Forms(HTMLParser):
    """Collects POST forms: action, field names and submit values."""

    def __init__(self):
        super().__init__()
        self.forms, self.current, self.ids = [], None, set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "form":
            self.current = {"action": attrs.get("action", ""), "method": attrs.get("method", "get").lower(),
                            "fields": set(), "buttons": set()}
            self.forms.append(self.current)
        elif self.current is not None and tag in ("input", "select", "textarea") and attrs.get("name"):
            self.current["fields"].add(attrs["name"])
        elif self.current is not None and tag == "button" and attrs.get("name"):
            self.current["buttons"].add(f'{attrs["name"]}={attrs.get("value", "")}')

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


VIEWS = [(page, role) for page in PAGES for role in ("admin",)] + [("File Manager", "files"), ("Login", None)]


def collect():
    contract, problems = {}, []
    client = app.test_client()
    for page, role in VIEWS:
        for state in ("", "empty", "error", "offline"):
            url = "/login" if page == "Login" else ("/file-manager" if page == "File Manager" else "/" + page.lower())
            response = client.get(f"{url}?as={role or 'admin'}&state={state}")
            if response.status_code != 200:
                problems.append(f"{page} [{state}] HTTP {response.status_code}")
                continue
            parser = Forms()
            parser.feed(response.get_data(as_text=True))
            for form in parser.forms:
                if form["method"] == "post" and "csrf" not in form["fields"]:
                    problems.append(f"{page} [{state}] POST form {form['action']} has no CSRF field")
            if state == "":
                key = f"{page}:{role}"
                contract[key] = sorted({json.dumps({"action": f["action"], "method": f["method"],
                                                    "fields": sorted(f["fields"]), "buttons": sorted(f["buttons"])},
                                                   sort_keys=True) for f in parser.forms if f["method"] == "post"})
                if page == "File Manager" and role == "files":
                    contract[key + ":ids"] = sorted(i for i in parser.ids if i.startswith("fm-") or i == "file-manager")
    return contract, problems


def check():
    contract, problems = collect()
    if CONTRACT.exists():
        expected = json.loads(CONTRACT.read_text())
        for key, forms in expected.items():
            missing = set(forms) - set(contract.get(key, []))
            for item in sorted(missing):
                problems.append(f"{key}: form contract changed or missing: {item}")
    for problem in problems:
        print("FAIL", problem)
    print(f"{len(VIEWS) * 4} renders, {sum(len(v) for v in contract.values())} contract entries, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "serve":
        app.run(port=int(sys.argv[2]) if len(sys.argv) > 2 else 8765, debug=False)
    elif command == "contract":
        print(json.dumps(collect()[0], indent=1, ensure_ascii=False))
    else:
        sys.exit(check())

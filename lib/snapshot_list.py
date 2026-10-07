"""Readable interactive Restic snapshot listing, grouped by backup run."""
import datetime
import json
import shutil
import sys
import textwrap


def clean(value):
    return "".join(c if c.isprintable() else " " for c in str(value))


def display(snapshots, color="", reset="", order="oldest"):
    width = max(40, min(shutil.get_terminal_size((100, 24)).columns, 120))
    groups = {}
    ordered = list(reversed(snapshots)) if order == "newest" else sorted(snapshots, key=lambda s: s.get("time", ""))
    for index, snapshot in enumerate(ordered, 1):
        tags = snapshot.get("tags") or []
        run = next((t for t in tags if t.startswith("bitx-bk-run:")), None)
        key = (snapshot.get("hostname", ""), run or snapshot.get("id", ""))
        groups.setdefault(key, []).append((index, snapshot))
    if not snapshots:
        print("No snapshots found.")
        return
    for (host, run), items in groups.items():
        print()
        print(color + "─" * width + reset)
        label = run.removeprefix("bitx-bk-run:")
        print(color + clean(f"Backup: {label} | Host: {host}") + reset)
        print(color + "─" * width + reset)
        for index, snapshot in items:
            tags = snapshot.get("tags") or []
            scope = next((t[6:] for t in tags if t.startswith("scope:")), "snapshot")
            project = next((t[8:] for t in tags if t.startswith("project:")), "")
            timestamp = snapshot.get("time", "")
            try:
                timestamp = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            except (ValueError, TypeError):
                pass
            print()
            label = f"  {index}) {snapshot.get('short_id') or snapshot.get('id', '')[:8]}  |  {scope.upper()}"
            if project:
                label += "  |  " + project
            print(color + clean(label) + reset)
            print("     Time: " + clean(timestamp))
            extra = [t for t in tags if t != "bitx-bk" and not t.startswith(("host:", "scope:", "project:", "bitx-bk-run:"))]
            if extra:
                print(textwrap.fill("Tags: " + clean(", ".join(extra)), width=width, initial_indent="     ", subsequent_indent="       "))
            print("     Paths:")
            for path in snapshot.get("paths") or []:
                print(textwrap.fill(clean(path), width=width, initial_indent="       ", subsequent_indent="         ", break_on_hyphens=False))
    print()
    print(f"{len(snapshots)} snapshots / {len(groups)} backup groups")


if __name__ == "__main__":
    with (sys.stdin if sys.argv[1] == "-" else open(sys.argv[1], encoding="utf-8")) as source:
        snapshots = json.load(source) or []
    display(snapshots, *sys.argv[2:5])

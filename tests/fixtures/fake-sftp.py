#!/usr/bin/env python3
"""Minimal stand-in for OpenSSH `sftp -b` against an SFTP-only (chrooted) server.

The server root is FAKE_SFTP_ROOT. Behaviour switches (environment):
  FAKE_SFTP_MODE      ok | auth | hostkey-unknown | hostkey-changed | unreachable | no-sftp
  FAKE_SFTP_KNOWN     file; when it exists, hostkey-unknown behaves like ok (key accepted)
  FAKE_SFTP_DENY      colon-separated remote paths whose listing is denied
  FAKE_SFTP_READONLY  colon-separated remote paths where mkdir is denied
  FAKE_SFTP_NORMDIR   when set, rmdir fails with "Permission denied"
  FAKE_SFTP_LOG       every invocation's argv is appended here

Like real sftp: "ls" errors print to stderr but do not fail the batch,
commands prefixed with "-" ignore errors, "@" suppresses the echo.
"""
import os
import shlex
import sys

argv = sys.argv[1:]
with open(os.environ.get("FAKE_SFTP_LOG", os.devnull), "a") as log:
    log.write(" ".join(shlex.quote(a) for a in argv) + "\n")

mode = os.environ.get("FAKE_SFTP_MODE", "ok")
known = os.environ.get("FAKE_SFTP_KNOWN", "")
target = argv[-1]
if mode == "hostkey-unknown" and not (known and os.path.exists(known)):
    sys.stderr.write("Host key verification failed.\r\nConnection closed\r\n")
    sys.exit(255)
if mode == "hostkey-changed":
    sys.stderr.write("@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\r\nHost key verification failed.\r\nConnection closed\r\n")
    sys.exit(255)
if mode == "auth":
    sys.stderr.write(f"{target}: Permission denied (publickey).\r\nConnection closed\r\n")
    sys.exit(255)
if mode == "unreachable":
    host = target.split("@")[-1]
    sys.stderr.write(f"ssh: Could not resolve hostname {host}: Name or service not known\r\nConnection closed\r\n")
    sys.exit(255)
if mode == "no-sftp":
    sys.stderr.write("subsystem request failed on channel 0\r\nConnection closed\r\n")
    sys.exit(255)

batch = argv[argv.index("-b") + 1]
root = os.environ["FAKE_SFTP_ROOT"]
deny = set(filter(None, os.environ.get("FAKE_SFTP_DENY", "").split(":")))
readonly = set(filter(None, os.environ.get("FAKE_SFTP_READONLY", "").split(":")))
cwd = "/home" if os.path.isdir(os.path.join(root, "home")) else "/"


def local(path):
    return os.path.join(root, path.lstrip("/"))


def absolute(path):
    return os.path.normpath(path if path.startswith("/") else os.path.join(cwd, path))


for raw in open(batch).read().splitlines():
    line = raw.strip()
    if not line:
        continue
    ignore = echo_off = False
    while line[:1] in ("-", "@"):
        ignore |= line[0] == "-"
        echo_off |= line[0] == "@"
        line = line[1:]
    if not echo_off:
        print("sftp> " + line)
    words = shlex.split(line)
    cmd, args = words[0], words[1:]
    failed = False
    if cmd == "pwd":
        print("Remote working directory: " + cwd)
    elif cmd == "cd":
        path = absolute(args[0])
        if not os.path.isdir(local(path)):
            sys.stderr.write("stat remote: No such file or directory\n")
            failed = True
        else:
            cwd = path
    elif cmd == "ls":
        flags = [a for a in args if a.startswith("-")]
        if cwd in deny:
            sys.stderr.write(f'remote readdir("{cwd}"): Permission denied\n')
        else:
            names = sorted(os.listdir(local(cwd)))
            if any("a" in f for f in flags):
                names = [".", ".."] + names
            else:
                names = [n for n in names if not n.startswith(".")]
            for name in names:
                print(name)
    elif cmd == "mkdir":
        path = absolute(args[0])
        parent = os.path.dirname(path)
        if parent in readonly or parent in deny:
            sys.stderr.write(f'remote mkdir "{path}": Permission denied\n')
            failed = True
        elif os.path.exists(local(path)) or not os.path.isdir(local(parent)):
            sys.stderr.write(f'remote mkdir "{path}": Failure\n')
            failed = True
        else:
            os.mkdir(local(path))
    elif cmd == "rmdir":
        path = absolute(args[0])
        if os.environ.get("FAKE_SFTP_NORMDIR"):
            sys.stderr.write(f'remote rmdir "{path}": Permission denied\n')
            failed = True
        else:
            os.rmdir(local(path))
    else:
        sys.stderr.write(f"Invalid command: {cmd}\n")
        failed = True
    if failed and not ignore:
        sys.exit(1)
sys.exit(0)

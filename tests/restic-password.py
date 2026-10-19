#!/usr/bin/env python3
"""Check explicit password disclosure using fixture credentials and a pseudo terminal."""
import errno
import os
from pathlib import Path
import pty
import shlex
import subprocess
import tempfile

app = Path(__file__).resolve().parents[1] / "bin/bitx-bk"
definitions = app.read_text().split('cmd="${1:-}"')[0]
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    runner = root / "runner.sh"
    runner.write_text(definitions + '\nrestic_password_show "$1"\n')
    backup = root / "backup-password"
    recovery = root / "recovery-password"
    backup.write_text("fixture-backup-%-$-password\n")
    recovery.write_text("fixture-recovery-password\n")
    config = root / "backup.conf"
    config.write_text("RESTIC_REPOSITORY=/fixture/backup\nRESTIC_PASSWORD_FILE=" + shlex.quote(str(backup)) + "\n")
    rcfg = root / "recovery.conf"
    rcfg.write_text("RECOVERY_REPOSITORY=/fixture/recovery\nRECOVERY_PASSWORD_FILE=" + shlex.quote(str(recovery)) + "\n")
    env = dict(os.environ, BITX_BK_CONFIG=str(config), BITX_BK_RECOVERY_CONFIG=str(rcfg), NO_COLOR="1")

    def run(mode, answer="SHOW\n"):
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(["bash", str(runner), mode], env=env, stdin=slave, stdout=slave, stderr=slave)
            os.close(slave)
            slave = None
            os.write(master, answer.encode())
            proc.wait(timeout=5)
            chunks = []
            while True:
                try:
                    chunk = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                chunks.append(chunk)
            return proc.returncode, b"".join(chunks).decode()
        finally:
            if slave is not None:
                os.close(slave)
            os.close(master)

    code, text = run("backup")
    assert code == 0 and backup.read_text().strip() in text and recovery.read_text().strip() not in text
    code, text = run("recovery")
    assert code == 0 and recovery.read_text().strip() in text and backup.read_text().strip() not in text
    code, text = run("backup", "0\n")
    assert code == 0 and "Cancelled" in text and backup.read_text().strip() not in text
    captured = subprocess.run(["bash", str(runner), "backup"], env=env, input="SHOW\n", capture_output=True, text=True)
    assert captured.returncode != 0 and backup.read_text().strip() not in captured.stdout + captured.stderr
    backup.unlink()
    code, text = run("backup")
    assert code != 0 and "missing, empty or unreadable" in text
    config.unlink()
    code, text = run("backup")
    assert code != 0 and "Configuration not found" in text
print("Restic password checks passed: backup/recovery selection, confirmation, redirection and missing files")

"""Does a lock left by a DEAD process actually block a new run?"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/home/houminxi/code/forge/src")
from code_forge.lock import ForgeLockBusy, acquire_lock, _pid_alive  # noqa: E402

L = Path("/tmp/locktest/.code-forge/code-forge.lock")
L.parent.mkdir(parents=True, exist_ok=True)

# A PID that is certainly dead: spawn true, reap it.
p = subprocess.Popen(["true"])
p.wait()
dead = p.pid
print("  dead pid %d: _pid_alive=%s" % (dead, _pid_alive(dead)))

L.write_text("%d\n" % dead)
print("  wrote stale lock holding pid %d; exists=%s" % (dead, L.exists()))
try:
    acquire_lock(L)
    print("  STALE  -> acquired anyway, self-healed. content now=%r (our pid %d)"
          % (L.read_text().strip(), os.getpid()))
    L.unlink()
except ForgeLockBusy as e:
    print("  STALE  -> BLOCKED: %s" % e)

L.write_text("%d\n" % os.getpid())
try:
    acquire_lock(L)
    print("  LIVE   -> acquired (UNEXPECTED)")
except ForgeLockBusy as e:
    print("  LIVE   -> correctly blocked: %s" % e)
L.unlink()

L.write_text("not-a-pid\n")
try:
    acquire_lock(L)
    print("  GARBAGE-> reclaimed, as documented")
    L.unlink()
except ForgeLockBusy as e:
    print("  GARBAGE-> BLOCKED: %s" % e)

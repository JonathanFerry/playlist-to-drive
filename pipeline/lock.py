"""
lock.py - one pipeline instance per destination folder.

Two supervisors once ran the same playlists into the same folders
concurrently and produced 3,166 duplicate documents. Nothing was lost —
both wrote complete transcripts — but every folder held two copies of
everything.

The cause was a process check that could not see the process it was
looking for. A file had been renamed while its process was running, so
the running command line still carried the old name and a pgrep for the
new one never matched. A second supervisor was started on the belief the
first had died, then a third.

Detecting that reliably is hard. Making the second instance refuse to
start is easy, and does not depend on anyone reading a status display
correctly.

Keyed by destination folder rather than playlist, because the damage
comes from two writers sharing a folder.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LOCK_DIR = BASE / ".locks"


class AlreadyRunning(Exception):
    """Another instance holds the lock for this folder."""


def _lock_path(folder_id: str) -> Path:
    LOCK_DIR.mkdir(exist_ok=True)
    return LOCK_DIR / f"{folder_id}.lock"


def _pid_alive(pid: int) -> bool:
    """Whether a process with this pid still exists.

    Signal 0 performs the permission and existence checks without
    actually sending anything.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists, owned by someone else


class FolderLock:
    """Refuse to start if another live process holds this folder.

    A stale lock — one whose owning process no longer exists — is
    reclaimed rather than treated as fatal, since a crashed run should
    not require manual cleanup before the next attempt.
    """

    def __init__(self, folder_id: str, label: str = ""):
        self.folder_id = folder_id
        self.label = label
        self.path = _lock_path(folder_id)

    def __enter__(self) -> "FolderLock":
        if self.path.exists():
            try:
                held = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError):
                held = {}
            pid = int(held.get("pid", 0) or 0)

            if pid and _pid_alive(pid):
                raise AlreadyRunning(
                    f"Folder {self.folder_id} is already being written by "
                    f"pid {pid} (started {held.get('started', '?')}, "
                    f"{held.get('label', 'unknown job')}).\n"
                    f"Two writers on one folder duplicate every document. "
                    f"Wait for it to finish, or stop it first.\n"
                    f"If you are certain it is dead: rm {self.path}"
                )
            print(f"  reclaiming stale lock from pid {pid} (no longer running)")

        self.path.write_text(json.dumps({
            "pid": os.getpid(),
            "label": self.label,
            "folder_id": self.folder_id,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, indent=2))
        return self

    def __exit__(self, *exc) -> None:
        try:
            held = json.loads(self.path.read_text())
            if int(held.get("pid", 0)) == os.getpid():
                self.path.unlink()
        except (OSError, ValueError, json.JSONDecodeError):
            # Releasing is best-effort and must never mask an exception
            # propagating out of the guarded block.
            pass
        return None

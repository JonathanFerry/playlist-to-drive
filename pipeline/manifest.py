"""
manifest.py - pipeline state, keyed by YouTube video ID.

One JSON file per destination folder. Written incrementally so an
interrupted run resumes rather than restarting.

Statuses:
  completed    - transcript exists in Drive; drive_file_id is set
  no_captions  - video has no captions; retried on later runs
  unavailable  - private, deleted, or region-blocked; retried on later runs
  failed       - unexpected error; retried on later runs

Only 'completed' suppresses reprocessing. Everything else is retryable,
so a video that becomes available later is picked up automatically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

# Anchored to the repository, not to a fixed home directory path.
# Hardcoding ~/Documents made this work only on one machine, and
# put working state inside a cloud-synced folder — which once
# evicted a temp file mid-write and ended an eight-hour run.
BASE_DIR = Path(__file__).resolve().parent.parent

COMPLETED = "completed"
NO_CAPTIONS = "no_captions"
UNAVAILABLE = "unavailable"
FAILED = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Entry:
    video_id: str
    status: str
    title: str = ""
    drive_file_id: str = ""
    source: str = ""          # "captions" | "asr" | "bootstrap"
    word_count: int = 0
    processed_at: str = ""
    last_seen_in_playlist: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class Manifest:
    """Video-ID-keyed state, persisted as JSON."""

    def __init__(self, path: Path, folder_id: str = ""):
        self.path = path
        self.folder_id = folder_id
        self.entries: dict[str, Entry] = {}
        self.created_at = _now()

    @classmethod
    def load_or_new(cls, folder_id: str, name: str = "manifest.json") -> "Manifest":
        path = BASE_DIR / name
        m = cls(path, folder_id)
        if not path.exists():
            return m

        data = json.loads(path.read_text())
        stored_folder = data.get("folder_id", "")
        if stored_folder and folder_id and stored_folder != folder_id:
            raise SystemExit(
                f"Manifest folder mismatch.\n"
                f"  manifest.json targets : {stored_folder}\n"
                f"  config.toml targets   : {folder_id}\n"
                "Refusing to proceed. Use a separate manifest per folder, or "
                "pass --migrate once the intent is deliberate."
            )
        m.folder_id = stored_folder or folder_id
        m.created_at = data.get("created_at", m.created_at)
        for vid, raw in data.get("entries", {}).items():
            m.entries[vid] = Entry(**raw)
        return m

    def save(self) -> None:
        payload = {
            "folder_id": self.folder_id,
            "created_at": self.created_at,
            "updated_at": _now(),
            "entry_count": len(self.entries),
            "entries": {k: v.to_dict() for k, v in sorted(self.entries.items())},
        }
        # Atomic rename where possible, but never fatal. Cloud-synced
        # directories (iCloud manages ~/Documents) can move or evict a
        # temp file between write and rename, and losing the whole run to
        # a bookkeeping failure is far worse than a non-atomic write.
        data = json.dumps(payload, indent=2)
        tmp = self.path.with_suffix(".json.tmp")
        try:
            tmp.write_text(data)
            tmp.replace(self.path)
        except (FileNotFoundError, OSError):
            try:
                self.path.write_text(data)
            except OSError as exc:
                # Disk full, permissions, a vanished directory. A TypeError
                # from unserialisable state is a bug and must not be silenced.
                print(f"  WARNING: manifest save failed ({exc}); continuing")

    def is_done(self, video_id: str) -> bool:
        e = self.entries.get(video_id)
        return e is not None and e.status == COMPLETED

    def get(self, video_id: str) -> Entry | None:
        return self.entries.get(video_id)

    def record(self, entry: Entry) -> None:
        entry.processed_at = entry.processed_at or _now()
        self.entries[entry.video_id] = entry

    def mark_seen(self, video_ids: list[str]) -> None:
        stamp = _now()
        for vid in video_ids:
            if vid in self.entries:
                self.entries[vid].last_seen_in_playlist = stamp

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries.values():
            out[e.status] = out.get(e.status, 0) + 1
        return out

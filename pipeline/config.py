"""
config.py - load and validate config.toml.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

# Anchored to the repository, not to a fixed home directory path.
# Hardcoding ~/Documents made this work only on one machine, and
# put working state inside a cloud-synced folder — which once
# evicted a temp file mid-write and ended an eight-hour run.
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.toml"


@dataclass
class Config:
    playlist_url: str
    cookies_from_browser: str
    folder_id: str
    file_format: str
    account: str
    delete_removed: bool
    sync_renames: bool
    rate_limit_per_minute: int


def load(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise SystemExit(
            f"Missing {path}\nCopy config.example.toml to config.toml and edit it."
        )

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    src = raw.get("source", {})
    dst = raw.get("destination", {})
    beh = raw.get("behaviour", {})

    cfg = Config(
        playlist_url=src.get("playlist_url", "").strip(),
        cookies_from_browser=src.get("cookies_from_browser", "").strip(),
        folder_id=dst.get("folder_id", "").strip(),
        file_format=dst.get("file_format", "txt").strip().lower(),
        account=raw.get("auth", {}).get("account", "default").strip(),
        delete_removed=bool(beh.get("delete_removed", False)),
        sync_renames=bool(beh.get("sync_renames", True)),
        rate_limit_per_minute=int(beh.get("rate_limit_per_minute", 20)),
    )

    if not cfg.playlist_url:
        raise SystemExit("config.toml: source.playlist_url is empty")
    if not cfg.folder_id:
        raise SystemExit("config.toml: destination.folder_id is empty")
    if cfg.file_format not in ("txt", "gdoc"):
        raise SystemExit(
            f'config.toml: destination.file_format is "{cfg.file_format}"; '
            'expected "txt" or "gdoc"'
        )

    return cfg

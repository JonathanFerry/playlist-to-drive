"""
fetch.py - YouTube access via yt-dlp.

Captions are written to a temporary directory and read back, then the
directory is removed. yt-dlp has no stdout mode for subtitles, so this is
the closest available to "nothing persists locally".
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

TIMEOUT = 300


@dataclass
class Video:
    video_id: str
    title: str


class Unavailable(Exception):
    """Video is private, deleted, or otherwise not retrievable."""


class NoCaptions(Exception):
    """Video exists but has no usable caption track."""


class RateLimited(Exception):
    """YouTube is throttling. Says nothing about the video itself.

    Distinct from Unavailable because it is transient and the video is
    fine. Conflating the two is actively harmful over a long run: a
    sustained throttle would mark hundreds of good videos unavailable,
    and since 'unavailable' is a legitimate per-video outcome it would
    never trip the circuit breaker.
    """


RATE_LIMIT_MARKERS = (
    "http error 429",
    "too many requests",
    "sign in to confirm you're not a bot",
    "rate-limited",
    "rate limited",
)


def _is_rate_limited(stderr: str) -> bool:
    low = stderr.lower()
    return any(marker in low for marker in RATE_LIMIT_MARKERS)


def _base_cmd(cookies_browser: str) -> list[str]:
    cmd = ["yt-dlp"]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    return cmd


def playlist(url: str, cookies_browser: str = "") -> list[Video]:
    """Enumerate the playlist. Raises if it cannot be read at all.

    An empty result is treated as an error rather than an empty playlist,
    because a failed fetch and a genuinely empty playlist look identical
    and the difference matters.
    """
    cmd = _base_cmd(cookies_browser) + [
        "--flat-playlist", "--print", "%(id)s\t%(title)s", url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)

    videos = []
    for line in proc.stdout.splitlines():
        if "\t" in line:
            vid, title = line.split("\t", 1)
            videos.append(Video(vid.strip(), title.strip()))

    if not videos:
        raise Unavailable(
            "Playlist returned no videos. Check the URL is correct and the "
            "playlist is public or unlisted.\n" + proc.stderr.strip()[:400]
        )
    return videos


def _why(stderr: str) -> str:
    low = stderr.lower()
    if "private" in low:
        return "private video"
    if "unavailable" in low or "removed" in low or "terminated" in low:
        return "deleted or removed"
    if "age" in low and "confirm" in low:
        return "age-restricted"
    first = stderr.strip().splitlines()
    return first[-1][:160] if first else "unavailable"


def captions(video_id: str, cookies_browser: str = "") -> str:
    """Return the raw English VTT for a video.

    Prefers manual captions over auto-generated when both exist. Raises
    NoCaptions if neither is available, which is a normal outcome rather
    than an error - roughly 5% of videos.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.TemporaryDirectory(prefix="tp-") as tmp:
        tmpdir = Path(tmp)
        cmd = _base_cmd(cookies_browser) + [
            "--skip-download",
            "--write-sub", "--write-auto-sub",
            "--sub-lang", "en",
            "--sub-format", "vtt",
            "-o", str(tmpdir / "%(id)s"),
            url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)

        files = sorted(tmpdir.glob("*.vtt"))
        if not files:
            if _is_rate_limited(proc.stderr):
                raise RateLimited("YouTube is throttling caption requests")
            if proc.returncode != 0:
                raise Unavailable(_why(proc.stderr))
            raise NoCaptions("no English caption track")

        # A manually-uploaded track has no ".auto." marker and is preferred.
        manual = [f for f in files if "auto" not in f.name.lower()]
        chosen = manual[0] if manual else files[0]
        return chosen.read_text(encoding="utf-8", errors="replace")

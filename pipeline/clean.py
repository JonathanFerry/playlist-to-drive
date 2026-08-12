"""
clean.py - WebVTT to clean prose.

YouTube auto-captions emit each cue as <tail of previous> + <new text>,
so naive concatenation repeats most sentences two or three times. Three
rules, applied over a sliding window:

  1. exact match       - drop a line already seen recently
  2. containment       - drop a line wholly inside a recent line
  3. superset replace  - if a new line contains a recent one, replace it

The window matters: a global check would wrongly drop genuine repetition
(a speaker repeating themselves for emphasis), which is a real feature of
spoken transcripts rather than an artefact.
"""

from __future__ import annotations

import html
import re

WINDOW = 12

TIMING_TAG = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")
ANY_TAG = re.compile(r"<[^>]+>")
CUE_TIME = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}\s+-->")
SPEAKER_MARK = re.compile(r"^\s*(-\s*)?\[[^\]]*\]\s*")
NONSPEECH = re.compile(r"\[(music|applause|laughter|inaudible|clears throat)[^\]]*\]", re.I)
TURN_MARK = re.compile(r">>\s*")
WS = re.compile(r"\s+")
PUNCT_SPACE = re.compile(r"\s+([,.!?;:])")


def _clean_line(raw: str) -> str:
    s = TIMING_TAG.sub("", raw)
    s = ANY_TAG.sub("", s)
    s = html.unescape(s)
    s = NONSPEECH.sub("", s)
    s = SPEAKER_MARK.sub("", s)
    s = TURN_MARK.sub("", s)
    s = WS.sub(" ", s).strip()
    return PUNCT_SPACE.sub(r"\1", s)


def _parse(vtt: str) -> list[str]:
    lines = []
    for raw in vtt.splitlines():
        raw = raw.rstrip()
        if not raw:
            continue
        if raw.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if CUE_TIME.search(raw) or raw.strip().isdigit():
            continue
        cleaned = _clean_line(raw)
        if cleaned:
            lines.append(cleaned)
    return lines


def _dedup(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        window = out[-WINDOW:]
        low = line.lower()

        if any(low == w.lower() for w in window):
            continue
        if any(low in w.lower() for w in window):
            continue

        replaced = False
        start = max(len(out) - WINDOW, 0)
        for i in range(len(out) - 1, start - 1, -1):
            if out[i].lower() in low:
                out[i] = line
                replaced = True
                break
        if replaced:
            continue

        out.append(line)
    return out


def vtt_to_text(vtt: str) -> tuple[str, dict]:
    """Return (prose, stats). Stats are for reporting, not control flow."""
    raw = _parse(vtt)
    kept = _dedup(raw)
    body = WS.sub(" ", " ".join(kept)).strip()
    body = PUNCT_SPACE.sub(r"\1", body)
    return body, {
        "raw_lines": len(raw),
        "kept_lines": len(kept),
        "words": len(body.split()),
    }

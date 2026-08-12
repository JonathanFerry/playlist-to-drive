#!/usr/bin/env python3
"""
audit.py - what was captured, what was not, and why.

Reads the manifests and writes a report. Intended to be handed to
whoever asked for the transcripts, so it names every video that did not
make it and says plainly whether anything can be done about it.

The distinction that matters is recoverable versus not. A deleted video
is gone; a rate-limited one is a healthy video that YouTube happened to
be throttling. Reporting both as "failed" would be useless.

    .venv/bin/python audit.py              # print
    .venv/bin/python audit.py -o FILE.md   # write markdown
"""


from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SKIP = {"credentials.json", "token.json", "config.json"}

# Manifests from unrelated work that should not appear in a report about
# this batch: 'manifest' is the three-video test folder, 'production' is a
# separate personal archive. Override with --include.
EXCLUDE_STEMS = {"manifest", "production"}


def classify(entry: dict) -> tuple[str, bool]:
    """Return (human reason, recoverable).

    The title is checked before the note. YouTube replaces the title of a
    video you cannot see with a literal '[Private video]' or '[Deleted
    video]', which is authoritative — whereas yt-dlp's error text for the
    same video says "Please sign in", suggesting cookies would help. They
    do not: cookies for your account cannot open a video private to
    someone else's. Reading the note first reported three dead videos as
    recoverable and sent us chasing them.
    """
    status = entry.get("status", "?")
    note = (entry.get("note") or "").lower()
    title = (entry.get("title") or "").lower()

    if status == "completed":
        return "captured", True

    # Title is authoritative when YouTube has substituted a placeholder.
    if "[private video]" in title:
        return "private video", False
    if "[deleted video]" in title or "[unavailable video]" in title:
        return "deleted or removed", False

    if status == "no_captions":
        return "no captions on YouTube", False
    if "po token" in note or "po_token" in note:
        return "needs a PO token (YouTube anti-bot)", False
    if "rate limit" in note or "429" in note or "throttl" in note:
        return "rate limited by YouTube", True
    if "private" in note:
        return "private video", False
    if "deleted" in note or "removed" in note or "unavailable" in note:
        return "deleted or removed", False
    if "sign in" in note or "bot" in note:
        return "sign-in required", True
    if "age" in note:
        return "age-restricted", True
    if "live stream" in note:
        return "live stream not available", False
    if status == "failed":
        return f"error: {(entry.get('note') or '')[:60]}", True
    return f"{status}: {(entry.get('note') or '')[:60]}", False


def build() -> list[str]:
    out = ["# Transcript capture report",
           "", f"Generated {datetime.now():%Y-%m-%d %H:%M}", ""]

    manifests = sorted(p for p in BASE.glob("*.json")
                       if p.name not in SKIP and p.stem not in EXCLUDE_STEMS)
    grand = Counter()
    per_playlist = []

    for path in manifests:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        entries = data.get("entries", {})
        if not entries:
            continue

        reasons = Counter()
        misses = []
        for vid, e in entries.items():
            reason, recoverable = classify(e)
            reasons[reason] += 1
            grand[reason] += 1
            if reason != "captured":
                misses.append((vid, e.get("title", ""), reason, recoverable))

        total = len(entries)
        ok = reasons["captured"]
        per_playlist.append((path.stem, total, ok, misses))

    return out, per_playlist, grand


def render() -> str:
    out, per_playlist, grand = build()

    tot_seen = sum(t for _, t, _, _ in per_playlist)
    tot_ok = sum(o for _, _, o, _ in per_playlist)
    pct = 100 * tot_ok / tot_seen if tot_seen else 0

    out += ["## Summary", "",
            f"**{tot_ok:,} transcripts captured from {tot_seen:,} videos "
            f"({pct:.1f}%).**", "",
            "| Playlist | Videos | Captured | Rate |",
            "|---|---:|---:|---:|"]
    for name, total, ok, _ in per_playlist:
        r = 100 * ok / total if total else 0
        out.append(f"| {name} | {total:,} | {ok:,} | {r:.1f}% |")
    out += [f"| **Total** | **{tot_seen:,}** | **{tot_ok:,}** | **{pct:.1f}%** |", ""]

    out += ["## Why videos were not captured", "",
            "| Reason | Count | Recoverable |", "|---|---:|---|"]
    for reason, n in grand.most_common():
        if reason == "captured":
            continue
        rec = "no"
        if reason in ("rate limited by YouTube", "sign-in required", "age-restricted"):
            rec = "yes"
        elif reason.startswith("error:"):
            rec = "retry"
        out.append(f"| {reason} | {n} | {rec} |")
    out.append("")
    return out, per_playlist


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", default="")
    ap.add_argument("--full", action="store_true",
                    help="list every uncaptured video, not just a sample")
    args = ap.parse_args()

    out, per_playlist = render()

    out += ["## Videos not captured", ""]
    for name, total, ok, misses in per_playlist:
        if not misses:
            continue
        out += [f"### {name} ({len(misses)} of {total})", ""]
        misses.sort(key=lambda m: (not m[3], m[2]))
        shown = misses if args.full else misses[:40]
        for vid, title, reason, recoverable in shown:
            flag = " **[recoverable]**" if recoverable else ""
            out.append(f"- `{vid}` {title[:70]} — {reason}{flag}")
        if len(misses) > len(shown):
            out.append(f"- _...and {len(misses) - len(shown)} more "
                       f"(use --full to list all)_")
        out.append("")

    text = "\n".join(out)
    if args.output:
        Path(args.output).write_text(text)
        print(f"wrote {args.output} ({len(text.splitlines())} lines)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

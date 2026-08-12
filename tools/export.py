#!/usr/bin/env python3
"""
export.py - pull transcripts out of Drive as plain text.

Google Docs export to text/plain natively, so this fetches exactly what
the pipeline uploaded. Downloading a .docx and converting it back would
round-trip through a zipped XML format nobody in this chain wants, and
risks style metadata bleeding into the text.

Idempotent: a file already present and non-empty is skipped, so an
interrupted export resumes rather than restarting.

    python tools/export.py --manifest newright.json --out ./export
    python tools/export.py --manifest newright.json --out ./export --apply
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json
import re
import time

from pipeline.auth import drive_service
from pipeline.resilience import with_retry

BASE = Path(__file__).resolve().parent.parent

# Characters a filesystem will not accept, plus the control range.
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(title: str, video_id: str, seen: set[str]) -> str:
    """A filesystem-safe name that stays unique.

    Two videos can share a title, so a collision appends the video ID
    rather than letting the second export overwrite the first.
    """
    name = ILLEGAL.sub("", title).strip().rstrip(".")
    name = re.sub(r"\s+", " ", name)[:150] or video_id

    candidate = f"{name}.txt"
    if candidate.lower() in seen:
        candidate = f"{name} [{video_id}].txt"
    seen.add(candidate.lower())
    return candidate


def export_one(drive, file_id: str) -> str:
    """Fetch a Google Doc as plain text."""
    data = with_retry(
        lambda: drive.files().export(fileId=file_id, mimeType="text/plain").execute(),
        label="export",
    )
    text = data.decode("utf-8") if isinstance(data, bytes) else str(data)

    # Drive prepends a UTF-8 BOM to text exports. Invisible in a word
    # processor, but for machine ingestion it is a stray leading character
    # on every file and it corrupts the first header field for anything
    # that parses one.
    text = text.lstrip("\ufeff")

    # Drive also uses CRLF line endings on export.
    return text.replace("\r\n", "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, help="e.g. newright.json")
    ap.add_argument("--out", required=True, help="destination directory")
    ap.add_argument("--apply", action="store_true", help="actually write files")
    ap.add_argument("--rate", type=int, default=30,
                    help="exports per minute (default 30)")
    args = ap.parse_args()

    manifest_path = BASE / args.manifest
    if not manifest_path.exists():
        print(f"missing manifest: {manifest_path}")
        return 1

    data = json.loads(manifest_path.read_text())
    done = [e for e in data.get("entries", {}).values()
            if e.get("status") == "completed" and e.get("drive_file_id")]

    out_dir = Path(args.out).expanduser().resolve()
    print(f"{'[APPLY]' if args.apply else '[DRY RUN]'} {args.manifest}")
    print(f"  transcripts : {len(done)}")
    print(f"  destination : {out_dir}\n")

    if not done:
        print("  nothing to export")
        return 0

    if not args.apply:
        seen: set[str] = set()
        for e in done[:5]:
            print(f"    {safe_filename(e.get('title', ''), e['video_id'], seen)}")
        if len(done) > 5:
            print(f"    ... and {len(done) - 5} more")
        print("\nDry run — nothing written. Re-run with --apply.")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    drive = drive_service()
    delay = 60.0 / max(args.rate, 1)

    seen, written, skipped, failed = set(), 0, 0, []

    for i, entry in enumerate(done, 1):
        name = safe_filename(entry.get("title", ""), entry["video_id"], seen)
        target = out_dir / name

        # Idempotent: an interrupted export resumes rather than restarting.
        if target.exists() and target.stat().st_size > 0:
            skipped += 1
            continue

        try:
            target.write_text(export_one(drive, entry["drive_file_id"]),
                              encoding="utf-8")
            written += 1
        except Exception as exc:
            # Broad on purpose: one unexportable document must not end a
            # run of thousands. The failure is listed in the summary.
            failed.append((name, str(exc)[:70]))

        if i % 100 == 0:
            print(f"  {i}/{len(done)}  written={written} skipped={skipped}",
                  flush=True)
        time.sleep(delay)

    print(f"\n  written : {written}")
    print(f"  skipped : {skipped} (already present)")
    print(f"  failed  : {len(failed)}")
    for name, why in failed[:20]:
        print(f"    - {name[:50]} ({why})")
    if len(failed) > 20:
        print(f"    ... and {len(failed) - 20} more")

    total_bytes = sum(f.stat().st_size for f in out_dir.glob("*.txt"))
    print(f"\n  {len(list(out_dir.glob('*.txt'))):,} files, "
          f"{total_bytes / 1_048_576:.1f} MB in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

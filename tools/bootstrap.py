"""
bootstrap.py - build an initial manifest from documents already in Drive.

Reads each Google Doc in the destination folder, pulls the video ID from
the header's URL line, and records it as completed. Without this, the
first pipeline run would treat every existing transcript as missing and
reprocess the entire playlist.

Read-only against Drive. Writes only manifest.json, and only with --apply.

    python bootstrap.py                    # dry run, current config folder
    python bootstrap.py --apply            # write the manifest
    python bootstrap.py --folder-id XXX    # override the config folder
"""


from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import re
import subprocess
import sys

from pipeline import config as config_mod
from pipeline.auth import drive_service, docs_service
from pipeline.drive import list_docs, read_header
from pipeline.manifest import Manifest, Entry, COMPLETED

VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})")
WORDS_RE = re.compile(r"^Words:\s*(\d+)", re.M)
SOURCE_RE = re.compile(r"^Source:\s*(.+)$", re.M)
TITLE_RE = re.compile(r"^Title:\s*(.+)$", re.M)
HEADER_PARAGRAPHS = 8   # read enough to cover header + delimiter


def playlist_titles(playlist_url: str, cookies_browser: str = "") -> dict[str, str]:
    """Map normalised title -> video ID for every entry in the playlist.

    Used only as a fallback when a document's header carries a malformed
    video ID. Titles that appear more than once are dropped, so an
    ambiguous match is never silently resolved.
    """
    cmd = ["yt-dlp", "--flat-playlist", "--print", "%(id)s\t%(title)s"]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    cmd.append(playlist_url)

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  (playlist lookup unavailable: {exc})")
        return {}

    seen: dict[str, str] = {}
    duplicated: set[str] = set()
    for line in out.splitlines():
        if "\t" not in line:
            continue
        vid, title = line.split("\t", 1)
        key = norm_title(title)
        if key in seen:
            duplicated.add(key)
        seen[key] = vid
    for key in duplicated:
        seen.pop(key, None)
    return seen


def norm_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write manifest.json")
    ap.add_argument("--folder-id", default="", help="override config folder")
    ap.add_argument("--manifest", default="manifest.json")
    args = ap.parse_args()

    cfg = config_mod.load()
    folder_id = args.folder_id or cfg.folder_id

    drive, docs_api = drive_service(), docs_service()
    meta = drive.files().get(fileId=folder_id, fields="name").execute()

    print(f"{'[APPLY]' if args.apply else '[DRY RUN]'} folder: {meta['name']}")
    print(f"          id: {folder_id}\n")

    files = list_docs(drive, folder_id)
    print(f"Found {len(files)} Google Docs.\n")
    if not files:
        print("Nothing to bootstrap. An empty manifest is correct for a new folder.")

    manifest = Manifest.load_or_new(folder_id, args.manifest)
    matched, duplicates, unparsed = 0, [], []
    recovered = []
    seen_ids: dict[str, str] = {}
    title_index: dict[str, str] | None = None   # built lazily, only if needed

    for i, f in enumerate(files, 1):
        if i % 25 == 0:
            print(f"  ...{i}/{len(files)}")
        try:
            head = read_header(drive, docs_api, f["id"],
                               f.get("mimeType", ""))
        except Exception as exc:
            unparsed.append((f["name"], f"read error: {exc}"))
            continue

        m = VIDEO_ID_RE.search(head)
        if m:
            vid = m.group(1)
        else:
            # Header ID is malformed — the original extraction stripped
            # leading underscores, producing 10-character IDs. Fall back to
            # matching the document title against the playlist, which yields
            # the correct ID rather than a salvaged wrong one.
            if title_index is None:
                print("  (malformed header found; fetching playlist to recover)")
                title_index = playlist_titles(
                    cfg.playlist_url, cfg.cookies_from_browser
                )

            tm = TITLE_RE.search(head)
            doc_title = tm.group(1).strip() if tm else f["name"]
            vid = title_index.get(norm_title(doc_title), "")

            if not vid:
                unparsed.append((f["name"], "no video ID, and no unique title match"))
                continue
            recovered.append((f["name"], vid))
        if vid in seen_ids:
            duplicates.append((vid, seen_ids[vid], f["name"]))
            continue
        seen_ids[vid] = f["name"]

        words = WORDS_RE.search(head)
        source = SOURCE_RE.search(head)
        source_text = source.group(1).strip() if source else ""
        kind = "asr" if "whisper" in source_text.lower() else "captions"

        manifest.record(Entry(
            video_id=vid,
            status=COMPLETED,
            title=f["name"],
            drive_file_id=f["id"],
            source=kind,
            word_count=int(words.group(1)) if words else 0,
            note="bootstrapped from existing Drive document",
        ))
        matched += 1

    print(f"\nMatched to a video ID : {matched}")
    print(f"  of which recovered  : {len(recovered)} (via playlist title match)")
    print(f"Could not parse       : {len(unparsed)}")
    print(f"Duplicate video IDs   : {len(duplicates)}")

    if recovered:
        print("\nRecovered by title match — header IDs are malformed and should be repaired:")
        for name, vid in recovered[:20]:
            print(f"  - {name[:55]} -> {vid}")

    if unparsed:
        print("\nNeeds attention — these would be REPROCESSED on the next run:")
        for name, why in unparsed[:30]:
            print(f"  - {name[:60]} ({why})")
        if len(unparsed) > 30:
            print(f"  ... and {len(unparsed) - 30} more")

    if duplicates:
        print("\nTwo documents share a video ID:")
        for vid, first, second in duplicates[:20]:
            print(f"  - {vid}: {first[:40]} | {second[:40]}")

    by_source: dict[str, int] = {}
    for e in manifest.entries.values():
        by_source[e.source] = by_source.get(e.source, 0) + 1
    if by_source:
        print("\nBy source:", ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())))

    if args.apply:
        manifest.save()
        print(f"\nWrote {manifest.path} ({len(manifest.entries)} entries)")
    else:
        print("\nDry run — nothing written. Re-run with --apply to save.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

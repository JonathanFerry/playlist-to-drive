#!/usr/bin/env python3
"""
rename_to_titles.py - make filenames match the source titles exactly.

Files that reached Drive by way of a local disk carry filenames a
filesystem would accept, not the titles they came from: colons, slashes,
pipes and question marks were stripped on the way through. Drive has no
such restriction, so the stripping bought nothing and lost information.

Each file's own header records the title it was made from, so this needs
no manifest and works on any folder — including ones this tool did not
create.

    python tools/rename_to_titles.py --folder-id XXX
    python tools/rename_to_titles.py --folder-id XXX --apply
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import re
import time

from pipeline.auth import drive_service, docs_service
from pipeline.drive import list_docs, read_header
from pipeline.resilience import with_retry

TITLE_RE = re.compile(r"^Title:\s*(.+?)\s*$", re.M)


def wanted_name(header: str, current: str) -> str | None:
    """The filename this file should have, or None if it cannot be told."""
    m = TITLE_RE.search(header)
    if not m:
        return None
    title = m.group(1).strip()
    if not title:
        return None
    # Drive accepts everything except a forward slash, which it treats as
    # a path separator. Nothing else needs stripping.
    title = title.replace("/", "-")
    return title if title.endswith(".txt") else f"{title}.txt"


def rename(drive, file_id: str, name: str) -> None:
    with_retry(
        lambda: drive.files().update(
            fileId=file_id, body={"name": name},
            supportsAllDrives=True).execute(),
        label="rename",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder-id", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument("--rate", type=int, default=60, help="renames per minute")
    args = ap.parse_args()

    drive, docs_api = drive_service(), docs_service()

    meta = with_retry(lambda: drive.files().get(
        fileId=args.folder_id,
        fields="name,capabilities(canEdit,canAddChildren)",
        supportsAllDrives=True).execute(), label="meta")

    can_edit = meta.get("capabilities", {}).get("canEdit")
    print(f"{'[APPLY]' if args.apply else '[DRY RUN]'} {meta.get('name')}")
    print(f"  write access: {can_edit}")
    if args.apply and not can_edit:
        print("\n  This account cannot modify that folder. Either grant it")
        print("  editor access, or set auth.account in config.toml to a")
        print("  profile for an account that has it.")
        return 1

    files = list_docs(drive, args.folder_id)
    print(f"  files       : {len(files)}\n")

    changes, unchanged, unreadable, seen = [], 0, [], {}

    for i, f in enumerate(files, 1):
        if i % 200 == 0:
            print(f"  ...read {i}/{len(files)}", flush=True)
        try:
            head = read_header(drive, docs_api, f["id"], f.get("mimeType", ""))
        except Exception as exc:
            unreadable.append((f["name"], str(exc)[:60]))
            continue

        want = wanted_name(head, f["name"])
        if not want:
            unreadable.append((f["name"], "no Title line in header"))
            continue

        # Two videos can share a title. Appending the id keeps the second
        # from silently taking the first one's name.
        key = want.lower()
        if key in seen:
            m = re.search(r"^Video ID:\s*(\S+)", head, re.M)
            if m:
                want = f"{want[:-4]} [{m.group(1)}].txt"
        seen[key] = True

        if want == f["name"]:
            unchanged += 1
        else:
            changes.append((f["id"], f["name"], want))

    print(f"\n  already correct : {unchanged}")
    print(f"  to rename       : {len(changes)}")
    print(f"  unreadable      : {len(unreadable)}")

    for name, why in unreadable[:5]:
        print(f"    - {name[:52]} ({why})")

    if changes:
        print("\n  examples:")
        for _fid, old, new in changes[: args.show]:
            print(f"    {old[:60]}")
            print(f"    -> {new[:60]}")
        if len(changes) > args.show:
            print(f"    ... and {len(changes) - args.show} more")

    if not args.apply:
        print("\nDry run — nothing renamed. Re-run with --apply.")
        return 0

    if not changes:
        print("\n  nothing to do")
        return 0

    print()
    delay = 60.0 / max(args.rate, 1)
    done, failed = 0, []
    for fid, old, new in changes:
        try:
            rename(drive, fid, new)
            done += 1
            if done % 100 == 0:
                print(f"  renamed {done}/{len(changes)}", flush=True)
        except Exception as exc:
            failed.append((old, str(exc)[:70]))
        time.sleep(delay)

    print(f"\n  renamed : {done} of {len(changes)}")
    print(f"  failed  : {len(failed)}")
    for old, why in failed[:10]:
        print(f"    - {old[:50]} ({why})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

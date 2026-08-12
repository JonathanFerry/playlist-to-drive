#!/usr/bin/env python3
"""
dedupe.py - remove duplicate transcripts from a Drive folder.

Duplicates arise when two pipeline instances process the same playlist
into the same folder concurrently. Each writes a complete transcript, so
nothing is lost — the folder just holds two copies of everything.

Keeps the copy the manifest references, because that is the one the
pipeline will recognise on future runs. Where the manifest references
neither, keeps the oldest.

Duplicates are moved to TRASH, not deleted. Drive holds trash for 30
days, so a mistake here is recoverable; a permanent delete would not be.

    python dedupe.py --manifest newright.json --folder-id XXX
    python dedupe.py --manifest newright.json --folder-id XXX --apply
"""


from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from pipeline.auth import drive_service
from pipeline.drive import list_docs
from pipeline.resilience import with_retry

BASE = Path(__file__).resolve().parent.parent


def choose_keeper(group: list[dict], known: set[str]) -> dict:
    """Which copy to keep.

    The manifest's copy wins: it is the one the pipeline will recognise,
    so keeping any other would leave the manifest pointing at a trashed
    file and trigger a reprocess on the next run. Failing that, oldest.
    """
    for doc in group:
        if doc["id"] in known:
            return doc
    return min(group, key=lambda d: d.get("createdTime", ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--folder-id", required=True)
    ap.add_argument("--apply", action="store_true", help="actually trash")
    ap.add_argument("--show", type=int, default=8, help="examples to print")
    args = ap.parse_args()

    manifest_path = BASE / args.manifest
    if not manifest_path.exists():
        print(f"missing manifest: {manifest_path}")
        return 1
    data = json.loads(manifest_path.read_text())
    known = {e.get("drive_file_id") for e in data.get("entries", {}).values()
             if e.get("drive_file_id")}

    drive = drive_service()
    docs = list_docs(drive, args.folder_id)

    by_name: dict[str, list[dict]] = defaultdict(list)
    for doc in docs:
        by_name[doc["name"]].append(doc)

    to_trash: list[dict] = []
    orphan_keeps = 0
    for name, group in by_name.items():
        if len(group) < 2:
            continue
        keeper = choose_keeper(group, known)
        if keeper["id"] not in known:
            orphan_keeps += 1
        to_trash.extend(d for d in group if d["id"] != keeper["id"])

    print(f"{'[APPLY]' if args.apply else '[DRY RUN]'} {args.manifest}")
    print(f"  documents in Drive : {len(docs)}")
    print(f"  unique titles      : {len(by_name)}")
    print(f"  duplicates to trash: {len(to_trash)}")
    print(f"  manifest file ids  : {len(known)}")
    if orphan_keeps:
        print(f"  kept oldest copy where the manifest referenced neither: {orphan_keeps}")

    if not to_trash:
        print("\n  nothing to do")
        return 0

    print("\n  examples:")
    for doc in to_trash[: args.show]:
        print(f"    {doc.get('createdTime','?')}  {doc['name'][:52]}")
    if len(to_trash) > args.show:
        print(f"    ... and {len(to_trash) - args.show} more")

    if not args.apply:
        print("\nDry run — nothing trashed. Re-run with --apply.")
        return 0

    print()
    done = 0
    for doc in to_trash:
        try:
            with_retry(lambda: drive.files().update(
                fileId=doc["id"], body={"trashed": True}).execute(),
                label="trash")
            done += 1
            if done % 100 == 0:
                print(f"  trashed {done}/{len(to_trash)}", flush=True)
        except Exception as exc:
            print(f"  FAILED {doc['name'][:40]}: {str(exc)[:70]}")

    print(f"\n  trashed {done} of {len(to_trash)}")
    print("  Recoverable from Drive trash for 30 days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

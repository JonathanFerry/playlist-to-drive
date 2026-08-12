"""
normalize_typography.py - apply the canonical typography to a folder.

New uploads are formatted at write time, so this is only needed for
documents created before that existed, or to repair manual edits.

Font and size alone are not enough. Converting plain text to a Google Doc
applies the default NORMAL_TEXT style, which carries 1.15 line spacing and
space after each paragraph. Those survive a font change, which is why
manually reformatted documents end up loosely spaced.

    python normalize_typography.py                    # dry run
    python normalize_typography.py --apply
    python normalize_typography.py --folder-id XXX
"""


from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import sys

from pipeline import config as config_mod
from pipeline import drive as drive_mod
from pipeline import format as fmt
from pipeline.auth import drive_service, docs_service
from pipeline.drive import list_docs


def audit(docs_api, file_id: str) -> list[str]:
    """Report which typography properties are non-compliant.

    Every paragraph is checked, including the trailing empty one. It is
    invisible in the rendered document but Cmd+A selects it, so a stale
    Arial 11 there makes the toolbar show blank font and size fields and
    no colour swatch, because the selection spans mixed values.
    """
    doc = docs_api.documents().get(documentId=file_id).execute()
    issues = set()

    for element in doc.get("body", {}).get("content", []):
        para = element.get("paragraph")
        if not para:
            continue

        style = para.get("paragraphStyle", {})
        if style.get("lineSpacing", fmt.LINE_SPACING) != fmt.LINE_SPACING:
            issues.add("line spacing")
        for key, want in (("spaceAbove", fmt.SPACE_ABOVE_PT),
                          ("spaceBelow", fmt.SPACE_BELOW_PT)):
            if style.get(key, {}).get("magnitude", 0) != want:
                issues.add("paragraph spacing")

        for run in para.get("elements", []):
            ts = run.get("textRun", {}).get("textStyle", {})
            fam = ts.get("weightedFontFamily", {}).get("fontFamily")
            if fam and fam != fmt.FONT_FAMILY:
                issues.add("font")
            size = ts.get("fontSize", {}).get("magnitude")
            if size and size != fmt.FONT_SIZE_PT:
                issues.add("size")

            # An explicit colour is non-compliant even when it renders
            # identically to the default. The picker shows the stored
            # value, and an explicit colour blocks future style changes
            # from propagating.
            if "foregroundColor" in ts:
                issues.add("explicit text colour")
            if "backgroundColor" in ts:
                issues.add("explicit highlight")

    return sorted(issues)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--folder-id", default="")
    args = ap.parse_args()

    cfg = config_mod.load()
    folder_id = args.folder_id or cfg.folder_id
    drive, docs_api = drive_service(), docs_service()

    name = drive.files().get(fileId=folder_id, fields="name").execute()["name"]
    print(f"{'[APPLY]' if args.apply else '[DRY RUN]'} {name}")
    print(f"  target: {fmt.FONT_FAMILY} {fmt.FONT_SIZE_PT}pt, "
          f"single spacing, no paragraph spacing\n")

    files = list_docs(drive, folder_id)
    print(f"{len(files)} documents\n")

    changed, already, failed = 0, 0, []

    for i, f in enumerate(files, 1):
        if i % 25 == 0:
            print(f"  ...{i}/{len(files)}")
        try:
            issues = audit(docs_api, f["id"])
            if not issues:
                already += 1
                continue
            print(f"  {f['name'][:52]:54s} {', '.join(issues)}")
            if args.apply:
                drive_mod.apply_typography(docs_api, f["id"])
            changed += 1
        except Exception as exc:
            failed.append((f["name"], str(exc)[:90]))

    print("\n" + "=" * 60)
    print(f"Already compliant : {already}")
    print(f"{'Reformatted' if args.apply else 'Would reformat'}       : {changed}")
    print(f"Failed            : {len(failed)}")

    for fname, why in failed[:20]:
        print(f"  - {fname[:50]} ({why})")

    if not args.apply and changed:
        print("\nDry run — nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

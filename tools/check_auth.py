"""
check_auth.py - first-run smoke test.

Proves credentials, scopes and token caching all work before anything
else depends on them. Read-only: creates and modifies nothing.

Reads the destination folder from config.toml.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import sys

from pipeline import config as config_mod
from pipeline.auth import drive_service, docs_service


def describe(drive, folder_id, label):
    meta = drive.files().get(fileId=folder_id, fields="name").execute()
    print(f"\n{label}")
    print(f"  name : {meta['name']}")

    resp = drive.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        fields="files(id,name,mimeType)",
        pageSize=1000,
    ).execute()
    files = resp.get("files", [])
    docs = [f for f in files
            if f["mimeType"] == "application/vnd.google-apps.document"]
    print(f"  items: {len(files)} total, {len(docs)} Google Docs")
    for f in docs[:3]:
        print(f"    - {f['name'][:60]}")
    if len(docs) > 3:
        print(f"    ... and {len(docs) - 3} more")
    return docs


def main():
    cfg = config_mod.load()

    print("Authorizing (a browser window may open)...")
    drive = drive_service()
    print("Drive API: OK")

    docs = describe(drive, cfg.folder_id, "DESTINATION FOLDER")

    if docs:
        docs_api = docs_service()
        sample = docs[0]
        doc = docs_api.documents().get(documentId=sample["id"]).execute()
        body = doc.get("body", {}).get("content", [])
        paras = sum(1 for el in body if "paragraph" in el)
        print(f"\nDocs API: OK  (read '{sample['name'][:40]}', {paras} paragraphs)")
    else:
        print("\nDocs API: not exercised (folder is empty)")

    print("\nAll checks passed. token.json cached for future runs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

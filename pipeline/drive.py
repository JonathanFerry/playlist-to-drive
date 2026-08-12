"""
drive.py - Google Drive upload and maintenance.

Plain text is uploaded with a Google Docs target mime type, which makes
Drive convert it to an editable, searchable document. The text is built
by format.build_document, so the header format lives in exactly one place.
"""

from __future__ import annotations

import io

from googleapiclient.http import MediaIoBaseUpload

from pipeline.resilience import with_retry

DOC_MIME = "application/vnd.google-apps.document"


def upload_document(drive, folder_id: str, title: str, text: str,
                    as_doc: bool = False) -> str:
    """Upload a transcript. Returns the new file ID.

    as_doc=False uploads plain text and leaves it as plain text, which is
    what a downstream consumer wants: no conversion, no markup, readable
    without a parser. Exporting a Google Doc back to text later means a
    round trip through a format nobody in that chain asked for.

    as_doc=True converts to a Google Doc — readable in a browser and
    formattable, at the cost of that round trip.
    """
    name = title if as_doc or title.endswith(".txt") else f"{title}.txt"

    def _do():
        media = MediaIoBaseUpload(
            io.BytesIO(text.encode("utf-8")),
            mimetype="text/plain",
            resumable=False,
        )
        body = {"name": name, "parents": [folder_id]}
        if as_doc:
            # Naming the Docs mime type on create is what triggers Drive's
            # conversion. Omitting it keeps the upload as text/plain.
            body["mimeType"] = DOC_MIME
        return drive.files().create(
            body=body, media_body=media, fields="id",
        ).execute()

    return with_retry(_do, label="upload")["id"]


def get_name(drive, file_id: str) -> str:
    return drive.files().get(fileId=file_id, fields="name").execute()["name"]


def rename(drive, file_id: str, new_title: str, as_doc: bool = False) -> None:
    """Rename the Drive file and the in-document Title line together.

    Both derive from the same normalized string, so they cannot drift.
    """
    name = new_title if as_doc or new_title.endswith(".txt") else f"{new_title}.txt"
    with_retry(lambda: drive.files().update(
        fileId=file_id, body={"name": name}).execute(), label="rename")


def update_title_line(docs_api, file_id: str, old_title: str, new_title: str) -> None:
    """Replace the header's Title: line to match a renamed document."""
    if old_title == new_title:
        return
    docs_api.documents().batchUpdate(
        documentId=file_id,
        body={"requests": [{"replaceAllText": {
            "containsText": {"text": f"Title: {old_title}", "matchCase": True},
            "replaceText": f"Title: {new_title}",
        }}]},
    ).execute()


def list_docs(drive, folder_id: str, with_created: bool = False) -> list[dict]:
    """Every Google Doc in a folder, paginated.

    Lived in two places before — bootstrap and dedupe each had a copy —
    which is one copy too many for a Drive operation.
    """
    fields = "nextPageToken, files(id,name,mimeType" + (",createdTime" if with_created else "") + ")"
    docs, token = [], None
    while True:
        resp = with_retry(lambda: drive.files().list(
            q=(f"'{folder_id}' in parents and trashed = false and ("
               "mimeType = 'application/vnd.google-apps.document' or "
               "mimeType = 'text/plain')"),
            fields=fields,
            pageSize=200,
            pageToken=token,
        ).execute(), label="list")
        docs.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            return docs


def read_header(drive, docs_api, file_id: str, mime: str = "",
                max_chars: int = 900) -> str:
    """The leading text of a transcript, whichever format it is stored in.

    A plain-text file is downloaded directly; a Google Doc has to be
    exported. Callers should not have to know which they are holding.
    """
    if not mime:
        mime = with_retry(
            lambda: drive.files().get(fileId=file_id, fields="mimeType").execute(),
            label="meta")["mimeType"]

    if mime == DOC_MIME:
        raw = with_retry(
            lambda: drive.files().export(fileId=file_id,
                                         mimeType="text/plain").execute(),
            label="export")
    else:
        raw = with_retry(
            lambda: drive.files().get_media(fileId=file_id).execute(),
            label="download")

    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    return text.lstrip("\ufeff").replace("\r\n", "\n")[:max_chars]

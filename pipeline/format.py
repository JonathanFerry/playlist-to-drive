"""
format.py - the canonical transcript format, defined exactly once.

Every write path imports from here: new documents, renames, header
updates. There is deliberately no second definition, because the two
defects found while building this both came from format decisions made
at the write site and repaired downstream.

Header:
    Title: ...
    Video ID: ...
    URL: https://www.youtube.com/watch?v=...
    Words: N
    Source: ...
    ------------------------------------------------------------
    <body as one flowing paragraph, no hard wrapping>

Body is never hard-wrapped. Fixed-width wrapping turns every line into
its own paragraph on import to Google Docs.
"""

from __future__ import annotations

import re
import unicodedata

DELIMITER = "-" * 60
SOURCE_CAPTIONS = "YouTube captions via yt-dlp"
SOURCE_ASR = "Whisper large-v3-turbo (no YouTube captions available)"


# Download tools substitute fullwidth Unicode homoglyphs for characters
# that are illegal in filenames, rather than stripping them. Map them back
# so titles match the source and sort predictably.
HOMOGLYPHS = {
    "\uff1a": ":",   # ：
    "\uff1f": "?",   # ？
    "\uff0f": "/",   # ／
    "\uff3c": "\\",  # ＼
    "\uff5c": "|",   # ｜
    "\uff1c": "<",   # ＜
    "\uff1e": ">",   # ＞
    "\uff0a": "*",   # ＊
    "\uff02": '"',   # ＂
}

WS = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Canonical form for both the Drive filename and the header Title."""
    text = unicodedata.normalize("NFC", title)
    for bad, good in HOMOGLYPHS.items():
        text = text.replace(bad, good)
    text = WS.sub(" ", text).strip()
    return text


def build_document(title: str, video_id: str, body: str, source: str) -> str:
    """Assemble the full document text. The only place this is done."""
    clean_title = normalize_title(title)
    flowing = WS.sub(" ", body).strip()
    words = len(flowing.split())

    header = [
        f"Title: {clean_title}",
        f"Video ID: {video_id}",
        f"URL: https://www.youtube.com/watch?v={video_id}",
        f"Words: {words}",
        f"Source: {source}",
        DELIMITER,
    ]
    return "\n".join(header) + "\n" + flowing + "\n"


def parse_header(text: str) -> dict:
    """Read a header back out of an existing document."""
    fields = {}
    for line in text.splitlines():
        if line.startswith(DELIMITER[:20]):
            break
        if ": " in line:
            key, _, value = line.partition(": ")
            fields[key.strip()] = value.strip()
    return fields


# Typography, applied at upload so no repair pass is ever needed.
#
# Converting plain text to a Google Doc applies the default NORMAL_TEXT
# style, which carries 1.15 line spacing and space after each paragraph.
# Setting font and size alone leaves both in place, which is why manually
# reformatted documents end up looking loosely spaced. All four properties
# have to be set together.
FONT_FAMILY = "Times New Roman"
FONT_SIZE_PT = 12
LINE_SPACING = 100      # percent; 100 = single
SPACE_ABOVE_PT = 0
SPACE_BELOW_PT = 0


def typography_requests(end_index: int) -> list[dict]:
    """Docs API requests to normalise typography across a whole document.

    Colour is CLEARED rather than set. Listing a property in the fields
    mask while omitting it from textStyle resets it to the inherited
    default — so the colour picker shows default black and the highlight
    picker shows None, rather than an explicit value that merely looks
    identical. Apps Script cannot do this; the raw API can.
    """
    span = {"startIndex": 1, "endIndex": end_index}
    return [
        {"updateTextStyle": {
            "range": span,
            "textStyle": {
                "weightedFontFamily": {"fontFamily": FONT_FAMILY},
                "fontSize": {"magnitude": FONT_SIZE_PT, "unit": "PT"},
            },
            # foregroundColor and backgroundColor are named but unset,
            # which clears them.
            "fields": "weightedFontFamily,fontSize,foregroundColor,backgroundColor",
        }},
        {"updateParagraphStyle": {
            "range": span,
            "paragraphStyle": {
                "lineSpacing": LINE_SPACING,
                "spaceAbove": {"magnitude": SPACE_ABOVE_PT, "unit": "PT"},
                "spaceBelow": {"magnitude": SPACE_BELOW_PT, "unit": "PT"},
            },
            "fields": "lineSpacing,spaceAbove,spaceBelow",
        }},
    ]

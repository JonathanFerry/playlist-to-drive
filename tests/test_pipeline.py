"""Cases that are easy to get wrong silently."""

import sys
import json
import tempfile
import pathlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import format as fmt
from pipeline import clean
from pipeline.manifest import Manifest, Entry, COMPLETED
from tools import audit

fails = []
def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail and not cond else ''}")
    if not cond: fails.append(name)

# empty caption input
body, stats = clean.vtt_to_text("")
check("empty VTT does not crash", body == "" and stats['words'] == 0)

# VTT with only headers
body, _ = clean.vtt_to_text("WEBVTT\nKind: captions\nLanguage: en\n")
check("header-only VTT yields empty", body == "")

# title normalisation must not eat legitimate characters
check("keeps internal underscore", fmt.normalize_title("A_B test") == "A_B test")
check("strips fullwidth colon", fmt.normalize_title("A\uff1aB") == "A:B")
check("collapses whitespace", fmt.normalize_title("  a   b  ") == "a b")

# Titles are taken verbatim from yt-dlp, so a trailing underscore is real.
t = fmt.normalize_title("Episode 5_")
check("trailing underscore preserved", t == "Episode 5_", f"got {t!r}")

# document build with empty body
doc = fmt.build_document("T", "abc12345678", "", "src")
check("empty body still yields a header", "Words: 0" in doc)

# audit classify on placeholder titles
r, rec = audit.classify({"status": "unavailable", "title": "[Private video]",
                         "note": "Please sign in. Use --cookies-from-browser"})
check("private title beats sign-in note", r == "private video" and not rec, f"got {r}")

r, rec = audit.classify({"status": "unavailable", "title": "Real Title",
                         "note": "rate limited: throttling"})
check("genuine rate limit still recoverable", r == "rate limited by YouTube" and rec)

# manifest folder mismatch guard
with tempfile.TemporaryDirectory() as d:
    from pipeline import manifest as mm
    old = mm.BASE_DIR
    mm.BASE_DIR = pathlib.Path(d)
    m = Manifest(pathlib.Path(d) / "x.json", "FOLDER_A")
    m.record(Entry(video_id="v", status=COMPLETED))
    m.save()
    try:
        Manifest.load_or_new("FOLDER_B", "x.json")
        check("folder mismatch refuses to run", False, "no SystemExit raised")
    except SystemExit:
        check("folder mismatch refuses to run", True)
    mm.BASE_DIR = old


# ---------------------------------------------------------------- resilience
# The circuit breaker and the retry policy decide whether an eight-hour run
# survives a bad afternoon, so they are worth testing directly.

from pipeline.resilience import (
    CircuitBreaker, with_retry, QuotaExhausted, is_transient,
)
from googleapiclient.errors import HttpError
import pipeline.resilience as _res


class _Resp:
    def __init__(self, status):
        self.status = status
        self.reason = "test"


def _http(status):
    return HttpError(_Resp(status), b'{"error":{"message":"x"}}')


check("429 is transient", is_transient(_http(429)))
check("404 is not transient", not is_transient(_http(404)))

b = CircuitBreaker(consecutive_limit=3, total_limit=10)
for _ in range(20):
    b.record_success()
check("successes never trip the breaker", not b.tripped)
for i in range(3):
    b.record_failure(f"err{i}")
check("three consecutive failures trip it", b.tripped)

b2 = CircuitBreaker(consecutive_limit=3, total_limit=10)
b2.record_failure("a"); b2.record_failure("b")
b2.record_success(); b2.record_failure("c")
check("a success resets the consecutive count", not b2.tripped)

_res.MAX_ATTEMPTS, _res.BASE_DELAY = 2, 0.01
calls = {"n": 0}
def _always_429():
    calls["n"] += 1
    raise _http(429)
try:
    with_retry(_always_429)
    check("exhausted 429 raises QuotaExhausted", False)
except QuotaExhausted:
    check("exhausted 429 raises QuotaExhausted", calls["n"] == 2)

calls2 = {"n": 0}
def _always_404():
    calls2["n"] += 1
    raise _http(404)
try:
    with_retry(_always_404)
except HttpError:
    pass
check("permanent errors are not retried", calls2["n"] == 1)


# ---------------------------------------------------------------- lock
# Two writers on one folder duplicate every document. This is the guard.

from pipeline.lock import FolderLock, AlreadyRunning, _lock_path

_fid = "TESTFOLDER_UNITTEST"
_lp = _lock_path(_fid)
if _lp.exists():
    _lp.unlink()

with FolderLock(_fid, "job-a"):
    check("lock file created", _lp.exists())
check("lock released on exit", not _lp.exists())

with FolderLock(_fid, "job-a"):
    try:
        with FolderLock(_fid, "job-b"):
            check("second instance refused", False)
    except AlreadyRunning:
        check("second instance refused", True)

_lp.write_text(json.dumps({"pid": 999999, "label": "ghost", "started": "then"}))
try:
    with FolderLock(_fid, "job-c"):
        check("stale lock reclaimed, not fatal", True)
except AlreadyRunning:
    check("stale lock reclaimed, not fatal", False)

try:
    with FolderLock(_fid, "job-d"):
        raise RuntimeError("boom")
except RuntimeError:
    pass
check("lock released after an exception", not _lp.exists())

print()
print("FAILURES:", fails if fails else "none")

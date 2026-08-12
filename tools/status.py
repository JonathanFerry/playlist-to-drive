#!/usr/bin/env python3
"""
status.py - one-glance progress across all runs.

    .venv/bin/python status.py          # snapshot
    .venv/bin/python status.py --watch  # refresh every 60s

Checks the supervisor separately from the worker. The log alone will not
tell you the chain is broken: a dead supervisor still leaves its current
child running, so everything looks fine until that child finishes and
nothing follows it.
"""


from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json
import re
import subprocess
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# Never read these. credentials.json and token.json are secrets and have
# no business being opened by a status display.
SKIP = {"credentials.json", "token.json", "config.json",
        "package.json", "package-lock.json"}


def running(pattern: str) -> str | None:
    """Return the pid of a matching process, or None."""
    try:
        out = subprocess.run(["pgrep", "-f", pattern],
                             capture_output=True, text=True).stdout.strip()
        return out.splitlines()[0] if out else None
    except (OSError, IndexError):
        return None


def current_manifest() -> str:
    """Which manifest the running worker is writing to."""
    pid = running("run.py --apply")
    if not pid:
        return ""
    try:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", pid],
                             capture_output=True, text=True).stdout
        m = re.search(r"--manifest\s+(\S+)", cmd)
        return m.group(1) if m else ""
    except (OSError, AttributeError):
        return ""


def log_position(log_path: Path) -> str:
    """Latest [n/total] marker from a run log."""
    if not log_path.exists():
        return ""
    try:
        tail = log_path.read_text(errors="replace")[-40000:]
        hits = re.findall(r"^\[(\d+)/(\d+)\]", tail, re.M)
        if not hits:
            return ""
        cur, total = hits[-1]
        pct = 100 * int(cur) / int(total)
        return f"{cur}/{total} ({pct:.0f}%)"
    except OSError:
        return ""


def summarise(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    counts = Counter(v.get("status", "?") for v in d.get("entries", {}).values())
    return {
        "total": d.get("entry_count", 0),
        "counts": counts,
        "updated": d.get("updated_at", "")[11:19],
    }


def snapshot() -> None:
    print("=" * 74)
    print(f"transcript-pipeline status   {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 74)

    worker = running("run.py --apply")
    supervisor = running("run_queue.py --apply")
    active = current_manifest()

    print("\nPROCESSES")
    print(f"  worker      {'running  ' + (active or '') if worker else 'NOT RUNNING'}")
    print(f"  supervisor  {'running' if supervisor else 'NOT RUNNING'}")
    if worker and not supervisor:
        print("    ^ the current job will finish, but nothing will start after it")
    if not worker and not supervisor:
        print("    ^ everything has stopped")


    manifests = sorted(p for p in BASE.glob("*.json") if p.name not in SKIP)
    if manifests:
        print("\nMANIFESTS")
        grand_ok = grand_seen = 0
        for p in manifests:
            s = summarise(p)
            if not s:
                continue
            c = s["counts"]
            ok = c.get("completed", 0)
            grand_ok += ok
            grand_seen += s["total"]
            mark = " <-- active" if p.name == active else ""
            pct = 100 * ok / s["total"] if s["total"] else 0
            print(f"  {p.stem:14s} {s['total']:5d} seen   {ok:5d} ok ({pct:4.1f}%)"
                  f"   last {s['updated']}{mark}")
            detail = [f"{k}={v}" for k, v in sorted(c.items()) if k != "completed"]
            if detail:
                print(f"                 {'  '.join(detail)}")
        print(f"  {'TOTAL':14s} {grand_seen:5d} seen   {grand_ok:5d} documents")

    # Pick the most recently written log, not a fixed name — the queue is
    # often restarted into a new log file and a stale one reads as current.
    logs = sorted(Path("/tmp").glob("queue*.log"), key=lambda p: p.stat().st_mtime,
                  reverse=True)
    logs += [Path("/tmp/covid19.log")]
    for log in logs:
        pos = log_position(log)
        if pos:
            age = (time.time() - log.stat().st_mtime) / 60
            stale = f"   (log idle {age:.0f} min)" if age > 10 else ""
            print(f"\nCURRENT JOB   {pos}   [{log.name}]{stale}")
            break
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true",
                    help="refresh every 60 seconds")
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()

    if not args.watch:
        snapshot()
        return 0

    try:
        while True:
            print("\033[2J\033[H", end="")   # clear screen
            snapshot()
            print(f"refreshing every {args.interval}s — Ctrl-C to stop")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped watching (runs are unaffected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

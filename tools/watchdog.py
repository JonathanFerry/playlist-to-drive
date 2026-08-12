#!/usr/bin/env python3
"""
watchdog.py - notice a broken run without being asked.

status.py only reports when you run it, which means a failure sits
undetected until you happen to look. This polls and speaks up.

Three conditions, each needing a different response:

  CHAIN BROKEN - worker alive, supervisor dead. The current job finishes
                 and nothing follows it. Easy to miss: the log keeps
                 scrolling and everything looks healthy.
  ALL STOPPED  - neither running. Either finished, or crashed.
  STALLED      - worker alive but no manifest write for a long time.
                 Hung rather than dead, which no process check catches.

    .venv/bin/python watchdog.py                 # 2-minute polling
    .venv/bin/python watchdog.py --interval 300  # every 5 minutes
"""


from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import subprocess
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STALL_MINUTES = 20


def alive(pattern: str) -> bool:
    return bool(subprocess.run(["pgrep", "-f", pattern],
                               capture_output=True, text=True).stdout.strip())


def alert(title: str, message: str, speak: str) -> None:
    """Raise an alert through several channels, none of which can be
    silently lost.

    Audio alone is not enough: an alert fired correctly and went unnoticed
    because the machine's output volume was zero, so both the notification
    sound and the spoken message were silent. A banner can also be
    suppressed by Focus, or simply missed while away from the desk.

    So: a marker file that persists until deleted, the log, a notification,
    and speech. The marker is the one that survives everything.
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {title}: {message}"
    print(f"\n{line}", flush=True)

    try:
        marker = BASE / "ALERT.txt"
        with marker.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass

    try:
        subprocess.run([
            "osascript", "-e",
            f'display notification "{message}" with title "{title}" sound name "Basso"'
        ], capture_output=True, timeout=10)
        subprocess.run(["say", speak], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        # Notification and speech are conveniences; the marker file
        # above is the channel that must not fail.
        pass


def newest_manifest_age() -> float:
    """Minutes since any manifest was last written."""
    skip = {"credentials.json", "token.json"}
    files = [p for p in BASE.glob("*.json") if p.name not in skip]
    if not files:
        return 0.0
    newest = max(p.stat().st_mtime for p in files)
    return (time.time() - newest) / 60


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=120,
                    help="seconds between checks (default 120)")
    ap.add_argument("--stall", type=int, default=STALL_MINUTES,
                    help="minutes without a manifest write before alerting")
    args = ap.parse_args()

    print(f"watchdog started {datetime.now():%H:%M:%S} — checking every "
          f"{args.interval}s, stall threshold {args.stall} min")
    print("Ctrl-C stops watching. The runs are unaffected.\n", flush=True)

    warned = set()
    try:
        while True:
            worker = alive("run.py --apply")
            supervisor = alive("run_queue.py")
            age = newest_manifest_age()
            stamp = datetime.now().strftime("%H:%M:%S")

            if not worker and not supervisor:
                if "done" not in warned:
                    alert("transcript-pipeline",
                          "All processes stopped — finished or crashed",
                          "transcript pipeline has stopped")
                    warned.add("done")
                    print("  check: .venv/bin/python status.py", flush=True)
                time.sleep(args.interval)
                continue

            if worker and not supervisor:
                if "chain" not in warned:
                    alert("transcript-pipeline",
                          "Supervisor died — nothing will run after this job",
                          "supervisor died. the queue is broken")
                    warned.add("chain")
                    print("  re-arm: nohup .venv/bin/python -u run_queue.py "
                          "--apply --wait-for-pid $(pgrep -f 'run.py --apply' "
                          "| head -1) > /tmp/queue_next.log 2>&1 &", flush=True)
            else:
                warned.discard("chain")

            if worker and age > args.stall:
                if "stall" not in warned:
                    alert("transcript-pipeline",
                          f"No manifest write for {age:.0f} minutes — possibly hung",
                          "the run appears to be stalled")
                    warned.add("stall")
            elif age < args.stall / 2:
                warned.discard("stall")

            state = "ok" if (worker and supervisor) else "DEGRADED"
            print(f"[{stamp}] {state}  worker={'y' if worker else 'n'} "
                  f"supervisor={'y' if supervisor else 'n'} "
                  f"last write {age:.0f} min ago", flush=True)

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nwatchdog stopped (runs are unaffected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

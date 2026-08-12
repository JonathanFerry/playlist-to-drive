#!/usr/bin/env python3
"""
dedupe_all.py - run dedupe across every configured job.

Reads jobs.toml, so no folder identifiers live in source. The manifest
name is derived from the job name the same way run_queue.py derives it,
which keeps the two in step.

    python dedupe_all.py            # dry run
    python dedupe_all.py --apply    # trash duplicates
"""


from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import sys
import tomllib
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
JOBS_PATH = BASE / "jobs.toml"


def main() -> int:
    apply = "--apply" in sys.argv

    if not JOBS_PATH.exists():
        print(f"missing {JOBS_PATH}\nCopy jobs.example.toml to jobs.toml and edit it.")
        return 1

    with JOBS_PATH.open("rb") as fh:
        jobs = tomllib.load(fh).get("job", [])
    if not jobs:
        print("no jobs defined in jobs.toml")
        return 1

    py = str(BASE / ".venv" / "bin" / "python")

    for job in jobs:
        manifest = f"{job['name'].lower()}.json"
        cmd = [py, "-u", str(BASE / "dedupe.py"),
               "--manifest", manifest,
               "--folder-id", job["folder_id"],
               "--show", "0"]
        if apply:
            cmd.append("--apply")

        print(f"\n{'=' * 60}\n{job['name']}  ({manifest})\n{'=' * 60}", flush=True)
        result = subprocess.run(cmd, cwd=BASE)
        if result.returncode != 0:
            print(f"  FAILED with exit {result.returncode} — stopping", flush=True)
            return result.returncode

    print("\nall folders processed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

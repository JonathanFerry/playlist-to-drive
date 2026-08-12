"""
queue.py - work through several playlists in series.

Each job writes config.toml, runs the pipeline with its own manifest, and
reports. Jobs are independent: one can be re-run without touching the
others.

A job that stops early stops the QUEUE, not just itself. run.py exits
non-zero when its circuit breaker trips, and that almost always means
something systemic — expired credentials, exhausted quota, no network.
Continuing would burn through the remaining playlists producing the same
failure thousands of times.

    python queue.py            # show the plan and current state
    python queue.py --apply    # run it
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
JOBS_PATH = BASE / "jobs.toml"
PY = str(BASE / ".venv" / "bin" / "python")


def load_jobs() -> list[dict]:
    with JOBS_PATH.open("rb") as fh:
        return tomllib.load(fh).get("job", [])


def write_config(job: dict) -> None:
    """Point config.toml at this job. Behaviour settings are preserved."""
    path = BASE / "config.toml"
    lines = path.read_text().splitlines()
    out = []
    for line in lines:
        if line.startswith("playlist_url"):
            out.append(f'playlist_url = "{job["playlist_url"]}"')
        elif line.startswith("folder_id"):
            out.append(f'folder_id = "{job["folder_id"]}"')
        else:
            out.append(line)
    path.write_text("\n".join(out) + "\n")


def manifest_name(job: dict) -> str:
    return f"{job['name'].lower()}.json"


def manifest_count(job: dict) -> int:
    p = BASE / manifest_name(job)
    if not p.exists():
        return 0
    try:
        return json.loads(p.read_text()).get("entry_count", 0)
    except (OSError, json.JSONDecodeError):
        return 0


def run_job(job: dict, index: int, total: int) -> int:
    name = job["name"]
    started = datetime.now()

    print("\n" + "=" * 70)
    print(f"JOB {index}/{total}: {name}")
    print(f"  folder   : {job['folder_id']}")
    print(f"  manifest : {manifest_name(job)} ({manifest_count(job)} existing)")
    print(f"  started  : {started:%H:%M:%S}")
    print("=" * 70, flush=True)

    write_config(job)

    proc = subprocess.run(
        [PY, "-u", "run.py", "--apply", "--manifest", manifest_name(job)],
        cwd=BASE,
    )

    took = datetime.now() - started
    print(f"\n--- {name} finished in {str(took).split('.')[0]} "
          f"(exit {proc.returncode}, {manifest_count(job)} entries) ---",
          flush=True)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually run")
    ap.add_argument("--wait-for-pid", type=int, default=0,
                    help="wait for a running process to finish first")
    args = ap.parse_args()

    jobs = load_jobs()
    if not jobs:
        print("No jobs in jobs.toml")
        return 1

    print(f"{len(jobs)} jobs queued:\n")
    for i, j in enumerate(jobs, 1):
        print(f"  {i}. {j['name']:12s} manifest={manifest_name(j):18s} "
              f"({manifest_count(j)} entries so far)")

    if not args.apply:
        print("\nDry run. Re-run with --apply to start.")
        return 0

    if args.wait_for_pid:
        print(f"\nWaiting for pid {args.wait_for_pid} to finish...", flush=True)
        while True:
            try:
                import os
                os.kill(args.wait_for_pid, 0)
            except OSError:
                break
            time.sleep(30)
        print("It finished. Starting the queue.\n", flush=True)

    queue_started = datetime.now()
    results = []

    for i, job in enumerate(jobs, 1):
        code = run_job(job, i, len(jobs))
        results.append((job["name"], code, manifest_count(job)))

        if code != 0:
            print("\n" + "!" * 70)
            print(f"QUEUE STOPPED at job {i}/{len(jobs)}: {job['name']} "
                  f"exited {code}.")
            print("  A non-zero exit means the circuit breaker tripped, which")
            print("  is almost always systemic — credentials, quota, network.")
            print("  Continuing would repeat the same failure across every")
            print("  remaining playlist. Fix the cause, then re-run: each")
            print("  manifest resumes where it stopped.")
            print("!" * 70, flush=True)
            break

    print("\n" + "=" * 70)
    print(f"QUEUE SUMMARY  (total {str(datetime.now() - queue_started).split('.')[0]})")
    for name, code, count in results:
        status = "ok" if code == 0 else f"STOPPED (exit {code})"
        print(f"  {name:12s} {count:6d} entries   {status}")
    remaining = [j["name"] for j in jobs[len(results):]]
    if remaining:
        print(f"  not started: {', '.join(remaining)}")
    print("=" * 70)

    return 0 if all(c == 0 for _, c, _ in results) else 2


if __name__ == "__main__":
    sys.exit(main())

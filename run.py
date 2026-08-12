"""
run.py - the pipeline.

Append-and-report. New videos are processed; everything already in the
manifest is skipped; videos removed from the playlist keep their
transcripts and are only reported. Nothing is ever deleted, because a
video pulled from YouTube makes its transcript the only surviving copy.

    python run.py            # dry run
    python run.py --apply    # process and upload
"""

from __future__ import annotations

import argparse
import random
import sys
import time

from pipeline import config as config_mod
from pipeline import drive as drive_mod
from pipeline import fetch
from pipeline import format as fmt
from pipeline.auth import drive_service, docs_service
from pipeline.clean import vtt_to_text
from pipeline.manifest import (
    Manifest, Entry, COMPLETED, NO_CAPTIONS, UNAVAILABLE, FAILED,
)
from pipeline.resilience import CircuitBreaker, QuotaExhausted
from pipeline.lock import FolderLock, AlreadyRunning


CAPTION_ATTEMPTS = 4
CAPTION_BASE_DELAY = 15.0   # YouTube throttles for longer than an API does


def fetch_captions_with_backoff(video_id, cookies_browser, on_throttle=None):
    """Fetch captions, backing off when YouTube throttles.

    Separate from the Google API retry path in resilience.py: yt-dlp
    failures arrive as text on stderr from a subprocess, not as
    HttpError objects, so they bypass that machinery entirely.
    """
    for attempt in range(1, CAPTION_ATTEMPTS + 1):
        try:
            return fetch.captions(video_id, cookies_browser)
        except fetch.RateLimited:
            if on_throttle:
                on_throttle()
            if attempt == CAPTION_ATTEMPTS:
                raise
            delay = CAPTION_BASE_DELAY * (2 ** (attempt - 1))
            delay += random.uniform(0, delay * 0.25)
            print(f"        throttled by YouTube, waiting {delay:.0f}s "
                  f"(attempt {attempt}/{CAPTION_ATTEMPTS - 1})", flush=True)
            time.sleep(delay)


def process_one(video, cfg, drive, docs_api, dry_run, on_throttle=None):
    """Fetch, clean and upload one video. Returns an Entry."""
    base = Entry(video_id=video.video_id, status=FAILED, title=video.title)

    try:
        vtt = fetch_captions_with_backoff(
            video.video_id, cfg.cookies_from_browser, on_throttle)
    except fetch.RateLimited as exc:
        # Counts as a failure so the circuit breaker can see a sustained
        # throttle. Retryable on a later run.
        base.status, base.note = FAILED, f"rate limited: {exc}"
        return base, None
    except fetch.NoCaptions as exc:
        base.status, base.note = NO_CAPTIONS, str(exc)
        return base, None
    except fetch.Unavailable as exc:
        base.status, base.note = UNAVAILABLE, str(exc)
        return base, None

    body, stats = vtt_to_text(vtt)
    if not body:
        base.status, base.note = NO_CAPTIONS, "caption track produced no text"
        return base, None

    title = fmt.normalize_title(video.title)
    text = fmt.build_document(title, video.video_id, body, fmt.SOURCE_CAPTIONS)

    if dry_run:
        base.status, base.source, base.word_count = COMPLETED, "captions", stats["words"]
        base.title = title
        return base, stats

    as_doc = cfg.file_format == "gdoc"
    file_id = drive_mod.upload_document(drive, cfg.folder_id, title, text,
                                        as_doc=as_doc)
    if as_doc:
        # Typography is a Docs concept. A plain-text file has none, which
        # is the point of uploading it that way.
        drive_mod.apply_typography(docs_api, file_id)
    base.status = COMPLETED
    base.title = title
    base.drive_file_id = file_id
    base.source = "captions"
    base.word_count = stats["words"]
    return base, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="process and upload")
    ap.add_argument("--manifest", default="manifest.json")
    ap.add_argument("--limit", type=int, default=0, help="stop after N new videos")
    args = ap.parse_args()

    cfg = config_mod.load()
    dry = not args.apply

    # One writer per destination folder. Two concurrent runs each write a
    # complete transcript for every video, so the folder ends up holding
    # two copies of everything. A dry run writes nothing and needs no lock.
    if dry:
        return _run(args, cfg, dry)

    try:
        with FolderLock(cfg.folder_id, args.manifest):
            return _run(args, cfg, dry)
    except AlreadyRunning as exc:
        print(f"\nREFUSING TO START\n\n{exc}\n")
        return 3


def _run(args, cfg, dry) -> int:
    drive = drive_service()
    docs_api = docs_service()
    manifest = Manifest.load_or_new(cfg.folder_id, args.manifest)

    folder_name = drive.files().get(
        fileId=cfg.folder_id, fields="name").execute()["name"]

    print(f"{'[DRY RUN]' if dry else '[APPLY]'}")
    print(f"  playlist : {cfg.playlist_url}")
    print(f"  folder   : {folder_name}")
    print(f"  format   : {cfg.file_format}")
    print(f"  manifest : {len(manifest.entries)} existing entries\n")

    try:
        videos = fetch.playlist(cfg.playlist_url, cfg.cookies_from_browser)
    except fetch.Unavailable as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"Playlist has {len(videos)} videos.\n")
    manifest.mark_seen([v.video_id for v in videos])
    playlist_ids = {v.video_id for v in videos}

    # Reconcile against Drive. A manifest entry marked complete whose
    # document has been deleted or trashed would otherwise be skipped
    # forever, leaving a permanent gap. One folder listing is far cheaper
    # than an existence check per entry.
    from bootstrap import list_docs
    live_ids = {f["id"] for f in list_docs(drive, cfg.folder_id)}

    missing = [e for e in manifest.entries.values()
               if e.status == COMPLETED and e.drive_file_id
               and e.drive_file_id not in live_ids]
    if missing:
        print(f"{len(missing)} manifest entries point at documents no longer "
              f"in Drive — they will be reprocessed:")
        for e in missing[:10]:
            print(f"  - {e.video_id}  {e.title[:50]}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")
        print()
        for e in missing:
            e.status = FAILED
            e.drive_file_id = ""
            e.note = "document missing from Drive; queued for reprocessing"

    todo = [v for v in videos if not manifest.is_done(v.video_id)]
    if args.limit:
        todo = todo[: args.limit]

    print(f"To process : {len(todo)}")
    print(f"Skipping   : {len(videos) - len([v for v in videos if not manifest.is_done(v.video_id)])} already complete\n")

    processed, skipped_nc, unavailable, failed, renamed = [], [], [], [], []
    base_delay = 60.0 / max(cfg.rate_limit_per_minute, 1)
    breaker = CircuitBreaker()
    started_at = time.time()
    stopped_early = ""

    # Adaptive pacing. Every throttle slows the whole run, and the pace
    # never speeds back up within a run: if YouTube is rate limiting, the
    # configured rate was too fast for current conditions and returning to
    # it would just trip the limit again.
    throttle_state = {"count": 0, "multiplier": 1.0}

    def on_throttle():
        throttle_state["count"] += 1
        throttle_state["multiplier"] = min(throttle_state["multiplier"] * 1.5, 8.0)
        print(f"        pacing: {throttle_state['multiplier']:.1f}x slower "
              f"({throttle_state['count']} throttles this run)", flush=True)

    for i, video in enumerate(todo, 1):
        # Progress with an estimate, so a multi-hour run is legible.
        if i > 1:
            elapsed = time.time() - started_at
            per = elapsed / (i - 1)
            remaining = per * (len(todo) - i + 1)
            eta = f"  ~{remaining / 60:.0f} min left"
        else:
            eta = ""
        print(f"[{i}/{len(todo)}] {video.title[:56]}{eta}")

        try:
            entry, stats = process_one(video, cfg, drive, docs_api, dry, on_throttle)
        except QuotaExhausted as exc:
            stopped_early = str(exc)
            print(f"        STOPPING: {exc}")
            break
        except Exception as exc:
            # Broad on purpose: one unexpected failure must not end a
            # run of several thousand items. The failure is recorded in
            # the manifest and counted by the circuit breaker, so it is
            # visible and bounded rather than silent.
            entry = Entry(video_id=video.video_id, status=FAILED,
                          title=video.title, note=str(exc)[:200])
            stats = None

        if entry.status == COMPLETED:
            note = f"{stats['raw_lines']}->{stats['kept_lines']} lines, {stats['words']} words"
            print(f"        ok   ({note})")
            processed.append(entry)
            breaker.record_success()
        elif entry.status == NO_CAPTIONS:
            print("        skip (no captions)")
            skipped_nc.append(entry)
            breaker.record_success()   # not a failure; the video simply has none
        elif entry.status == UNAVAILABLE:
            print(f"        skip ({entry.note})")
            unavailable.append(entry)
            breaker.record_success()   # permanent, and not our fault
        else:
            print(f"        FAIL ({entry.note})")
            failed.append(entry)
            breaker.record_failure(entry.note)

        manifest.record(entry)
        if not dry:
            manifest.save()          # incremental: an interrupted run resumes

        if breaker.tripped:
            stopped_early = breaker.reason
            print(f"\n        STOPPING: {breaker.reason}")
            break

        time.sleep(base_delay * throttle_state["multiplier"])

    # Title sync for documents already present
    if cfg.sync_renames and not dry:
        for video in videos:
            e = manifest.get(video.video_id)
            if not e or e.status != COMPLETED or not e.drive_file_id:
                continue
            wanted = fmt.normalize_title(video.title)
            if e.title != wanted:
                try:
                    drive_mod.rename(drive, e.drive_file_id, wanted,
                                     as_doc=cfg.file_format == "gdoc")
                    if cfg.file_format == "gdoc":
                        # Rewriting the in-body Title line needs the Docs
                        # API. A plain-text file would have to be
                        # re-uploaded wholesale, which is not worth it for
                        # a title change; the filename already carries it.
                        drive_mod.update_title_line(docs_api, e.drive_file_id,
                                                    e.title, wanted)
                    renamed.append((e.title, wanted))
                    e.title = wanted
                except Exception as exc:
                    print(f"  rename failed for {video.video_id}: {exc}")
        if renamed:
            manifest.save()

    # Videos in the manifest but no longer in the playlist. Reported only —
    # their transcripts may be the last surviving record.
    dropped = [e for vid, e in manifest.entries.items()
               if vid not in playlist_ids and e.status == COMPLETED]

    print("\n" + "=" * 64)
    print(f"Processed   : {len(processed)}")
    print(f"No captions : {len(skipped_nc)}")
    print(f"Unavailable : {len(unavailable)}")
    print(f"Failed      : {len(failed)}")
    if renamed:
        print(f"Renamed     : {len(renamed)}")
    print(f"Kept (no longer in playlist) : {len(dropped)}")

    def show(label, entries, fmt_fn):
        if not entries:
            return
        print(f"\n{label}")
        for e in entries[:25]:
            print(f"  - {fmt_fn(e)}")
        if len(entries) > 25:
            print(f"  ... and {len(entries) - 25} more")

    show("No captions available — not processed, will retry on later runs:",
         skipped_nc, lambda e: f"{e.video_id}  {e.title[:55]}")
    show("Unavailable — private, deleted, or restricted:",
         unavailable, lambda e: f"{e.video_id}  {e.title[:45]} ({e.note})")
    show("Failed — unexpected errors:",
         failed, lambda e: f"{e.video_id}  {e.note[:60]}")
    show("In Drive but no longer in the playlist (kept deliberately):",
         dropped, lambda e: f"{e.video_id}  {e.title[:55]}")

    if renamed:
        print("\nRenamed:")
        for old, new in renamed[:20]:
            print(f"  - {old[:40]} -> {new[:40]}")

    if dry:
        print("\nDry run — nothing uploaded, manifest not written.")
        print("Re-run with --apply to process.")
    else:
        manifest.save()
        print(f"\nManifest saved: {len(manifest.entries)} entries")

    if stopped_early:
        done = len(processed) + len(skipped_nc) + len(unavailable) + len(failed)
        print("\n" + "!" * 64)
        print("RUN STOPPED EARLY")
        print(f"  {stopped_early}")
        print(f"  Reached {done} of {len(todo)}.")
        print("  The manifest is accurate for what completed. Re-run to "
              "resume from where this stopped.")
        print("!" * 64)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())

# playlist-to-drive

Mirrors a YouTube playlist into Google Drive as searchable transcript
documents.

Add a video to the playlist, run the script, and only that video is
processed — everything already captured is skipped. Transcripts are never
deleted: videos removed from the playlist keep their documents, which
matters when a creator later makes a video private or takes it down.

## What it does

- Reads a public or unlisted YouTube playlist
- Pulls existing captions — no audio download, no transcription cost
- Collapses YouTube's rolling-caption duplication, where each cue repeats
  the tail of the previous one and naive extraction triples the text
- Uploads to a Drive folder with a provenance header, as plain text by
  default or as a Google Doc if you prefer browser-readable output
- Applies consistent typography at write time
- Syncs document titles when a video is renamed on YouTube
- Detects documents deleted from Drive and reprocesses them
- Reports what it could not process — no captions, private, or deleted —
  and retries those on later runs

## What it does not do

No speech-to-text. Videos without captions are reported, not transcribed.
That keeps the dependency footprint to Python and yt-dlp, at the cost of
roughly 5% of a typical playlist.

## Setup

Requires Python 3.11+ and [yt-dlp](https://github.com/yt-dlp/yt-dlp).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml   # then edit it
```

`yt-dlp` is invoked as a command, not imported, and needs updating far
more often than anything else here — YouTube changes break it regularly.
Install it separately (`brew install yt-dlp` or `pipx install yt-dlp`) so
it can be upgraded on its own schedule.

### Google credentials

1. Create a project at [console.cloud.google.com](https://console.cloud.google.com)
2. Enable the **Google Drive API** and **Google Docs API**
3. Under **Google Auth Platform → Data Access**, add these scopes:
   `auth/drive`, `auth/documents`, `auth/drive.metadata.readonly`
4. Under **Audience**, publish the app — apps left in Testing mode issue
   refresh tokens that expire after seven days
5. Under **Clients**, create an OAuth client of type **Desktop app** and
   download the JSON as `credentials.json`

The broad `auth/drive` scope is required rather than the narrower
`drive.file`, because the bootstrap has to read documents the app did not
create.

## Usage

```bash
.venv/bin/python tools/check_auth.py      # verify credentials and scopes
.venv/bin/python tools/bootstrap.py       # dry run over existing documents
.venv/bin/python tools/bootstrap.py --apply
.venv/bin/python run.py                   # dry run
.venv/bin/python run.py --apply           # process and upload
```

**Run the bootstrap first if the destination folder already contains
transcripts.** It matches each document to its video ID so existing work
is never reprocessed. Without it, every document looks missing and the
whole playlist is fetched again.

Every command is a dry run by default. Nothing is written until `--apply`.

### Maintenance

```bash
.venv/bin/python run_queue.py --apply               # several playlists in series
.venv/bin/python tools/status.py                    # progress across all runs
.venv/bin/python tools/watchdog.py                  # alert when a run breaks
.venv/bin/python tools/audit.py -o report.md --full # what was captured, and why not
.venv/bin/python tools/dedupe.py --manifest X --folder-id Y
.venv/bin/python tools/normalize_typography.py      # audit formatting
.venv/bin/python tools/export.py --manifest X.json --out ./export
.venv/bin/python tests/test_pipeline.py             # tests
```

New uploads are formatted at write time, so this is only needed for
documents created before that existed, or to repair manual edits.


## Layout

```
run.py                  process one playlist
run_queue.py            process several in series

pipeline/               imported, never run directly
  auth  config  format  clean  fetch
  drive  manifest  resilience  lock

tools/                  run occasionally, on their own
  export                pull transcripts out of Drive as plain text
  bootstrap             build a manifest from documents already in Drive
  check_auth            verify credentials and scopes
  audit                 what was captured, and why the rest was not
  status                progress across all runs
  watchdog              alert when a run breaks
  dedupe / dedupe_all   remove duplicate documents
  normalize_typography  audit and repair formatting

tests/
```

## Files kept out of git

`credentials.json`, `token.json`, `config.toml`, and the manifest are all
gitignored — two secrets, your playlist and folder IDs, and machine-local
state holding Drive file IDs.

## Notes

**Multiple playlists.** Use a separate manifest per destination folder via
`--manifest`. The code refuses to run when the manifest's folder and the
config's folder disagree, rather than guessing.

**Typography.** Setting font and size alone is not enough. Converting
plain text to a Google Doc applies the default NORMAL_TEXT style, which
carries 1.15 line spacing and space after each paragraph, and those
survive a font change. All four properties are set together.

**The trailing paragraph.** Every Google Doc ends with an empty paragraph.
Left at the editor defaults it is invisible when rendered, but Ctrl/Cmd+A
selects it, and the toolbar then shows blank font and size fields because
the selection spans mixed values. Styling ranges include it.

## Licence

MIT

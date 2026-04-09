# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A CLI tool that concatenates audio files from `Music for mixtape/` into a single MP3 mix with crossfades, generates Mixcloud-compatible tracklists, and optionally uploads to Mixcloud via their API.

## Prerequisites

- Python 3.10+, `ffmpeg` and `ffprobe` on PATH
- Dependencies: `requests`, `PyYAML`

## Commands

```bash
# Install (editable)
pip install -e .

# Build a mix (requires audio files in "Music for mixtape/")
python -m mixtape build --input "Music for mixtape" --out "output/mixtape.mp3"

# Dry run (prints the ffmpeg command without executing)
python -m mixtape build --dry-run

# With DJ-style random transitions
python -m mixtape build --fx dj-random --fx-prob 0.35 --fx-seed 123

# OAuth + upload to Mixcloud
mixtape auth --client-id ID --client-secret SECRET
mixtape upload --name "Title" --mp3 output/mixtape.mp3 --tracklist output/tracklist.json
```

No test suite exists yet. No linter is configured.

## Architecture

All source is under `src/mixtape/` (setuptools src-layout). Four modules:

- **`__main__.py`** — CLI entry point using argparse. Three subcommands: `build`, `auth`, `upload`. Dispatches to `audio.build_mix`, `mixcloud.auth_cmd`, `mixcloud.upload_cmd`.
- **`tracklist.py`** — Core data model. `Track` dataclass holds path/artist/title/duration/start_time. `discover_tracks()` scans the input dir for audio files (mp3/wav/flac/m4a/aac/ogg), optionally reorders via a YAML manifest or `--first-track`. `compute_start_times()` calculates timestamps accounting for crossfade overlap. Writes both `.txt` (human-readable timestamps) and `.json` (structured) tracklists.
- **`audio.py`** — Builds the ffmpeg filter_complex command: normalizes all inputs to 48kHz stereo fltp, chains `acrossfade` filters between consecutive tracks, applies `loudnorm` at the end. In `dj-random` mode, randomly varies crossfade curves and inserts subtle audio FX (highpass/lowpass/echo/phaser/tremolo). Outputs 320kbps MP3.
- **`mixcloud.py`** — OAuth flow (local HTTP server on localhost to receive callback code) and upload via Mixcloud REST API. Token stored in `.mixcloud_token.json`.

## Key Design Details

- Track ordering defaults to case-insensitive filename sort. A YAML manifest (`--manifest`) can override ordering and artist/title metadata.
- The `--parse-style` flag controls filename-to-metadata parsing: `artist-dash-title` splits on ` - `, `filename-only` uses the stem as-is.
- All ffmpeg interaction is via subprocess (`_run` / `_run_capture`); no Python audio libraries.
- Audio files in `Music for mixtape/` are gitignored (large binaries); only `.gitkeep` is tracked.
- OAuth token file `.mixcloud_token.json` is gitignored.

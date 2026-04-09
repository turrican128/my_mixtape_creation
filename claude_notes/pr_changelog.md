# PR & Commit Changelog

## Commit History (as of 2026-04-09)

### c3e4968 — Initial commit: mixtape builder CLI
- **Date:** 2026-04-09
- CLI entry point (`__main__.py`) with `build`, `auth`, `upload` subcommands
- Track discovery and ordering (`tracklist.py`) with YAML manifest support
- ffmpeg filter_complex builder (`audio.py`) with crossfade and loudnorm
- Mixcloud OAuth + upload (`mixcloud.py`)
- Audio files in `Music for mixtape/` gitignored

### b51d18a — feat: add web UI (track visualisation, drag-and-drop, transitions, live duration)
- **Date:** 2026-04-09
- Added `web.py` module with Flask-based web UI
- Track visualisation, drag-and-drop reordering, transition config, live duration display

### da30a1c — feat: add web UI with track visualisation, drag-and-drop reordering, transitions and build button
- **Date:** 2026-04-09
- Refined web UI: added build button triggering mix creation from the browser

## Uncommitted Changes (as of 2026-04-09)
- `audio.py` — expanded/refactored (+27/-12 lines)
- `tracklist.py` — minor removals (-3 lines)
- `web.py` — edits (+10/-10 lines)

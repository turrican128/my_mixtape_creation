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

### 338a783 — feat: add DJ Dynamic mode, fix tracklist duplicate, and add project notes (PR #2)
- **Date:** 2026-04-09
- Added `dj-dynamic` FX mode with more aggressive effects and 70% FX probability
- Removed duplicate unreachable code block in `tracklist.py`
- Replaced "None (Hard Cut)" with "Standard Crossfade" and added "DJ Dynamic" in web UI
- Added `claude_notes/` with tech stack, changelog, and project plans
- Added `CLAUDE.md` with project guidance
- Added `.claude/settings.local.json`

### 17a24bb — feat: add DJ Smooth mode and remove plain crossfade option (PR #3)
- **Date:** 2026-04-09
- Added `dj-smooth` FX mode with warm, gentle effects (ambient echo, soft phaser, subtle warmth EQ, gentle tremolo, high-end rolloff)
- Uses smooth-only crossfade curves (tri, qsin, hsin, esin) at 50% FX probability
- Removed "None (Hard Cut)" and "Standard Crossfade" from UI — all modes now have FX
- Updated HTML template dropdown to match API

### b6559fd — feat: per-track transition modes, settings page, and default crossfade mode (PR #4)
- **Date:** 2026-04-09
- Per-track transition mode selection (Default / DJ Smooth / DJ Random / DJ Dynamic) via dropdown between each track
- Moved global settings (crossfade duration, filename parsing) to dedicated `/settings` page with auto-save
- Added "Default" mode for plain fade-in/fade-out crossfade with no FX
- Removed settings sidebar from main page, added Settings button in header
- Updated CLI `--fx` choices to support all four modes
- Backend: `_build_filter_complex` accepts per-transition modes list

### fefc699 — fix: remove FX filters that corrupted entire track audio (PR #5)
- **Date:** 2026-04-10
- **Bug:** FX filters (lowpass, highpass, echo, phaser, tremolo, compressor) were applied to entire track audio, not just the transition overlap
- Removed all pre-crossfade FX filter application from `_build_filter_complex`
- Removed `_maybe_fx()` and `should_fx()` functions
- Transition modes now differentiate solely via `acrossfade` curve selection

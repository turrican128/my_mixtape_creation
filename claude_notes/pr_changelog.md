# PR & Commit Changelog

## Commit History (as of 2026-04-10)

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

### 6f42ac8 — feat: add Mixcloud upload from web UI (PR #6)
- **Date:** 2026-04-10
- Mixcloud OAuth flow integrated into the Flask web UI (callback handled at `/api/mixcloud/callback` on the same server)
- New Settings page section for Mixcloud Client ID / Client Secret with "Connect to Mixcloud" button and live connection status
- Credentials and access token persisted to `.mixtape_config.json` (gitignored) so they survive server restarts
- New `/api/mixcloud/auth`, `/api/mixcloud/callback`, `/api/mixcloud/status`, `/api/mixcloud/upload`, `/api/mixcloud/upload/status/<job_id>` endpoints
- Upload runs in background thread (same pattern as build jobs) with progress polling
- Tracklist sections auto-populated from `output/tracklist.json` when uploading
- After a successful build, an "Upload to Mixcloud" trigger appears on the main page (only if connected)

### b2b5a97 — fix: redesign upload UI as a modal popup (PR #7)
- **Date:** 2026-04-10
- Replaced the inline upload form with a polished modal dialog
- Main page now shows a single "☁ Upload to Mixcloud" button after build; clicking it opens the modal
- Modal contains Mixtape Name, Description, Tags fields + Cancel/Upload footer and inline status line
- Added modal overlay, blur backdrop, entry animation, and close handlers (X button, Cancel button, overlay click)

### f03580a — feat: add play + delete buttons to track rows (PR #8)
- **Date:** 2026-04-10
- **Play button (left of each row):** aesthetic circular button that streams the track via a new `/api/audio/<filename>` Flask endpoint using a shared HTML5 `<audio>` element; toggles ▶ / ⏸ with blue glow when playing; resumes in place when paused and re-clicked
- **Delete button (right of each row):** removes a track from the playlist only (files on disk are untouched); triggers total-duration recalculation; releases the audio element if the deleted track was loaded
- Backend `/api/tracks/reorder` and `/api/build` now honor the exact client track list — removed tracks are excluded from the built mixtape; `build_mix()` gained an `include_files` parameter
- SortableJS drag-and-drop still works — the new buttons are filtered out so clicking them doesn't start a drag
- **Bug fixes included in the same PR:**
  - `togglePlay` no longer restarts from the beginning after pause — resumes in place
  - `reorderTracks` re-renders after the server response so position numbers and start times stay in sync
  - Added a monotonic `reorderSeq` counter so stale `/api/tracks/reorder` responses can't clobber newer local deletes (fixes a race when clicking delete rapidly)
  - `deleteTrack` fully releases `audioPlayer.src` via `removeAttribute + load()`, dropping any in-flight fetch
  - `/api/audio` has an audio-extension whitelist and also rejects double-extension filenames (e.g. `secret.py.mp3`)

## Session 2026-06-30 — cover polish + upload modal fixes

### 004364e — Lower cover title: bottom-anchor text instead of centering mid-image
- **Date:** 2026-06-30
- **Problem:** all three cover presets (neon / chrome / outrun) vertically *centered* the title around 62–66% of the cover height, so it overlapped the artwork's focal point (e.g. the figure/car in the base image)
- Switched all presets to **bottom-anchor** the wrapped text block to a 7% bottom margin (`cover.py`), dropping the title into the dark scrim at the bottom where it reads cleanly without covering the base image
- Adapts automatically to line count — 1, 2, or 3 wrapped rows all share the same bottom margin and grow upward; verified live on the real base image for short titles and the 3-line worst case (no cut-off)
- Color, font size, glow, and shrink-to-fit logic unchanged — only the vertical anchor moved

### 96b6f9a — Fix upload modal closing mid-edit; add AI Regenerate button
- **Date:** 2026-06-30
- **Bug — modal disappeared while editing fields:** the backdrop click-to-close handler fired on *any* click landing on the overlay, including when you drag-select text inside a field and release the mouse past the field edge (the resulting click targets the backdrop). Now closes only when the press **started** on the backdrop too (mousedown + click both on the overlay), so text selection never closes the modal
- **New "✨ Regenerate" button** (modal footer, left side): re-runs the AI suggestion and **overwrites** the name/description/tags with a fresh take
- `autofillFromAI(force)` gained a `force` flag — auto-fill-on-open still only fills blank fields (never clobbers typed text), while Regenerate replaces existing content; the button shows only when AI is enabled, disables itself while generating, and refreshes the cover preview afterward
- Touched `app.js`, `style.css` (footer spacer), and `index.html` (button markup — needs a server restart to appear)

### 0933c12 — Block double upload + notify on completion from the main page
- **Date:** 2026-06-30
- **Request 1 — prevent a second concurrent upload:** the modal Upload button already disabled itself during an upload, but the header "☁ Upload to Mixcloud" button stayed active, so closing and reopening the modal could start a second upload. Added an `uploadInProgress` guard that disables both; the header button shows "⏳ Uploading…" as a main-page progress signal, restored on error (retry) or locked to "✓ Uploaded" on success
- **Request 2 — notify even when the modal is closed:** completion previously only updated the modal's status line (invisible once closed). Added a **toast notification** system (bottom-right, over the main page) — sticky green success toast with a "View on Mixcloud" link, red toast on error. The status poll already keeps running after the modal closes, so it now surfaces the result on the page
- **Best-effort desktop notification:** requests `Notification` permission once when an upload starts; fires a system notification on completion/error if granted, so the user is pinged even after tabbing away. The in-page toast always fires regardless of permission/support
- Touched `app.js` (state + `showToast`/`desktopNotify` helpers + wiring), `style.css` (toast styles), `index.html` (toast container — needs a server restart to appear)

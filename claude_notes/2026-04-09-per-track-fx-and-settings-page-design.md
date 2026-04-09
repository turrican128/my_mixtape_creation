# Per-Track Transition Mode & Settings Page

## Context

The mixtape web UI currently has a single global transition mode dropdown (DJ Smooth / DJ Random / DJ Dynamic) and all settings (crossfade duration, FX probability, FX seed, filename parsing) on the main page alongside the track list. This makes the main page cluttered and doesn't allow per-track control over transitions.

**Goal:** Give users per-track transition control and move global settings to a dedicated Settings page, keeping the main page focused on track editing and building.

## Design

### Main Page (Track Editor)

**Header bar:**
- Left: app icon + "Mixtape Creator" title
- Right: total duration, track count, **Settings button** (gear icon + "Settings" text)

**Track list:**
- Each track row keeps: drag handle, artist/title, duration
- **New:** Each track row gets a small dropdown to select the transition mode (DJ Smooth / DJ Random / DJ Dynamic) for the crossfade between that track and the next one
- The last track has no transition dropdown (no next track to transition into)
- Default transition mode for all tracks: DJ Smooth

**Build section:**
- "Build Mixtape" button and build status remain on the main page
- Build sends per-track transition modes to the backend

**Removed from main page:**
- Crossfade duration slider (moved to Settings)
- FX probability slider (removed — modes have built-in probabilities)
- FX seed input (removed — modes have built-in behavior)
- Filename parsing dropdown (moved to Settings)
- Transition mode global dropdown (replaced by per-track dropdowns)

### Settings Page (`/settings`)

**Header bar:**
- Left: gear icon + "Settings" title
- Right: "Back" button (navigates to main page)

**Settings:**
1. **Transition Duration** — range slider (0–15s, step 0.5, default 6.0s). Global crossfade overlap duration applied to all transitions. Label: "Transition Duration". Helper text: "Duration of crossfade overlap between tracks."
2. **Filename Parsing** — dropdown with two options: "Artist – Title (split on ' - ')" and "Filename only". Controls how filenames are parsed into artist/title metadata.

**Persistence:** Settings stored in-memory on the Flask server via a `/api/settings` endpoint (GET/PUT). Main page reads settings on load. Same in-memory pattern as build jobs — fine for single-user desktop tool.

### Backend Changes

**New API endpoints:**
- `GET /api/settings` — returns current settings (`crossfade_s`, `parse_style`)
- `PUT /api/settings` — updates settings

**Modified endpoints:**
- `POST /api/build` — accepts `transitions` array (one mode per track-pair) instead of a single global `fx_mode`. Each entry maps to the transition between track N and track N+1.
- `GET /api/tracks` — reads `crossfade_s` and `parse_style` from stored settings instead of query params
- `POST /api/tracks/reorder` — reads `crossfade_s` from stored settings instead of request body

**Modified build logic in `audio.py`:**
- `_build_filter_complex` accepts a list of per-transition modes instead of a single `fx_mode`
- Each crossfade step picks its FX based on the corresponding transition mode
- `build_mix` passes per-transition modes through

### New Files
- `src/mixtape/templates/settings.html` — Settings page template

### Modified Files
- `src/mixtape/web.py` — new `/settings` route, `/api/settings` endpoints, update existing API endpoints
- `src/mixtape/templates/index.html` — remove settings panel, add Settings button to header, add per-track transition dropdown
- `src/mixtape/static/app.js` — per-track transition state, fetch settings from API, remove settings panel logic
- `src/mixtape/static/style.css` — styles for transition dropdown in track rows, remove settings panel styles
- `src/mixtape/audio.py` — `_build_filter_complex` and `build_mix` accept per-transition modes

## Verification

1. Start the web UI: `python -m mixtape serve --input "Music for mixtape" --port 5050`
2. Open http://127.0.0.1:5050 — main page should show tracks with per-track transition dropdowns, no settings panel
3. Click "Settings" in header — should navigate to `/settings` page with crossfade duration and filename parsing
4. Change settings, go back — main page should reflect updated settings
5. Set different transition modes on different tracks, click Build — verify the build uses per-track modes
6. CLI `python -m mixtape build` should still work (backwards compatible)

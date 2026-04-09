# Tech Stack

## Language & Runtime
- **Python 3.10+** — all source under `src/mixtape/` (setuptools src-layout)

## Core Dependencies
- **ffmpeg / ffprobe** — all audio processing via subprocess (no Python audio libs)
- **requests** — HTTP client for Mixcloud API upload
- **PyYAML** — manifest parsing for track ordering/metadata
- **Flask** — web UI server (`web.py` module)

## Build & Packaging
- **setuptools** with src-layout (`pip install -e .`)
- No test suite or linter configured yet

## External Services
- **Mixcloud REST API** — OAuth flow + upload (token stored in `.mixcloud_token.json`)

## Audio Pipeline
- Inputs: mp3, wav, flac, m4a, aac, ogg
- Output: 320kbps MP3, normalized to 48kHz stereo fltp
- Effects: acrossfade between tracks, loudnorm
- Three FX modes (all crossfade-based, no plain/hard-cut option):
  - **dj-smooth** — warm, gentle effects (ambient echo, soft phaser, warmth EQ, gentle tremolo, high-end rolloff); smooth curves only; 50% FX probability
  - **dj-random** — varied crossfade curves with occasional subtle FX (highpass/lowpass/echo/phaser/tremolo); configurable FX probability (default 35%)
  - **dj-dynamic** — aggressive effects (strong bass cut, muffled, dub echo, intense phaser, fast tremolo, compressor); 70% FX probability

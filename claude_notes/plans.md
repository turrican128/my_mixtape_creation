# Project Plans & Roadmap

## Current State
- CLI tool is functional for building mixes with crossfades and uploading to Mixcloud
- Web UI (Flask) added for visual track management and triggering builds from the browser
- No tests, no linter, no CI/CD

## Known Gaps
- **Testing:** No test suite — unit tests for tracklist logic and integration tests for ffmpeg command generation would be high-value
- **Linting/Formatting:** No linter or formatter configured (e.g., ruff, black, mypy)
- **CI/CD:** No pipeline for automated checks
- **Error handling:** ffmpeg failures rely on subprocess exit codes; no structured error reporting to web UI
- **Web UI polish:** Build job status is in-memory only (lost on restart)

## Potential Future Work
- Add progress reporting for long builds (ffmpeg can be slow on large mixes)
- Support for more output formats beyond MP3
- Playlist/mix presets (save and recall track orders + transition settings)
- Waveform preview in the web UI
- Batch upload support for multiple mixes
- Docker container for portable deployment with ffmpeg included

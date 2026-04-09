# Mixcloud Mixtape Builder

This project builds one “glued” mix from the audio files in `Music for mixtape/` and exports:

- `output/mixtape.mp3` (default: 320kbps)
- `output/tracklist.txt` (Mixcloud-friendly timestamps)
- `output/tracklist.json` (structured)

## Prerequisites

- Python 3.10+
- `ffmpeg` and `ffprobe` available on PATH

## Install

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -U pip
pip install -e .
```

## Build a mix

```powershell
python -m mixtape build --input "Music for mixtape" --out "output/mixtape.mp3"
```

### Optional: DJ-style randomized transitions

This randomly varies crossfade curves and occasionally applies subtle transition FX.

```powershell
python -m mixtape build --input "Music for mixtape" --out "output/mixtape.mp3" --fx dj-random --fx-prob 0.35 --fx-seed 123
```

## Optional: upload to Mixcloud

You need a Mixcloud app (client id/secret) and to complete OAuth once:

```powershell
mixtape auth --client-id YOUR_ID --client-secret YOUR_SECRET
mixtape upload --name "My Mix Title" --mp3 "output/mixtape.mp3" --tracklist "output/tracklist.json"
```


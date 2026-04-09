from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class TrackParseStyle(str, Enum):
    artist_dash_title = "artist-dash-title"
    filename_only = "filename-only"


@dataclass(frozen=True)
class Track:
    path: Path
    artist: str
    title: str
    duration_s: float | None = None
    start_time_s: float | None = None

    @property
    def display(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} – {self.title}"
        stem = self.path.stem
        return stem


def _timestamp(seconds: float) -> str:
    s = int(round(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


_ARTIST_TITLE_RE = re.compile(r"^\s*(?P<artist>.+?)\s*-\s*(?P<title>.+?)\s*$")


def parse_artist_title_from_filename(p: Path) -> tuple[str, str]:
    # Common cleanup for files coming from trackers/archives.
    # Example: "Jeroen_Tel_-_Eliminator" -> "Jeroen Tel - Eliminator"
    normalized = p.stem.replace("_-_", " - ").replace("_", " ")
    m = _ARTIST_TITLE_RE.match(normalized)
    if not m:
        return ("", p.stem)
    return (m.group("artist").strip(), m.group("title").strip())


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("mixtape manifest must be a YAML mapping at top-level")
    return data


def discover_tracks(
    input_dir: Path,
    manifest_path: Path | None,
    parse_style: TrackParseStyle,
    first_track: str | None = None,
) -> list[Track]:
    if not input_dir.exists():
        raise FileNotFoundError(f"Input folder not found: {input_dir}")

    manifest: dict[str, Any] = {}
    if manifest_path is not None:
        manifest = load_manifest(manifest_path)

    exts = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}
    files = [p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in exts]

    # Default stable ordering: filename
    files.sort(key=lambda p: p.name.lower())

    if first_track:
        by_name_ci = {p.name.lower(): p for p in files}
        key = first_track.lower()
        if key not in by_name_ci:
            raise FileNotFoundError(f"--first-track not found in input folder: {first_track}")
        first_p = by_name_ci[key]
        files = [first_p] + [p for p in files if p != first_p]

    if "order" in manifest:
        order = manifest["order"]
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            raise ValueError("manifest 'order' must be a list of filenames")
        by_name = {p.name: p for p in files}
        missing = [name for name in order if name not in by_name]
        if missing:
            raise FileNotFoundError(f"manifest order references missing files: {missing}")
        files = [by_name[name] for name in order]

    overrides: dict[str, Any] = {}
    if "tracks" in manifest:
        if not isinstance(manifest["tracks"], dict):
            raise ValueError("manifest 'tracks' must be a mapping keyed by filename")
        overrides = manifest["tracks"]

    tracks: list[Track] = []
    for p in files:
        artist, title = ("", p.stem)
        if parse_style == TrackParseStyle.artist_dash_title:
            artist, title = parse_artist_title_from_filename(p)
        elif parse_style == TrackParseStyle.filename_only:
            artist, title = ("", p.stem)

        ov = overrides.get(p.name, {})
        if ov:
            if not isinstance(ov, dict):
                raise ValueError(f"manifest tracks['{p.name}'] must be a mapping")
            artist = str(ov.get("artist", artist) or "")
            title = str(ov.get("title", title) or "")

        tracks.append(Track(path=p, artist=artist, title=title))

    if not tracks:
        raise FileNotFoundError(f"No audio files found in {input_dir}")
    return tracks


def compute_start_times(tracks: list[Track], crossfade_s: float) -> list[Track]:
    if crossfade_s < 0:
        raise ValueError("crossfade must be >= 0")
    out: list[Track] = []
    t = 0.0
    for i, tr in enumerate(tracks):
        if tr.duration_s is None:
            raise ValueError("duration_s must be populated before computing start times")
        out.append(Track(**{**tr.__dict__, "start_time_s": max(0.0, t)}))
        if i < len(tracks) - 1:
            t = t + tr.duration_s - crossfade_s
            if t < 0:
                t = 0.0
    return out


def write_tracklist_txt(tracks: list[Track], path: Path) -> None:
    lines = []
    for tr in tracks:
        if tr.start_time_s is None:
            continue
        lines.append(f"{_timestamp(tr.start_time_s)} {tr.display}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_tracklist_json(tracks: list[Track], path: Path) -> None:
    payload = []
    for tr in tracks:
        payload.append(
            {
                "file": tr.path.name,
                "artist": tr.artist,
                "title": tr.title,
                "display": tr.display,
                "duration_s": tr.duration_s,
                "start_time_s": tr.start_time_s,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


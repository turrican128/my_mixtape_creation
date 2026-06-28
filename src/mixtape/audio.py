from __future__ import annotations

import json
import random
import shlex
import subprocess
from pathlib import Path

from .tracklist import (
    Track,
    TrackParseStyle,
    compute_start_times,
    discover_tracks,
    write_tracklist_json,
    write_tracklist_txt,
)


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + " ".join(shlex.quote(c) for c in cmd)
            + "\n\nSTDOUT:\n"
            + (p.stdout or "")
            + "\n\nSTDERR:\n"
            + (p.stderr or "")
        )


def _run_capture(cmd: list[str]) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + " ".join(shlex.quote(c) for c in cmd)
            + "\n\nSTDOUT:\n"
            + (p.stdout or "")
            + "\n\nSTDERR:\n"
            + (p.stderr or "")
        )
    return p.stdout


def probe_duration_seconds(path: Path) -> float:
    out = _run_capture(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    data = json.loads(out)
    dur = float(data["format"]["duration"])
    return dur


# Codecs that are mathematically lossless — their bitrate reflects the source
# fidelity, not a quality ceiling.
_LOSSLESS_CODECS = frozenset(
    {"flac", "alac", "wavpack", "tta", "ape", "pcm_s16le", "pcm_s24le", "pcm_s32le"}
)


def probe_media_info(path: Path) -> dict[str, object]:
    """Return duration plus quality metadata in a single ffprobe call.

    Keys: duration_s (float|None), bit_rate_bps (int|None),
    sample_rate_hz (int|None), codec (str|None), lossless (bool).
    Falls back gracefully when individual fields are missing.
    """
    out = _run_capture(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "format=duration,bit_rate:stream=codec_name,sample_rate,bit_rate",
            "-of",
            "json",
            str(path),
        ]
    )
    data = json.loads(out)
    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []
    stream = streams[0] if streams else {}

    def _to_int(v: object) -> int | None:
        try:
            return int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    def _to_float(v: object) -> float | None:
        try:
            return float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    # Prefer the container/format bitrate (overall), fall back to the stream's.
    bit_rate = _to_int(fmt.get("bit_rate")) or _to_int(stream.get("bit_rate"))
    codec = stream.get("codec_name") or None
    return {
        "duration_s": _to_float(fmt.get("duration")),
        "bit_rate_bps": bit_rate,
        "sample_rate_hz": _to_int(stream.get("sample_rate")),
        "codec": codec,
        "lossless": bool(codec and codec.lower() in _LOSSLESS_CODECS),
    }


_CURVES = [
    "tri",
    "qsin",
    "hsin",
    "esin",
    "exp",
    "log",
    "ipar",
    "iqsin",
    "ihsin",
]


def _build_filter_complex(
    n: int,
    crossfade_s: float,
    fx_mode: str,
    fx_prob: float,
    fx_seed: int | None,
    transition_modes: list[str] | None = None,
) -> str:
    # Each input: [i:a]aformat -> [ai]
    # Then chain acrossfade: [a0][a1]acrossfade=d=... -> [x1], etc.
    # FX are expressed solely via crossfade curve selection — no filters on tracks.
    rng = random.Random(fx_seed)
    parts: list[str] = []
    for i in range(n):
        parts.append(f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a{i}]")

    if n == 1:
        parts.append("[a0]loudnorm=I=-14:LRA=11:TP=-1.5:print_format=summary[m]")
        return ";".join(parts)

    _SMOOTH_CURVES = ["tri", "qsin", "hsin", "esin"]

    def pick_curve(mode: str) -> str:
        if mode == "dj-smooth":
            return rng.choice(_SMOOTH_CURVES)
        return rng.choice(_CURVES) if mode in ("dj-random", "dj-dynamic") else "tri"

    def _get_mode(transition_idx: int) -> str:
        if transition_modes and transition_idx < len(transition_modes):
            return transition_modes[transition_idx]
        return fx_mode

    # First transition — curve-only, no FX filters on tracks
    mode_0 = _get_mode(0)
    parts.append(f"[a0][a1]acrossfade=d={crossfade_s}:c1={pick_curve(mode_0)}:c2={pick_curve(mode_0)}[x1]")

    for i in range(2, n):
        mode_i = _get_mode(i - 1)
        parts.append(f"[x{i-1}][a{i}]acrossfade=d={crossfade_s}:c1={pick_curve(mode_i)}:c2={pick_curve(mode_i)}[x{i}]")
    parts.append(f"[x{n-1}]loudnorm=I=-14:LRA=11:TP=-1.5:print_format=summary[m]")
    return ";".join(parts)


def build_mix(
    input_dir: Path,
    out_mp3: Path,
    crossfade_s: float,
    fx_mode: str,
    fx_prob: float,
    fx_seed: int | None,
    manifest_path: Path | None,
    parse_style: TrackParseStyle,
    tracklist_txt_path: Path,
    tracklist_json_path: Path,
    first_track: str | None,
    dry_run: bool,
    transition_modes: list[str] | None = None,
    include_files: list[str] | None = None,
) -> int:
    tracks = discover_tracks(
        input_dir=input_dir,
        manifest_path=manifest_path,
        parse_style=parse_style,
        first_track=first_track,
    )

    # Optional filter: restrict to the given track ids (case-insensitive) and
    # order them according to include_files. Ids are folder-relative paths
    # (e.g. "CD1/song.flac"); we also accept bare filenames for backward
    # compatibility (CLI / top-level tracks).
    if include_files is not None:
        by_rel = {tr.rel.lower(): tr for tr in tracks}
        by_base = {tr.path.name.lower(): tr for tr in tracks}
        selected: list[Track] = []
        for f in include_files:
            key = f.lower()
            tr = by_rel.get(key) or by_base.get(key)
            if tr is not None:
                selected.append(tr)
        tracks = selected

    # Probe durations
    probed: list[Track] = []
    for tr in tracks:
        d = probe_duration_seconds(tr.path)
        probed.append(Track(**{**tr.__dict__, "duration_s": d}))

    with_starts = compute_start_times(probed, crossfade_s=crossfade_s)
    write_tracklist_txt(with_starts, tracklist_txt_path)
    write_tracklist_json(with_starts, tracklist_json_path)

    out_mp3.parent.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = ["ffmpeg", "-y"]
    for tr in with_starts:
        cmd += ["-i", str(tr.path)]

    filter_complex = _build_filter_complex(
        n=len(with_starts),
        crossfade_s=crossfade_s,
        fx_mode=fx_mode,
        fx_prob=fx_prob,
        fx_seed=fx_seed,
        transition_modes=transition_modes,
    )
    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[m]",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "320k",
        "-ar",
        "48000",
        str(out_mp3),
    ]

    if dry_run:
        print(" ".join(shlex.quote(c) for c in cmd))
        return 0

    _run(cmd)
    print(f"Built: {out_mp3}")
    print(f"Tracklist: {tracklist_txt_path}")
    return 0


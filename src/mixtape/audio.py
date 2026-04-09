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


def _maybe_fx(rng: random.Random, mode: str = "subtle") -> str | None:
    """
    Return a short, safe filter chain to apply on one side of a crossfade.
    'subtle' is for dj-random, 'smooth' is for dj-smooth, 'dynamic' is for dj-dynamic.
    """
    if mode == "dynamic":
        choices = [
            "highpass=f=400",            # More aggressive bass cut
            "lowpass=f=1000",           # Muffled/Under-water effect
            "aecho=0.7:0.4:60:0.2",     # Stronger dub echo
            "aphaser=type=t:decay=0.5:speed=1.2", # More intense phaser
            "tremolo=f=10:d=0.5",       # Faster, deeper tremolo
            "acompressor=threshold=-20dB:ratio=4", # Squashed sound
        ]
    elif mode == "smooth":
        choices = [
            "lowpass=f=8000",                       # Gentle high-end rolloff
            "aecho=0.5:0.3:80:0.08",               # Warm, ambient tail
            "aphaser=type=t:decay=0.2:speed=0.3",   # Soft, slow phaser sweep
            "tremolo=f=3:d=0.15",                   # Gentle pulsing
            "equalizer=f=250:t=q:w=1.5:g=2",       # Subtle warmth boost
        ]
    else:
        choices = [
            "highpass=f=120",
            "lowpass=f=5500",
            "aecho=0.6:0.3:50:0.12",
            "aphaser=type=t:decay=0.35:speed=0.6",
            "tremolo=f=6.5:d=0.25",
        ]
    return rng.choice(choices)


def _build_filter_complex(
    n: int,
    crossfade_s: float,
    fx_mode: str,
    fx_prob: float,
    fx_seed: int | None,
) -> str:
    # Each input: [i:a]aformat -> [ai]
    # Then chain acrossfade: [a0][a1]acrossfade=d=... -> [x1], etc.
    rng = random.Random(fx_seed)
    parts: list[str] = []
    for i in range(n):
        parts.append(f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a{i}]")

    if n == 1:
        parts.append("[a0]loudnorm=I=-14:LRA=11:TP=-1.5:print_format=summary[m]")
        return ";".join(parts)

    _SMOOTH_CURVES = ["tri", "qsin", "hsin", "esin"]

    def pick_curve() -> str:
        if fx_mode == "dj-smooth":
            return rng.choice(_SMOOTH_CURVES)
        return rng.choice(_CURVES) if fx_mode in ("dj-random", "dj-dynamic") else "tri"

    def should_fx() -> bool:
        if fx_mode == "dj-dynamic":
            return rng.random() < 0.7
        if fx_mode == "dj-smooth":
            return rng.random() < 0.5
        return fx_mode == "dj-random" and (rng.random() < fx_prob)

    # Build transitions, optionally inserting small FX filters on either side.
    left = "a0"
    right = "a1"
    if should_fx():
        fx_type = {"dj-dynamic": "dynamic", "dj-smooth": "smooth"}.get(fx_mode, "subtle")
        fx = _maybe_fx(rng, mode=fx_type)
        if fx:
            parts.append(f"[a0]{fx}[a0fx]")
            left = "a0fx"
    if should_fx():
        fx_type = {"dj-dynamic": "dynamic", "dj-smooth": "smooth"}.get(fx_mode, "subtle")
        fx = _maybe_fx(rng, mode=fx_type)
        if fx:
            parts.append(f"[a1]{fx}[a1fx]")
            right = "a1fx"
    parts.append(f"[{left}][{right}]acrossfade=d={crossfade_s}:c1={pick_curve()}:c2={pick_curve()}[x1]")

    for i in range(2, n):
        right = f"a{i}"
        if should_fx():
            fx_type = {"dj-dynamic": "dynamic", "dj-smooth": "smooth"}.get(fx_mode, "subtle")
            fx = _maybe_fx(rng, mode=fx_type)
            if fx:
                parts.append(f"[a{i}]{fx}[a{i}fx]")
                right = f"a{i}fx"
        parts.append(f"[x{i-1}][{right}]acrossfade=d={crossfade_s}:c1={pick_curve()}:c2={pick_curve()}[x{i}]")
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
) -> int:
    tracks = discover_tracks(
        input_dir=input_dir,
        manifest_path=manifest_path,
        parse_style=parse_style,
        first_track=first_track,
    )

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


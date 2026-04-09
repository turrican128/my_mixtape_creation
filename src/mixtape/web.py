from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from .tracklist import (
    Track,
    TrackParseStyle,
    compute_start_times,
    discover_tracks,
)
from .audio import build_mix, probe_duration_seconds

# ---------------------------------------------------------------------------
# Global build-job registry (in-process; fine for single-user desktop tool)
# ---------------------------------------------------------------------------
_build_jobs: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS."""
    s = int(round(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _track_to_dict(tr: Track) -> dict[str, Any]:
    return {
        "file": tr.path.name,
        "artist": tr.artist,
        "title": tr.title,
        "display": tr.display,
        "duration_s": tr.duration_s,
        "duration_display": _timestamp(tr.duration_s) if tr.duration_s else None,
        "start_time_s": tr.start_time_s,
        "start_time_display": _timestamp(tr.start_time_s) if tr.start_time_s is not None else None,
    }


def _compute_total_duration(tracks: list[Track], crossfade_s: float) -> float:
    """Return total mixtape length in seconds accounting for crossfade overlaps."""
    if not tracks:
        return 0.0
    total = sum(tr.duration_s for tr in tracks if tr.duration_s is not None)
    # Each crossfade between consecutive tracks removes crossfade_s from total
    n_overlaps = len(tracks) - 1
    for i in range(n_overlaps):
        dur = tracks[i].duration_s if tracks[i].duration_s else 0.0
        effective = min(crossfade_s, dur)
        total -= effective
    return total


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(input_dir: Path | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )

    # Default input dir – can be overridden via CLI --input
    app.config["INPUT_DIR"] = input_dir or Path("Music for mixtape")

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        return render_template("index.html")

    # ------------------------------------------------------------------
    # API: Tracks
    # ------------------------------------------------------------------

    @app.route("/api/tracks", methods=["GET"])
    def api_tracks():
        input_dir: Path = app.config["INPUT_DIR"]
        parse_style = TrackParseStyle(request.args.get("parse_style", "artist-dash-title"))

        try:
            tracks = discover_tracks(
                input_dir=input_dir,
                manifest_path=None,
                parse_style=parse_style,
            )
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

        # Probe durations
        probe_errors: list[str] = []
        probed: list[Track] = []
        for tr in tracks:
            try:
                d = probe_duration_seconds(tr.path)
                probed.append(Track(**{**tr.__dict__, "duration_s": d}))
            except FileNotFoundError:
                probe_errors.append("ffprobe not found — install ffmpeg and ensure it is on PATH")
                probed.append(tr)
            except Exception as exc:
                probe_errors.append(f"Could not probe {tr.path.name}: {exc}")
                probed.append(tr)

        # Only compute start times if all durations are available
        crossfade_s = float(request.args.get("crossfade", 6.0))
        if all(tr.duration_s is not None for tr in probed):
            with_starts = compute_start_times(probed, crossfade_s=crossfade_s)
            total = _compute_total_duration(with_starts, crossfade_s)
        else:
            with_starts = probed
            total = 0.0

        resp = jsonify({
            "tracks": [_track_to_dict(t) for t in with_starts],
            "total_duration_s": total,
            "total_duration_display": _timestamp(total) if total > 0 else "--:--",
            "crossfade_s": crossfade_s,
        })
        if probe_errors:
            resp.headers["X-Warning"] = "; ".join(probe_errors[:3])
        return resp

    # ------------------------------------------------------------------
    # API: Reorder tracks
    # ------------------------------------------------------------------

    @app.route("/api/tracks/reorder", methods=["POST"])
    def api_tracks_reorder():
        data = request.get_json(force=True)
        ordered_files: list[str] = data.get("order", [])
        crossfade_s = float(data.get("crossfade", 6.0))
        parse_style = TrackParseStyle(data.get("parse_style", "artist-dash-title"))
        input_dir: Path = app.config["INPUT_DIR"]

        try:
            tracks = discover_tracks(
                input_dir=input_dir,
                manifest_path=None,
                parse_style=parse_style,
            )
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

        # Build lookup by filename (case-insensitive)
        by_name = {tr.path.name.lower(): tr for tr in tracks}

        reordered: list[Track] = []
        for fname in ordered_files:
            key = fname.lower()
            if key in by_name:
                reordered.append(by_name[key])

        # Add any tracks not in the order list (preserving original sort)
        included = {fname.lower() for fname in ordered_files}
        for tr in tracks:
            if tr.path.name.lower() not in included:
                reordered.append(tr)

        # Probe durations
        probed: list[Track] = []
        for tr in reordered:
            try:
                d = probe_duration_seconds(tr.path)
                probed.append(Track(**{**tr.__dict__, "duration_s": d}))
            except Exception:
                probed.append(tr)

        # Only compute start times if all durations are available
        if all(tr.duration_s is not None for tr in probed):
            with_starts = compute_start_times(probed, crossfade_s=crossfade_s)
            total = _compute_total_duration(with_starts, crossfade_s)
        else:
            with_starts = probed
            total = 0.0

        return jsonify({
            "tracks": [_track_to_dict(t) for t in with_starts],
            "total_duration_s": total,
            "total_duration_display": _timestamp(total) if total > 0 else "--:--",
        })

    # ------------------------------------------------------------------
    # API: Transitions
    # ------------------------------------------------------------------

    @app.route("/api/transitions", methods=["GET"])
    def api_transitions():
        return jsonify({
            "modes": [
                {
                    "id": "none",
                    "name": "None (Hard Cut)",
                    "description": "No crossfade — tracks cut directly into each other.",
                    "has_crossfade": False,
                    "has_fx": False,
                },
                {
                    "id": "crossfade",
                    "name": "Standard Crossfade",
                    "description": "Smooth overlap between consecutive tracks.",
                    "has_crossfade": True,
                    "has_fx": False,
                },
                {
                    "id": "dj-random",
                    "name": "DJ Random",
                    "description": "Varied crossfade curves with occasional subtle FX.",
                    "has_crossfade": True,
                    "has_fx": True,
                },
            ]
        })

    # ------------------------------------------------------------------
    # API: Build
    # ------------------------------------------------------------------

    @app.route("/api/build", methods=["POST"])
    def api_build():
        data = request.get_json(force=True)
        order: list[str] = data.get("order", [])
        crossfade_s = float(data.get("crossfade", 6.0))
        fx_mode = data.get("fx_mode", "none")
        fx_prob = float(data.get("fx_prob", 0.35))
        fx_seed = data.get("fx_seed")
        parse_style = TrackParseStyle(data.get("parse_style", "artist-dash-title"))
        input_dir: Path = app.config["INPUT_DIR"]

        job_id = str(uuid.uuid4())[:8]
        _build_jobs[job_id] = {
            "status": "pending",
            "progress": "",
            "output_path": None,
            "error": None,
        }

        def _run_build():
            try:
                _build_jobs[job_id]["status"] = "building"
                _build_jobs[job_id]["progress"] = "Probing tracks..."

                # Discover and reorder
                tracks = discover_tracks(
                    input_dir=input_dir,
                    manifest_path=None,
                    parse_style=parse_style,
                )
                by_name = {tr.path.name.lower(): tr for tr in tracks}
                ordered: list[Track] = []
                for fname in order:
                    key = fname.lower()
                    if key in by_name:
                        ordered.append(by_name[key])
                included = {fname.lower() for fname in order}
                for tr in tracks:
                    if tr.path.name.lower() not in included:
                        ordered.append(tr)

                # Map fx_mode for the backend
                # "none" and "crossfade" both use fx_mode="none" in audio.py
                # "crossfade" just means standard crossfade (the default)
                actual_fx_mode = "none" if fx_mode in ("none", "crossfade") else fx_mode

                out_mp3 = Path("output") / "mixtape.mp3"
                tracklist_txt = Path("output") / "tracklist.txt"
                tracklist_json = Path("output") / "tracklist.json"

                _build_jobs[job_id]["progress"] = "Running ffmpeg..."

                rc = build_mix(
                    input_dir=input_dir,
                    out_mp3=out_mp3,
                    crossfade_s=crossfade_s,
                    fx_mode=actual_fx_mode,
                    fx_prob=fx_prob,
                    fx_seed=int(fx_seed) if fx_seed is not None else None,
                    manifest_path=None,
                    parse_style=parse_style,
                    tracklist_txt_path=tracklist_txt,
                    tracklist_json_path=tracklist_json,
                    first_track=order[0] if order else None,
                    dry_run=False,
                )

                if rc == 0:
                    _build_jobs[job_id]["status"] = "done"
                    _build_jobs[job_id]["output_path"] = str(out_mp3)
                    _build_jobs[job_id]["progress"] = "Done!"
                else:
                    _build_jobs[job_id]["status"] = "error"
                    _build_jobs[job_id]["error"] = f"Build exited with code {rc}"

            except Exception as exc:
                _build_jobs[job_id]["status"] = "error"
                _build_jobs[job_id]["error"] = str(exc)

        thread = threading.Thread(target=_run_build, daemon=True)
        thread.start()

        return jsonify({"job_id": job_id})

    @app.route("/api/build/status/<job_id>", methods=["GET"])
    def api_build_status(job_id: str):
        job = _build_jobs.get(job_id)
        if not job:
            return jsonify({"error": "Unknown job"}), 404
        return jsonify(job)

    return app


# ---------------------------------------------------------------------------
# CLI entry-point for `mixtape serve`
# ---------------------------------------------------------------------------

def _ensure_ffmpeg_on_path() -> None:
    """Try to locate ffmpeg/ffprobe if not already on PATH (Windows WinGet install)."""
    import shutil
    if shutil.which("ffprobe"):
        return  # Already available

    # Common WinGet install location
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if not local_appdata:
        return
    winget_base = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
    if not winget_base.exists():
        return
    for pkg_dir in winget_base.iterdir():
        if "ffmpeg" in pkg_dir.name.lower() or "gyan.ffmpeg" in pkg_dir.name.lower():
            for bin_dir in pkg_dir.rglob("bin"):
                if (bin_dir / "ffprobe.exe").exists():
                    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
                    return


def serve_cmd(input_dir: Path, host: str, port: int) -> int:
    """Start the Mixtape web UI server."""
    _ensure_ffmpeg_on_path()
    app = create_app(input_dir=input_dir)
    print(f"🎵 Mixtape Web UI")
    print(f"   Input folder: {input_dir}")
    print(f"   Listening on: http://{host}:{port}")
    print()
    app.run(host=host, port=port, debug=False)
    return 0
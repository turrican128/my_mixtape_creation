from __future__ import annotations

import json
import os
import secrets
import threading
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import requests as http_requests
from flask import Flask, jsonify, redirect, render_template, request

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
# Upload-job registry (same pattern as build jobs)
# ---------------------------------------------------------------------------
_upload_jobs: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Config file for persistent settings (credentials, etc.)
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path(".mixtape_config.json")

def _load_config() -> dict[str, Any]:
    if _CONFIG_PATH.exists():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}

def _save_config(data: dict[str, Any]) -> None:
    _CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

# ---------------------------------------------------------------------------
# Global settings (in-process; fine for single-user desktop tool)
# ---------------------------------------------------------------------------
_config = _load_config()
_settings: dict[str, Any] = {
    "crossfade_s": 6.0,
    "parse_style": "artist-dash-title",
    "mixcloud_client_id": _config.get("mixcloud_client_id", ""),
    "mixcloud_client_secret": _config.get("mixcloud_client_secret", ""),
    "mixcloud_access_token": _config.get("mixcloud_access_token", ""),
}

# Transient OAuth state (not persisted)
_oauth_state: str = ""

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
        parse_style = TrackParseStyle(_settings["parse_style"])

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
        crossfade_s = _settings["crossfade_s"]
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
        crossfade_s = _settings["crossfade_s"]
        parse_style = TrackParseStyle(_settings["parse_style"])
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
                    "id": "default",
                    "name": "Default",
                    "description": "Simple fade-out / fade-in crossfade with no effects.",
                    "has_crossfade": True,
                    "has_fx": False,
                },
                {
                    "id": "dj-smooth",
                    "name": "DJ Smooth",
                    "description": "Warm, gentle FX transitions — soft phaser, ambient echo, subtle warmth.",
                    "has_crossfade": True,
                    "has_fx": True,
                },
                {
                    "id": "dj-random",
                    "name": "DJ Random",
                    "description": "Varied crossfade curves with occasional subtle FX.",
                    "has_crossfade": True,
                    "has_fx": True,
                },
                {
                    "id": "dj-dynamic",
                    "name": "DJ Dynamic",
                    "description": "More aggressive, intentional DJ effects and varied curves.",
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
        crossfade_s = _settings["crossfade_s"]
        transition_modes: list[str] = data.get("transitions", [])
        fx_mode = data.get("fx_mode", "default")
        fx_prob = float(data.get("fx_prob", 0.35))
        fx_seed = data.get("fx_seed")
        parse_style = TrackParseStyle(_settings["parse_style"])
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

                out_mp3 = Path("output") / "mixtape.mp3"
                tracklist_txt = Path("output") / "tracklist.txt"
                tracklist_json = Path("output") / "tracklist.json"

                _build_jobs[job_id]["progress"] = "Running ffmpeg..."

                rc = build_mix(
                    input_dir=input_dir,
                    out_mp3=out_mp3,
                    crossfade_s=crossfade_s,
                    fx_mode=fx_mode,
                    fx_prob=fx_prob,
                    fx_seed=int(fx_seed) if fx_seed is not None else None,
                    manifest_path=None,
                    parse_style=parse_style,
                    tracklist_txt_path=tracklist_txt,
                    tracklist_json_path=tracklist_json,
                    first_track=order[0] if order else None,
                    dry_run=False,
                    transition_modes=transition_modes if transition_modes else None,
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

    # ------------------------------------------------------------------
    # Settings page
    # ------------------------------------------------------------------

    @app.route("/settings")
    def settings_page():
        return render_template("settings.html")

    # ------------------------------------------------------------------
    # API: Settings
    # ------------------------------------------------------------------

    @app.route("/api/settings", methods=["GET"])
    def api_settings_get():
        return jsonify(_settings)

    @app.route("/api/settings", methods=["PUT"])
    def api_settings_put():
        data = request.get_json(force=True)
        if "crossfade_s" in data:
            val = float(data["crossfade_s"])
            if val < 0.5:
                return jsonify({"error": "crossfade_s must be >= 0.5"}), 400
            _settings["crossfade_s"] = val
        if "parse_style" in data:
            try:
                TrackParseStyle(data["parse_style"])
            except ValueError:
                return jsonify({"error": "Invalid parse_style"}), 400
            _settings["parse_style"] = data["parse_style"]
        # Mixcloud credentials
        if "mixcloud_client_id" in data:
            _settings["mixcloud_client_id"] = str(data["mixcloud_client_id"]).strip()
        if "mixcloud_client_secret" in data:
            _settings["mixcloud_client_secret"] = str(data["mixcloud_client_secret"]).strip()
        # Persist Mixcloud fields to config file
        _save_config({
            "mixcloud_client_id": _settings["mixcloud_client_id"],
            "mixcloud_client_secret": _settings["mixcloud_client_secret"],
            "mixcloud_access_token": _settings["mixcloud_access_token"],
        })
        return jsonify(_settings)

    # ------------------------------------------------------------------
    # API: Mixcloud OAuth
    # ------------------------------------------------------------------

    @app.route("/api/mixcloud/auth", methods=["GET"])
    def api_mixcloud_auth():
        global _oauth_state
        client_id = _settings.get("mixcloud_client_id", "")
        if not client_id:
            return jsonify({"error": "Set your Mixcloud Client ID in Settings first"}), 400
        _oauth_state = secrets.token_urlsafe(16)
        port = request.host.split(":")[-1] if ":" in request.host else "5050"
        redirect_uri = f"http://localhost:{port}/api/mixcloud/callback"
        auth_url = "https://www.mixcloud.com/oauth/authorize?" + urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": _oauth_state,
        })
        return jsonify({"auth_url": auth_url})

    @app.route("/api/mixcloud/callback", methods=["GET"])
    def api_mixcloud_callback():
        global _oauth_state
        code = request.args.get("code", "")
        state = request.args.get("state", "")
        if not code or state != _oauth_state:
            return "Invalid or expired OAuth state. Please try connecting again from Settings.", 400
        _oauth_state = ""
        client_id = _settings.get("mixcloud_client_id", "")
        client_secret = _settings.get("mixcloud_client_secret", "")
        port = request.host.split(":")[-1] if ":" in request.host else "5050"
        redirect_uri = f"http://localhost:{port}/api/mixcloud/callback"
        try:
            r = http_requests.post(
                "https://www.mixcloud.com/oauth/access_token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
                timeout=60,
            )
            r.raise_for_status()
            token_data = r.json()
            access_token = token_data.get("access_token", "")
            if not access_token:
                return f"Mixcloud did not return an access token. Response: {token_data}", 400
        except Exception as exc:
            return f"Failed to exchange code for token: {exc}", 500
        _settings["mixcloud_access_token"] = access_token
        _save_config({
            "mixcloud_client_id": _settings["mixcloud_client_id"],
            "mixcloud_client_secret": _settings["mixcloud_client_secret"],
            "mixcloud_access_token": access_token,
        })
        return (
            "<html><body style='font-family:sans-serif;text-align:center;padding:60px;background:#0d1117;color:#e6edf3'>"
            "<h2 style='color:#3fb950'>Connected to Mixcloud!</h2>"
            "<p>You can close this tab and return to the app.</p>"
            "</body></html>"
        )

    @app.route("/api/mixcloud/status", methods=["GET"])
    def api_mixcloud_status():
        return jsonify({
            "connected": bool(_settings.get("mixcloud_access_token")),
            "has_credentials": bool(_settings.get("mixcloud_client_id") and _settings.get("mixcloud_client_secret")),
        })

    # ------------------------------------------------------------------
    # API: Mixcloud Upload
    # ------------------------------------------------------------------

    @app.route("/api/mixcloud/upload", methods=["POST"])
    def api_mixcloud_upload():
        access_token = _settings.get("mixcloud_access_token", "")
        if not access_token:
            return jsonify({"error": "Not connected to Mixcloud. Go to Settings to connect."}), 400
        data = request.get_json(force=True)
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "Mixtape name is required"}), 400
        description = data.get("description", "")
        tags: list[str] = data.get("tags", [])

        mp3_path = Path("output") / "mixtape.mp3"
        tracklist_json_path = Path("output") / "tracklist.json"
        if not mp3_path.exists():
            return jsonify({"error": "No built mixtape found. Build one first."}), 400

        job_id = str(uuid.uuid4())[:8]
        _upload_jobs[job_id] = {
            "status": "uploading",
            "progress": "Starting upload...",
            "error": None,
            "mixcloud_url": None,
        }

        def _run_upload():
            try:
                _upload_jobs[job_id]["progress"] = "Preparing upload data..."
                url = f"https://api.mixcloud.com/upload/?access_token={urllib.parse.quote(access_token)}"
                form_data: dict[str, str] = {
                    "name": name,
                    "description": description,
                    "percentage_music": "100",
                }
                for i, tag in enumerate(tags[:20]):
                    if tag.strip():
                        form_data[f"tags-{i}-tag"] = tag.strip()
                # Add track sections from tracklist
                if tracklist_json_path.exists():
                    tracks = json.loads(tracklist_json_path.read_text(encoding="utf-8"))
                    if isinstance(tracks, list):
                        for i, tr in enumerate(tracks[:500]):
                            artist = str(tr.get("artist", "") or "")
                            song = str(tr.get("title", "") or tr.get("song", "") or "")
                            start_time = tr.get("start_time_s", tr.get("start_time", 0))
                            try:
                                start_i = int(float(start_time))
                            except Exception:
                                start_i = 0
                            if artist:
                                form_data[f"sections-{i}-artist"] = artist
                            if song:
                                form_data[f"sections-{i}-song"] = song
                            form_data[f"sections-{i}-start_time"] = str(start_i)

                _upload_jobs[job_id]["progress"] = "Uploading to Mixcloud..."
                with mp3_path.open("rb") as f:
                    files = {"mp3": (mp3_path.name, f, "audio/mpeg")}
                    r = http_requests.post(url, data=form_data, files=files, timeout=60 * 30)
                r.raise_for_status()
                resp = r.json()
                result_url = resp.get("result", {}).get("key", "")
                if result_url:
                    mixcloud_url = f"https://www.mixcloud.com{result_url}"
                else:
                    mixcloud_url = ""
                _upload_jobs[job_id]["status"] = "done"
                _upload_jobs[job_id]["progress"] = "Upload complete!"
                _upload_jobs[job_id]["mixcloud_url"] = mixcloud_url
            except Exception as exc:
                _upload_jobs[job_id]["status"] = "error"
                _upload_jobs[job_id]["error"] = str(exc)

        thread = threading.Thread(target=_run_upload, daemon=True)
        thread.start()
        return jsonify({"job_id": job_id})

    @app.route("/api/mixcloud/upload/status/<job_id>", methods=["GET"])
    def api_mixcloud_upload_status(job_id: str):
        job = _upload_jobs.get(job_id)
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
    print(f"Mixtape Web UI")
    print(f"   Input folder: {input_dir}")
    print(f"   Listening on: http://{host}:{port}")
    print()
    app.run(host=host, port=port, debug=False)
    return 0
from __future__ import annotations

import json
import logging
import os
import random
import re
import secrets
import threading
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import requests as http_requests
from flask import Flask, jsonify, redirect, render_template, request, send_file, send_from_directory

from .tracklist import (
    Track,
    TrackParseStyle,
    compute_start_times,
    discover_tracks,
)
from .audio import build_mix, probe_media_info
from . import cover as cover_mod

# ---------------------------------------------------------------------------
# Global build-job registry (in-process; fine for single-user desktop tool)
# ---------------------------------------------------------------------------
_build_jobs: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Upload-job registry (same pattern as build jobs)
# ---------------------------------------------------------------------------
_upload_jobs: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Cover art
# ---------------------------------------------------------------------------
#: User drops their base image here (see cover/README.md). Relative to the
#: process working directory.
_COVER_DIR = Path("cover")

#: Mapping from the UI-level text size string to the multiplier cover.py
#: applies to the preset's starting font size. Medium (1.0) preserves the
#: original "auto-fit as large as possible" behavior.
_TEXT_SIZE_SCALES = {"small": 0.65, "medium": 1.0, "large": 1.15}


def _resolve_text_scale(raw: str | None) -> float:
    """Map a UI text-size string to a float scale; unknown -> 1.0."""
    key = (raw or "medium").strip().lower()
    return _TEXT_SIZE_SCALES.get(key, 1.0)

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
# Playlist session persistence (which tracks, their order, their transitions)
# ---------------------------------------------------------------------------
#: Stores the user's working playlist so it survives a browser reload:
#:   {"order": [filename, ...],        # included tracks, in order
#:    "removed": [filename, ...],      # files kept out on purpose
#:    "transitions": {filename: mode}} # per-track transition choice
#: Gitignored like the other dot-files.
_SESSION_PATH = Path(".mixtape_session.json")

#: Modes the first-time randomizer picks from (DJ modes only — every
#: fresh transition gets an effect).
_DJ_TRANSITION_MODES = ("dj-smooth", "dj-random", "dj-dynamic")


def _load_session() -> dict[str, Any]:
    """Read the saved playlist session, tolerating a missing/corrupt file."""
    empty = {"order": [], "removed": [], "transitions": {}}
    if not _SESSION_PATH.exists():
        return empty
    try:
        data = json.loads(_SESSION_PATH.read_text(encoding="utf-8"))
    except Exception:
        return empty
    return {
        "order": list(data.get("order", []) or []),
        "removed": list(data.get("removed", []) or []),
        "transitions": dict(data.get("transitions", {}) or {}),
    }


def _save_session(order: list[str], removed: list[str],
                  transitions: dict[str, str]) -> None:
    _SESSION_PATH.write_text(
        json.dumps(
            {"order": order, "removed": removed, "transitions": transitions},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def _merge_session(discovered: list[Track]) -> tuple[list[Track], list[str], dict[str, str]]:
    """Merge the saved playlist session with what's currently in the folder.

    - Restores the saved order; drops files no longer on disk.
    - Keeps removed files out of the playlist.
    - Appends brand-new files (never seen before) to the end.
    - Assigns a random DJ transition to any track seen for the first time,
      then persists it so reopening never re-randomizes.

    Returns (ordered_included_tracks, removed_filenames, transitions).
    """
    by_name = {t.rel: t for t in discovered}

    session = _load_session()
    order = [f for f in session["order"] if f in by_name]
    removed = [f for f in session["removed"] if f in by_name]
    known = set(order) | set(removed)
    new = [f for f in by_name if f not in known]  # discover order (sorted)
    order = order + new

    transitions = {f: m for f, m in session["transitions"].items() if f in by_name}

    # First-time randomization: any included track without a saved
    # transition gets a random DJ mode (then frozen via the save below).
    changed = (
        order != session["order"]
        or removed != session["removed"]
        or len(transitions) != len(session["transitions"])
    )
    for f in order:
        if f not in transitions:
            transitions[f] = random.choice(_DJ_TRANSITION_MODES)
            changed = True

    if changed:
        _save_session(order, removed, transitions)

    ordered = [by_name[f] for f in order]
    return ordered, removed, transitions

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
    "anthropic_api_key": _config.get("anthropic_api_key", ""),
}


def _persist_config() -> None:
    """Write the current persistent settings back to the config file."""
    _save_config({
        "mixcloud_client_id": _settings["mixcloud_client_id"],
        "mixcloud_client_secret": _settings["mixcloud_client_secret"],
        "mixcloud_access_token": _settings["mixcloud_access_token"],
        "anthropic_api_key": _settings["anthropic_api_key"],
    })


# Sent to the client in place of a stored secret, so the real value
# never leaves the server. If the client PUTs this placeholder back
# unchanged, we keep the existing value.
_SECRET_MASK = "********"
_SECRET_FIELDS = ("mixcloud_client_secret", "anthropic_api_key")


def _public_settings() -> dict[str, Any]:
    """Return a copy of _settings safe to send to the client, with
    stored secrets replaced by a mask."""
    out = dict(_settings)
    for field in _SECRET_FIELDS:
        if out.get(field):
            out[field] = _SECRET_MASK
    # Never expose the access token at all.
    out.pop("mixcloud_access_token", None)
    return out


def _resolve_secret(incoming: str, field: str) -> str:
    """If the client sends back the placeholder, keep the existing
    value; otherwise accept the new value (including clearing to '')."""
    if incoming == _SECRET_MASK:
        return _settings.get(field, "")
    return incoming.strip()

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
        "file": tr.rel or tr.path.name,
        "artist": tr.artist,
        "title": tr.title,
        "display": tr.display,
        "duration_s": tr.duration_s,
        "duration_display": _timestamp(tr.duration_s) if tr.duration_s else None,
        "start_time_s": tr.start_time_s,
        "start_time_display": _timestamp(tr.start_time_s) if tr.start_time_s is not None else None,
        "bit_rate_bps": tr.bit_rate_bps,
        "bit_rate_kbps": round(tr.bit_rate_bps / 1000) if tr.bit_rate_bps else None,
        "sample_rate_hz": tr.sample_rate_hz,
        "codec": tr.codec,
        "lossless": tr.lossless,
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
            discovered = discover_tracks(
                input_dir=input_dir,
                manifest_path=None,
                parse_style=parse_style,
            )
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

        # Merge with the saved session: restore order/removed/transitions,
        # append new files, and randomize first-seen transitions.
        tracks, removed, transitions = _merge_session(discovered)

        # Probe durations
        probe_errors: list[str] = []
        probed: list[Track] = []
        for tr in tracks:
            try:
                info = probe_media_info(tr.path)
                probed.append(Track(**{
                    **tr.__dict__,
                    "duration_s": info["duration_s"],
                    "bit_rate_bps": info["bit_rate_bps"],
                    "sample_rate_hz": info["sample_rate_hz"],
                    "codec": info["codec"],
                    "lossless": info["lossless"],
                }))
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
            "removed": removed,
            "transitions": transitions,
        })
        if probe_errors:
            # Header values may not contain newlines (ffprobe stderr often does).
            warning = "; ".join(probe_errors[:3])
            resp.headers["X-Warning"] = " ".join(warning.split())
        return resp

    # ------------------------------------------------------------------
    # API: Stream audio file (for in-browser playback)
    # ------------------------------------------------------------------

    _AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}

    @app.route("/api/audio/<path:filename>", methods=["GET"])
    def api_audio(filename: str):
        # Only serve files with recognized audio extensions. The file type
        # is gated by the suffix; path traversal is handled by
        # send_from_directory (it confines access to the base directory).
        # Note: we deliberately do NOT reject dots inside the stem —
        # legitimate names carry them (e.g. "19. Artist - Title.flac",
        # "Mr. Roboto").
        p = Path(filename)
        if p.suffix.lower() not in _AUDIO_EXTS:
            return jsonify({"error": "Not an audio file"}), 400
        input_dir: Path = app.config["INPUT_DIR"]
        return send_from_directory(input_dir.resolve(), filename, conditional=True)

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

        # Build lookup by relative path (case-insensitive)
        by_name = {tr.rel.lower(): tr for tr in tracks}

        reordered: list[Track] = []
        for fname in ordered_files:
            key = fname.lower()
            if key in by_name:
                reordered.append(by_name[key])
        # Note: tracks not in the order list are intentionally excluded
        # so the client can remove tracks from the playlist without
        # deleting them from the source folder.

        # Probe durations
        probed: list[Track] = []
        for tr in reordered:
            try:
                info = probe_media_info(tr.path)
                probed.append(Track(**{
                    **tr.__dict__,
                    "duration_s": info["duration_s"],
                    "bit_rate_bps": info["bit_rate_bps"],
                    "sample_rate_hz": info["sample_rate_hz"],
                    "codec": info["codec"],
                    "lossless": info["lossless"],
                }))
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
    # API: Playlist session (persist order / removed / transitions)
    # ------------------------------------------------------------------

    def _folder_names() -> set[str]:
        """Track identities (relative paths) in the input folder (empty if missing)."""
        try:
            discovered = discover_tracks(
                input_dir=app.config["INPUT_DIR"],
                manifest_path=None,
                parse_style=TrackParseStyle(_settings["parse_style"]),
            )
        except FileNotFoundError:
            return set()
        return {t.rel for t in discovered}

    @app.route("/api/session", methods=["PUT"])
    def api_session_put():
        """Persist the working playlist. Called (debounced) by the UI after
        every remove / reorder / transition change so it survives a reload.
        Only filenames still present in the folder are stored."""
        data = request.get_json(force=True)
        folder = _folder_names()

        order = [f for f in data.get("order", []) if f in folder]
        order_set = set(order)
        # A file can't be both in the playlist and removed — order wins.
        removed = [f for f in data.get("removed", []) if f in folder and f not in order_set]
        transitions_in = data.get("transitions", {}) or {}
        transitions = {f: str(m) for f, m in transitions_in.items() if f in folder}

        _save_session(order, removed, transitions)
        return jsonify({"ok": True})

    @app.route("/api/session/reset", methods=["POST"])
    def api_session_reset():
        """Restore all songs: clear removals so the next /api/tracks call
        brings every folder file back. Restored tracks (which lost their
        transition when removed) get a fresh random pick on reload."""
        session = _load_session()
        _save_session(session["order"], [], session["transitions"])
        return jsonify({"ok": True})

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
                by_name = {tr.rel.lower(): tr for tr in tracks}
                ordered: list[Track] = []
                for fname in order:
                    key = fname.lower()
                    if key in by_name:
                        ordered.append(by_name[key])
                # Tracks not in `order` are intentionally excluded
                # (user removed them from the playlist).

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
                    include_files=order if order else None,
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
        return jsonify(_public_settings())

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
            _settings["mixcloud_client_secret"] = _resolve_secret(
                str(data["mixcloud_client_secret"]), "mixcloud_client_secret"
            )
        # Anthropic API key (used for AI auto-fill of upload metadata)
        if "anthropic_api_key" in data:
            _settings["anthropic_api_key"] = _resolve_secret(
                str(data["anthropic_api_key"]), "anthropic_api_key"
            )
        _persist_config()
        return jsonify(_public_settings())

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
        _persist_config()
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
            "ai_enabled": bool(_settings.get("anthropic_api_key")),
            # Whether a built mixtape is already on disk and ready to upload.
            # Lets the UI restore the Upload button after a page reload.
            "has_build": (Path("output") / "mixtape.mp3").exists(),
        })

    # ------------------------------------------------------------------
    # API: AI suggestion for upload metadata
    # ------------------------------------------------------------------

    @app.route("/api/mixcloud/suggest", methods=["POST"])
    def api_mixcloud_suggest():
        api_key = _settings.get("anthropic_api_key", "")
        if not api_key:
            return jsonify({"error": "Anthropic API key not set. Add it in Settings."}), 400

        tracklist_json_path = Path("output") / "tracklist.json"
        if not tracklist_json_path.exists():
            return jsonify({"error": "No built mixtape found. Build one first."}), 400

        try:
            tracks_raw = json.loads(tracklist_json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return jsonify({"error": f"Could not read tracklist: {exc}"}), 500
        if not isinstance(tracks_raw, list) or not tracks_raw:
            return jsonify({"error": "Tracklist is empty"}), 400

        # Build a compact tracklist summary for the prompt
        lines: list[str] = []
        for i, tr in enumerate(tracks_raw[:200], start=1):
            artist = str(tr.get("artist", "") or "").strip()
            title = str(tr.get("title", "") or tr.get("song", "") or "").strip()
            if artist and title:
                lines.append(f"{i}. {artist} — {title}")
            elif title:
                lines.append(f"{i}. {title}")
            elif artist:
                lines.append(f"{i}. {artist}")
        tracklist_text = "\n".join(lines)

        prompt = (
            "You are helping name a DJ mixtape that will be uploaded to Mixcloud. "
            "Based on the tracklist below, propose:\n"
            "- a short, catchy mixtape name (max 60 characters, no quotes)\n"
            "- a punchy 1-2 sentence description that captures the vibe (max 280 characters)\n"
            "- 3-5 relevant lowercase tags (genres, moods, eras)\n\n"
            "Respond with ONLY a JSON object, no markdown fences, no commentary. "
            'Shape: {"name": "...", "description": "...", "tags": ["...", "..."]}\n\n'
            "Tracklist:\n"
            f"{tracklist_text}\n"
        )

        try:
            r = http_requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5",
                    "max_tokens": 512,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")
            text = text.strip()
            # Strip accidental markdown code fences (```json ... ``` or ``` ... ```)
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            text = text.strip()
            suggestion = json.loads(text)
        except http_requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 500
            # Log the upstream body server-side for debugging, but don't echo
            # it back to the client — it may contain the API key or other
            # sensitive details.
            body = exc.response.text[:300] if exc.response is not None else str(exc)
            logging.getLogger(__name__).warning(
                "Anthropic API error %s: %s", status, body
            )
            if status in (401, 403):
                msg = ("Anthropic API key is invalid or revoked. Update it in "
                       "Settings, or fill the fields in manually.")
            elif status == 429:
                msg = "Anthropic rate limit / quota reached. Try again later, or fill the fields in manually."
            else:
                msg = f"Anthropic API error ({status})"
            return jsonify({"error": msg}), 502
        except json.JSONDecodeError:
            return jsonify({"error": "AI returned invalid JSON"}), 502
        except Exception:
            logging.getLogger(__name__).exception("AI suggestion failed")
            return jsonify({"error": "AI suggestion failed"}), 500

        name = str(suggestion.get("name", "")).strip()[:100]
        description = str(suggestion.get("description", "")).strip()[:1000]
        raw_tags = suggestion.get("tags", [])
        tags: list[str] = []
        if isinstance(raw_tags, list):
            for t in raw_tags[:10]:
                s = str(t).strip().lstrip("#").lower()
                if s:
                    tags.append(s)

        return jsonify({"name": name, "description": description, "tags": tags})

    # ------------------------------------------------------------------
    # API: Cover art
    # ------------------------------------------------------------------

    @app.route("/api/cover/status", methods=["GET"])
    def api_cover_status():
        """Report whether a base image has been dropped into cover/."""
        base = cover_mod.find_base_image(_COVER_DIR)
        return jsonify({
            "has_base": base is not None,
            "base_filename": base.name if base is not None else None,
            "presets": list(cover_mod.PRESETS),
        })

    @app.route("/api/cover/preview", methods=["GET"])
    def api_cover_preview():
        """Render a cover preview and stream it back as a JPEG.

        Query params:
          - title:      the mixtape title to overlay (required)
          - preset:     one of cover.PRESETS (default 'neon')
          - text_size:  'small' | 'medium' | 'large' (default 'medium')
        """
        # Cap title at 200 chars to bound work inside the cover generator's
        # auto-wrap / auto-fit loop — otherwise an adversarial huge title
        # would cause the preview request to hang.
        title = request.args.get("title", "").strip()[:200]
        preset = request.args.get("preset", "neon").strip() or "neon"
        text_scale = _resolve_text_scale(request.args.get("text_size"))
        if not title:
            return jsonify({"error": "title is required"}), 400

        base = cover_mod.find_base_image(_COVER_DIR)
        if base is None:
            return jsonify({
                "error": "No base image. Drop a cover_base.jpg/.png into the cover/ folder.",
            }), 404

        # Flask's send_file resolves relative paths against the app's
        # root_path (the package directory), not cwd — pass an absolute
        # path so it picks up the file we actually just wrote.
        preview_path = (Path("output") / "cover_preview.jpg").resolve()
        try:
            cover_mod.generate_cover(base, title, preset, preview_path,
                                     text_scale=text_scale)
        except Exception:
            logging.getLogger(__name__).exception("cover preview failed")
            return jsonify({"error": "Cover rendering failed"}), 500

        resp = send_file(str(preview_path), mimetype="image/jpeg", max_age=0)
        # Disable caching so the modal preview updates as title/preset change.
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    # ------------------------------------------------------------------
    # API: Mixcloud Upload
    # ------------------------------------------------------------------

    @app.route("/api/mixcloud/upload", methods=["POST"])
    def api_mixcloud_upload():
        access_token = _settings.get("mixcloud_access_token", "")
        if not access_token:
            return jsonify({"error": "Not connected to Mixcloud. Go to Settings to connect."}), 400
        data = request.get_json(force=True)
        # Cap name at 200 chars to match Mixcloud's own limit and to bound
        # work inside the cover generator's auto-wrap / auto-fit loop.
        name = data.get("name", "").strip()[:200]
        if not name:
            return jsonify({"error": "Mixtape name is required"}), 400
        description = str(data.get("description", ""))[:1000]
        tags: list[str] = data.get("tags", [])
        cover_preset = str(data.get("cover_preset", "neon")).strip() or "neon"
        # Pass the raw value through — _resolve_text_scale handles None,
        # empty strings, and unknown values uniformly. Wrapping with str()
        # would turn a JSON null into the literal "None", which is harmless
        # today but silently diverges from how /api/cover/preview does it.
        cover_text_scale = _resolve_text_scale(data.get("text_size"))

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

                # Generate cover art if a base image exists. Failure here
                # is logged but does not abort the upload — we just send
                # without a picture (old behavior).
                cover_path: Path | None = None
                base_image = cover_mod.find_base_image(_COVER_DIR)
                if base_image is not None:
                    _upload_jobs[job_id]["progress"] = "Rendering cover art..."
                    try:
                        cover_path = Path("output") / "cover.jpg"
                        cover_mod.generate_cover(base_image, name, cover_preset, cover_path,
                                                 text_scale=cover_text_scale)
                    except Exception:
                        logging.getLogger(__name__).exception("cover generation failed; uploading without picture")
                        cover_path = None

                _upload_jobs[job_id]["progress"] = "Uploading to Mixcloud..."
                mp3_f = None
                cover_f = None
                try:
                    # Open both file handles inside the try so a failure
                    # on the second open cannot leak the first.
                    mp3_f = mp3_path.open("rb")
                    if cover_path is not None:
                        cover_f = cover_path.open("rb")
                    files: dict[str, tuple[str, Any, str]] = {
                        "mp3": (mp3_path.name, mp3_f, "audio/mpeg"),
                    }
                    if cover_f is not None:
                        files["picture"] = (cover_path.name, cover_f, "image/jpeg")
                    r = http_requests.post(url, data=form_data, files=files, timeout=60 * 30)
                finally:
                    if mp3_f is not None:
                        mp3_f.close()
                    if cover_f is not None:
                        cover_f.close()
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
            except http_requests.HTTPError as exc:
                # The upload URL embeds the access token as a query
                # parameter, and HTTPError's str() includes the full URL.
                # Log the details server-side and return only the status
                # code to the client so the token cannot leak into the
                # upload-status polling response.
                status_code = exc.response.status_code if exc.response is not None else 0
                body = exc.response.text[:500] if exc.response is not None else ""
                logging.getLogger(__name__).warning(
                    "Mixcloud upload HTTP %s: %s", status_code, body
                )
                _upload_jobs[job_id]["status"] = "error"
                _upload_jobs[job_id]["error"] = (
                    f"Mixcloud upload failed (HTTP {status_code})"
                )
            except Exception:
                # Same rationale — any exception raised while `url` is
                # in scope could stringify to include the token.
                logging.getLogger(__name__).exception("Mixcloud upload failed")
                _upload_jobs[job_id]["status"] = "error"
                _upload_jobs[job_id]["error"] = "Mixcloud upload failed"

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
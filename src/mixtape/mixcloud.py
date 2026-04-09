from __future__ import annotations

import json
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests


def _save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def auth_cmd(client_id: str, client_secret: str, redirect_uri: str, token_path: Path) -> int:
    """
    Minimal OAuth flow:
    - Open authorization URL in browser
    - Receive ?code=... at localhost redirect
    - Exchange code for access_token
    """

    state = secrets.token_urlsafe(16)
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        print("For this helper, redirect-uri must be http://localhost:<port>/callback", file=sys.stderr)
        return 2

    port = parsed.port or 80
    expected_path = parsed.path or "/callback"

    code_box: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            u = urllib.parse.urlparse(self.path)
            if u.path != expected_path:
                self.send_response(404)
                self.end_headers()
                return

            q = urllib.parse.parse_qs(u.query)
            if q.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid state.")
                return

            code = q.get("code", [""])[0]
            if not code:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code.")
                return

            code_box["code"] = code
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK. You can close this tab and return to the terminal.")

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

    httpd = HTTPServer(("localhost", port), Handler)

    def serve():
        httpd.timeout = 0.5
        while "code" not in code_box:
            httpd.handle_request()

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    auth_url = "https://www.mixcloud.com/oauth/authorize"
    url = auth_url + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
    )

    print("Opening browser for Mixcloud authorization...")
    print(url)
    webbrowser.open(url)

    # Wait for code
    for _ in range(600):
        if "code" in code_box:
            break
        time.sleep(0.25)
    else:
        print("Timed out waiting for OAuth callback.", file=sys.stderr)
        return 1

    token_url = "https://www.mixcloud.com/oauth/access_token"
    r = requests.post(
        token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code_box["code"],
        },
        timeout=60,
    )
    r.raise_for_status()
    token = r.json()
    if "access_token" not in token:
        print(f"Unexpected token response: {token}", file=sys.stderr)
        return 1

    _save_json(token_path, token)
    print(f"Saved token to: {token_path}")
    return 0


def upload_cmd(
    token_path: Path,
    name: str,
    mp3_path: Path,
    tracklist_json_path: Path,
    description: str,
    tags: list[str],
    percentage_music: int,
) -> int:
    if not token_path.exists():
        print(f"Token file not found: {token_path}. Run `mixtape auth` first.", file=sys.stderr)
        return 2
    token = _load_json(token_path)
    access_token = token.get("access_token")
    if not access_token:
        print("Token file missing access_token.", file=sys.stderr)
        return 2

    if not mp3_path.exists():
        print(f"MP3 not found: {mp3_path}", file=sys.stderr)
        return 2
    if not tracklist_json_path.exists():
        print(f"Tracklist JSON not found: {tracklist_json_path}", file=sys.stderr)
        return 2

    tracks = json.loads(tracklist_json_path.read_text(encoding="utf-8"))
    if not isinstance(tracks, list):
        print("Tracklist JSON must be a list.", file=sys.stderr)
        return 2

    url = f"https://api.mixcloud.com/upload/?access_token={urllib.parse.quote(access_token)}"

    data: dict[str, str] = {
        "name": name,
        "description": description or "",
        "percentage_music": str(int(percentage_music)),
    }

    for i, tag in enumerate(tags[:20]):
        data[f"tags-{i}-tag"] = tag

    # Sections (track markers)
    for i, tr in enumerate(tracks[:500]):
        artist = str(tr.get("artist", "") or "")
        song = str(tr.get("title", "") or tr.get("song", "") or "")
        start_time = tr.get("start_time_s", tr.get("start_time", 0))
        try:
            start_i = int(float(start_time))
        except Exception:
            start_i = 0
        if artist:
            data[f"sections-{i}-artist"] = artist
        if song:
            data[f"sections-{i}-song"] = song
        data[f"sections-{i}-start_time"] = str(start_i)

    with mp3_path.open("rb") as f:
        files = {"mp3": (mp3_path.name, f, "audio/mpeg")}
        r = requests.post(url, data=data, files=files, timeout=60 * 30)
    r.raise_for_status()
    resp = r.json()
    print(json.dumps(resp, indent=2))
    return 0


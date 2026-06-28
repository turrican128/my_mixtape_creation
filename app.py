#!/usr/bin/env python
"""One-command launcher for the Mixtape web app (dev).

    python app.py

Starts the Flask web UI and opens your browser:
  - Mixtape web UI  on http://127.0.0.1:5050

Port 5050 is used (not Flask's usual 5000) so it never clashes with the
8bit Legends editor, which runs on :5000.

From there you choose tracks, pick transitions, build the mix and upload to
Mixcloud — all in the browser. This just wraps `python -m mixtape serve` so
you don't have to remember the command. Frees :5050 first if a stale run is
holding it, streams the log, and stops on Ctrl+C.

Flags:
    --no-open      don't auto-open the browser
    --keep-port    don't free :5050 if already in use
    --port N       listen on a different port (default 5050)
"""

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 5050

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def _free_port(port: int) -> None:
    """Best-effort: kill whatever is listening on `port` (a stale run)."""
    if not _port_in_use(port):
        return
    own = os.getpid()
    try:
        if sys.platform == "win32":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True, timeout=10).stdout
            pids = {int(p.split()[-1]) for p in out.splitlines()
                    if "LISTENING" in p and p.split()[1].endswith(f":{port}") and p.split()[-1].isdigit()}
            for pid in pids:
                if pid not in (0, own):
                    print(f"  freeing :{port} (pid {pid})", file=sys.stderr)
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
        else:
            out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                                 capture_output=True, text=True, timeout=10).stdout
            for pid in (int(p) for p in out.split() if p.strip().isdigit()):
                if pid != own:
                    print(f"  freeing :{port} (pid {pid})", file=sys.stderr)
                    os.kill(pid, 9)
    except Exception as e:
        print(f"  [warn] couldn't free :{port}: {type(e).__name__}: {e}", file=sys.stderr)


def _stream(proc: subprocess.Popen, tag: str) -> None:
    try:
        for line in proc.stdout:
            sys.stdout.write(f"[{tag}] {line}")
            sys.stdout.flush()
    except Exception:
        pass  # pipe closed on shutdown — nothing to do


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the Mixtape web app (Flask).")
    parser.add_argument("--no-open", action="store_true", help="don't auto-open the browser")
    parser.add_argument("--keep-port", action="store_true", help="don't free :5050 first")
    parser.add_argument("--port", type=int, default=PORT, help="port to listen on (default 5050)")
    args = parser.parse_args()

    port = args.port
    url = f"http://{HOST}:{port}"

    # Make sure the package is importable (installed editable).
    try:
        import mixtape  # noqa: F401
    except ImportError:
        print("[ERROR] 'mixtape' package not installed. Run:  pip install -e .", file=sys.stderr)
        return 1

    if not args.keep_port:
        _free_port(port)
        time.sleep(0.8)

    print(f"\n  Mixtape web app launcher")
    print(f"  app → {url}   (open this)")
    print("  Ctrl+C to stop\n", flush=True)

    # Reuse whatever Python is running this launcher. encoding/errors set
    # explicitly so any non-ASCII log output doesn't crash a cp1252 console.
    serve = subprocess.Popen(
        [sys.executable, "-m", "mixtape", "serve", "--host", HOST, "--port", str(port)],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )

    threading.Thread(target=_stream, args=(serve, "app"), daemon=True).start()

    if not args.no_open:
        threading.Timer(2.5, lambda: webbrowser.open(url)).start()

    def shutdown(*_):
        if serve.poll() is None:
            try:
                serve.terminate()
            except Exception:
                pass
        print("\n  stopped.", file=sys.stderr)

    try:
        while serve.poll() is None:
            time.sleep(0.5)
        print("[app] exited.", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

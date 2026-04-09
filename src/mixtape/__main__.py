from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .audio import build_mix
from .mixcloud import auth_cmd, upload_cmd
from .tracklist import TrackParseStyle


def _path(p: str) -> Path:
    return Path(p).expanduser()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mixtape")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="Build a glued mix from a folder of tracks")
    p_build.add_argument("--input", type=_path, default=_path("Music for mixtape"))
    p_build.add_argument("--out", type=_path, default=_path("output/mixtape.mp3"))
    p_build.add_argument("--tracklist-txt", type=_path, default=_path("output/tracklist.txt"))
    p_build.add_argument("--tracklist-json", type=_path, default=_path("output/tracklist.json"))
    p_build.add_argument("--manifest", type=_path, default=None, help="Optional mixtape.yaml")
    p_build.add_argument("--crossfade", type=float, default=6.0, help="Seconds of crossfade overlap")
    p_build.add_argument("--first-track", default=None, help="Pin this exact filename as the first track")
    p_build.add_argument(
        "--fx",
        choices=["none", "dj-random"],
        default="none",
        help="Optional transition FX mode (dj-random varies crossfade curves and adds subtle FX sometimes)",
    )
    p_build.add_argument("--fx-prob", type=float, default=0.35, help="Probability of applying a transition FX (dj-random only)")
    p_build.add_argument("--fx-seed", type=int, default=None, help="Seed for deterministic randomness (dj-random only)")
    p_build.add_argument("--parse-style", choices=[s.value for s in TrackParseStyle], default=TrackParseStyle.artist_dash_title.value)
    p_build.add_argument("--dry-run", action="store_true", help="Only print the planned ffmpeg command")

    p_auth = sub.add_parser("auth", help="Authorize with Mixcloud (OAuth)")
    p_auth.add_argument("--client-id", required=True)
    p_auth.add_argument("--client-secret", required=True)
    p_auth.add_argument("--redirect-uri", default="http://localhost:8765/callback")
    p_auth.add_argument("--token-path", type=_path, default=_path(".mixcloud_token.json"))

    p_upload = sub.add_parser("upload", help="Upload a built MP3 to Mixcloud")
    p_upload.add_argument("--name", required=True)
    p_upload.add_argument("--mp3", type=_path, default=_path("dist/mixtape.mp3"))
    p_upload.add_argument("--tracklist", type=_path, default=_path("dist/tracklist.json"))
    p_upload.add_argument("--description", default="")
    p_upload.add_argument("--tag", action="append", default=[], help="Repeatable tag")
    p_upload.add_argument("--token-path", type=_path, default=_path(".mixcloud_token.json"))
    p_upload.add_argument("--percentage-music", type=int, default=100)

    args = parser.parse_args(argv)

    if args.cmd == "build":
        return build_mix(
            input_dir=args.input,
            out_mp3=args.out,
            crossfade_s=args.crossfade,
            fx_mode=args.fx,
            fx_prob=args.fx_prob,
            fx_seed=args.fx_seed,
            manifest_path=args.manifest,
            parse_style=TrackParseStyle(args.parse_style),
            tracklist_txt_path=args.tracklist_txt,
            tracklist_json_path=args.tracklist_json,
            first_track=args.first_track,
            dry_run=args.dry_run,
        )
    if args.cmd == "auth":
        return auth_cmd(
            client_id=args.client_id,
            client_secret=args.client_secret,
            redirect_uri=args.redirect_uri,
            token_path=args.token_path,
        )
    if args.cmd == "upload":
        return upload_cmd(
            token_path=args.token_path,
            name=args.name,
            mp3_path=args.mp3,
            tracklist_json_path=args.tracklist,
            description=args.description,
            tags=args.tag,
            percentage_music=args.percentage_music,
        )

    print(f"Unknown command: {args.cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


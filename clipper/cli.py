"""Command line interface.

    clipper serve                         # web UI on http://localhost:8000
    clipper vod URL --start 1:00 --end 1:30 [--precise] [--vertical [--focus 0.5]]
    clipper live URL                      # press Enter to clip the last 30s
    clipper live URL --auto               # also auto-clip loud moments / busy chat
    clipper vertical FILE [--focus 0.5]   # make a 9:16 (1080x1920) copy of a clip

`clipper live` options: --buffer SECS, --seconds SECS, --out DIR, --vertical,
--auto, --no-loud, --no-chat, --before SECS, --after SECS, --loud-db DB.
"""

import argparse
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path


def clips_folder(args):
    """Where CLI clips go: --out, else the Clips folder of Clipper's home."""
    from .storage import FOLDERS, default_home
    return args.out or default_home() / FOLDERS["clips"]


def cmd_serve(args):
    import uvicorn
    if args.data:
        print("--data is no longer used; use --home (files go in Clips/, Downloads/ ...).",
              file=sys.stderr)
    os.environ["CLIPPER_LISTEN_HOST"] = args.host  # lets the page say if phones can reach it
    if args.home:
        os.environ["CLIPPER_HOME"] = str(Path(args.home).expanduser().resolve())
    uvicorn.run("clipper.server:app", host=args.host, port=args.port, access_log=False)


def to_vertical(path, focus):
    """Replace a horizontal clip with its 9:16 version and return the new path."""
    from .edit import make_vertical
    path = Path(path)
    vertical = make_vertical(path, focus=focus)
    path.unlink(missing_ok=True)
    return vertical


def cmd_vod(args):
    from .vod import clip_vod
    print("Downloading clip…", file=sys.stderr)
    path = clip_vod(args.url, args.start, args.end, clips_folder(args), precise=args.precise)
    if args.vertical:
        print("Making 9:16 version…", file=sys.stderr)
        path = to_vertical(path, args.focus)
    print(path)


def cmd_vertical(args):
    from .edit import make_vertical
    src = Path(args.file)
    if not src.is_file():
        sys.exit(f"clipper: no such file: {src}")
    print(make_vertical(src, focus=args.focus))


def background_submit(label, fn):
    """AutoClipper `submit` for the CLI: save in a thread, report on stderr."""
    def work():
        try:
            path = fn()
            print(f"Auto clip saved: {path}", file=sys.stderr)
        except Exception as e:
            print(f"Auto clip failed ({label}): {e}", file=sys.stderr)
    print(f"Saving {label}…", file=sys.stderr)
    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t


def start_auto(rec, args):
    from .auto import AutoClipper
    auto = AutoClipper(rec, clips_folder(args), submit=background_submit, audio=args.loud,
                       chat=args.chat, before=args.before, after=args.after,
                       vertical=args.vertical, loud_db=args.loud_db).start()
    kinds = [k for k, on in (("loud moments", args.loud), ("busy chat", args.chat)) if on]
    print(f"  auto-clipping on {' and '.join(kinds) or 'nothing'} "
          f"({args.before:g}s before, {args.after:g}s after)", file=sys.stderr)
    return auto


def cmd_live(args):
    from .live import LiveRecorder
    work = tempfile.mkdtemp(prefix="clipper_")
    rec = LiveRecorder(args.url, work, buffer_seconds=args.buffer).start()
    print(f"Watching {args.url}\n"
          f"  Enter      -> clip last {args.seconds:g}s\n"
          f"  <number>   -> clip last <number> seconds\n"
          f"  q          -> quit", file=sys.stderr)
    auto = start_auto(rec, args) if args.auto else None
    try:
        while True:
            line = input().strip().lower()
            if line == "q":
                break
            if rec.status != "recording":
                print(f"Recorder {rec.status}: {rec.error or ''}", file=sys.stderr)
            try:
                seconds = float(line) if line else args.seconds
                path = rec.clip(seconds, out_dir=clips_folder(args))
                print(to_vertical(path, args.focus) if args.vertical else path)
            except Exception as e:
                print(f"Clip failed: {e}", file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        if auto:
            auto.stop()
        rec.discard()
        shutil.rmtree(work, ignore_errors=True)


def add_vertical_args(p, help_text):
    p.add_argument("--vertical", action="store_true", help=help_text)
    p.add_argument("--focus", type=float, default=0.5,
                   help="9:16 crop position: 0 = left, 0.5 = centre, 1 = right")


def build_parser():
    p = argparse.ArgumentParser(prog="clipper", description="Clip YouTube videos and live streams.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--home", help="folder for Clips/, Highlights/, Downloads/ and Logs/ "
                                  "(default: the Clipper folder itself)")
    s.add_argument("--data", help=argparse.SUPPRESS)  # old option, ignored
    s.set_defaults(fn=cmd_serve)

    v = sub.add_parser("vod", help="clip a section of a finished video")
    v.add_argument("url")
    v.add_argument("--start", required=True)
    v.add_argument("--end", required=True)
    v.add_argument("--out", help="folder to save in (default: Clips/ in Clipper's folder)")
    v.add_argument("--precise", action="store_true", help="frame-accurate cut (re-encodes)")
    add_vertical_args(v, "save only a 9:16 (1080x1920) version")
    v.set_defaults(fn=cmd_vod)

    l = sub.add_parser("live", help="watch a live stream and clip on demand")
    l.add_argument("url")
    l.add_argument("--buffer", type=int, default=300, help="seconds of rewind to keep")
    l.add_argument("--seconds", type=float, default=30, help="default clip length")
    l.add_argument("--out", help="folder to save in (default: Clips/ in Clipper's folder)")
    add_vertical_args(l, "save clips as 9:16 (1080x1920)")
    l.add_argument("--auto", action="store_true",
                   help="also clip loud moments and chat spikes automatically")
    l.add_argument("--no-loud", dest="loud", action="store_false",
                   help="with --auto: don't clip on loud moments")
    l.add_argument("--no-chat", dest="chat", action="store_false",
                   help="with --auto: don't clip on chat spikes")
    l.add_argument("--before", type=float, default=60, help="auto clips: seconds before the moment")
    l.add_argument("--after", type=float, default=60, help="auto clips: seconds after the moment")
    l.add_argument("--loud-db", type=float, default=8.0,
                   help="auto clips: how many dB above normal counts as loud")
    l.set_defaults(fn=cmd_live)

    e = sub.add_parser("vertical", help="make a 9:16 (1080x1920) copy of a clip")
    e.add_argument("file")
    e.add_argument("--focus", type=float, default=0.5,
                   help="crop position: 0 = left, 0.5 = centre, 1 = right")
    e.set_defaults(fn=cmd_vertical)
    return p


def add_venv_to_path():
    """Let `.venv/bin/clipper` find the venv's yt-dlp without activating it."""
    bin_dir = str(Path(sys.executable).parent)
    if not shutil.which("yt-dlp") and Path(bin_dir, "yt-dlp").exists():
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


def main(argv=None):
    args = build_parser().parse_args(argv)
    add_venv_to_path()
    args.fn(args)


if __name__ == "__main__":
    main()

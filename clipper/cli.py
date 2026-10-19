"""Command line interface.

    clipper serve                         # web UI on http://localhost:8000
    clipper vod URL --start 1:00 --end 1:30
    clipper live URL                      # press Enter to clip the last 30s
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path


def cmd_serve(args):
    import os
    import uvicorn
    os.environ["CLIPPER_DATA"] = str(Path(args.data).resolve())
    uvicorn.run("clipper.server:app", host=args.host, port=args.port)


def cmd_vod(args):
    from .vod import clip_vod
    print("Downloading clip…", file=sys.stderr)
    print(clip_vod(args.url, args.start, args.end, args.out, precise=args.precise))


def cmd_live(args):
    from .live import LiveRecorder
    work = tempfile.mkdtemp(prefix="clipper_")
    rec = LiveRecorder(args.url, work, buffer_seconds=args.buffer).start()
    print(f"Watching {args.url}\n"
          f"  Enter      -> clip last {args.seconds}s\n"
          f"  <number>   -> clip last <number> seconds\n"
          f"  q          -> quit", file=sys.stderr)
    try:
        while True:
            line = input().strip().lower()
            if line == "q":
                break
            if rec.status != "recording":
                print(f"Recorder {rec.status}: {rec.error or ''}", file=sys.stderr)
            seconds = float(line) if line else args.seconds
            try:
                print(rec.clip(seconds, out_dir=args.out))
            except Exception as e:
                print(f"Clip failed: {e}", file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        rec.stop()


def main(argv=None):
    p = argparse.ArgumentParser(prog="clipper", description="Clip YouTube videos and live streams.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--data", default="data", help="where clips and buffers are stored")
    s.set_defaults(fn=cmd_serve)

    v = sub.add_parser("vod", help="clip a section of a finished video")
    v.add_argument("url")
    v.add_argument("--start", required=True)
    v.add_argument("--end", required=True)
    v.add_argument("--out", default="clips")
    v.add_argument("--precise", action="store_true", help="frame-accurate cut (re-encodes)")
    v.set_defaults(fn=cmd_vod)

    l = sub.add_parser("live", help="watch a live stream and clip on demand")
    l.add_argument("url")
    l.add_argument("--buffer", type=int, default=300, help="seconds of rewind to keep")
    l.add_argument("--seconds", type=float, default=30, help="default clip length")
    l.add_argument("--out", default="clips")
    l.set_defaults(fn=cmd_live)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()

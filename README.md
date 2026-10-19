# Clipper

Clip YouTube videos, past broadcasts and **live streams in real time**.
Works with anything [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports — YouTube, Twitch, Kick, and more.

## How it works

- **Videos / VODs** — yt-dlp downloads only the section you ask for.
- **Live streams** — the stream is recorded into a rolling buffer of 2-second
  segments (default: last 5 minutes). Clipping "the last 30s" just stitches the
  newest segments together without re-encoding, so it is near-instant. Old
  segments are deleted automatically.

## Setup

Requirements: Python 3.10+, [ffmpeg](https://ffmpeg.org/download.html), and a
JavaScript runtime ([deno](https://deno.com) or Node.js — YouTube needs one).

```bash
pip install -e .
```

## Use it

### Web UI
```bash
clipper serve            # open http://localhost:8000
```
Paste a URL, pick a time range or start watching a live stream, then hit
**Last 30s** (or press **C**). Clips appear below with a preview and download link.

### Command line
```bash
clipper vod "https://youtu.be/VIDEO" --start 1:23 --end 1:53
clipper vod "https://youtu.be/VIDEO" --start 1:23 --end 1:53 --precise   # frame-accurate

clipper live "https://www.youtube.com/@channel/live" --buffer 600
#   Enter -> clip last 30s,  45 -> clip last 45s,  q -> quit
```

Clips are saved to `data/clips/` (web UI) or `clips/` (CLI).

## Tests
```bash
pip install -e '.[dev]' && pytest
```
The tests simulate a live stream with ffmpeg, so they need no internet.

## Notes
- If YouTube starts failing, update yt-dlp first: `pip install -U "yt-dlp[default]"`.
- For members-only or age-restricted content, yt-dlp can use your browser's
  cookies (`--cookies-from-browser`).
- Respect creators' rights and platform terms — clip content you own or have
  permission to use.

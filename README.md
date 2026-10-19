# Clipper

Clip YouTube videos, past broadcasts and **live streams in real time** —
by hand, or automatically when a stream gets loud or chat goes wild.
Works with what [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports (see [Supported sites](#supported-sites)).

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

- **Player** — preview the video in the page and mark the clip with **I** (in) and **O** (out).
- **Vertical 9:16** — tick it to get a 1080x1920 crop (Shorts, TikTok, Reels), or press
  **Make 9:16** on any finished clip.
- **Auto-clip** (live) — saves a clip when the stream gets much louder than usual
  and/or when chat suddenly gets busy, keeping 60s before and 60s after the moment
  (adjustable). Overlapping moments are merged into one clip.
- **Quit Clipper** — stops the server and any recordings; saved clips are kept.

### Command line
```bash
clipper vod "https://youtu.be/VIDEO" --start 1:23 --end 1:53
clipper vod "https://youtu.be/VIDEO" --start 1:23 --end 1:53 --precise   # frame-accurate

clipper vod "https://youtu.be/VIDEO" --start 1:23 --end 1:53 --vertical  # 9:16 only
#   --focus 0..1 picks the 9:16 slice: 0 = left, 0.5 = centre (default), 1 = right

clipper live "https://www.youtube.com/@channel/live" --buffer 600
#   Enter -> clip last 30s,  45 -> clip last 45s,  q -> quit
clipper live "https://www.twitch.tv/CHANNEL" --auto            # + auto-clip loud moments & busy chat
clipper live URL --auto --no-chat --before 30 --after 30 --loud-db 6 --vertical

clipper vertical clips/live_1234.mp4 --focus 0.3                # 9:16 copy of any clip
```
`live` options: `--seconds` (default clip length), `--out`, `--vertical`, and for
`--auto`: `--no-loud` / `--no-chat` (turn a trigger off), `--before` / `--after`
(seconds around the moment, default 60) and `--loud-db` (how much louder than
usual counts as loud, default 8). Auto clip paths are printed as they're saved.

Clips are saved to `data/clips/` (web UI) or `clips/` (CLI).

## Mac app

On a Mac you can get a double-clickable **Clipper** app (needs the setup above
done in a `.venv` inside the repo, plus `brew install ffmpeg node`):

```bash
cd ~/clipper
python3 -m venv .venv && .venv/bin/pip install -e .   # once, with Homebrew's python3
bash scripts/make-mac-app.sh                          # -> ~/Applications/Clipper.app
```
Double-click it to start Clipper in the background and open http://localhost:8000;
double-click again any time to reopen the page. Stop it with **Quit Clipper** in
the page. Drag it onto the Dock to keep it there.

- If macOS blocks it the first time, right-click the app → **Open** (on macOS 15+:
  System Settings → Privacy & Security → **Open Anyway**).
- Logs: `~/Library/Logs/Clipper.log` (open with Console.app). Clips: `~/clipper/data/clips/`.
- The app runs *this* checkout; rerun the script if you move the repo.
  To remove it: `rm -rf ~/Applications/Clipper.app`.

## Supported sites

Clipper works with whatever yt-dlp can download:

- **YouTube** — videos, VODs and live streams (tested).
- **Twitch** — VODs and live streams; expected to work.
- **Kick** — yt-dlp has an extractor, but Kick's Cloudflare protection may block it.
- **X / Twitter** — live broadcasts are only partially supported.
- **Instagram Live** — not supported by yt-dlp.

Auto-clipping on busy chat works for YouTube and Twitch only; auto-clipping on
loud moments works on any stream that records.

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

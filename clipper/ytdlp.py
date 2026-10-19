"""Shared yt-dlp options."""

import shutil


def base_args():
    """Arguments every yt-dlp call needs. YouTube requires a JavaScript runtime
    to decode its formats; yt-dlp only looks for deno by default, so point it
    at whichever runtime is installed."""
    for runtime in ("deno", "node", "bun"):
        if shutil.which(runtime):
            return ["--js-runtimes", runtime]
    return []

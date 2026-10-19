"""Let phones and iPads on the same Wi-Fi use Clipper.

The Mac keeps doing the work; other devices use the same web page. They need a
private key, which comes in the link (and QR code) shown in Clipper's settings
on the Mac. Opening that link once stores the key in a cookie on the device.
"""

import io
import secrets
import socket
import subprocess
import sys

COOKIE = "clipper_key"
COOKIE_DAYS = 400
# Requests from these addresses come from the Mac itself.
LOCAL_CLIENTS = {"127.0.0.1", "::1"}
# ...and must name the server like this, which stops other websites from
# reaching Clipper through the Mac's browser (DNS rebinding).
LOCAL_HOST_NAMES = {"localhost", "127.0.0.1", "[::1]", "::1"}
# Things only the Mac may do.
LOCAL_ONLY = {("POST", "/api/quit"), ("POST", "/api/storage/open"),
              ("PUT", "/api/remote"), ("POST", "/api/remote/new-link")}


def new_key():
    return secrets.token_urlsafe(24)


def same_key(given, key):
    return bool(given and key) and secrets.compare_digest(str(given), str(key))


def host_name(host_header):
    """'localhost:8000' -> 'localhost', '[::1]:8000' -> '[::1]'."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host[: host.find("]") + 1]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def lan_addresses():
    """This Mac's address on the local network, plus its Bonjour name (Mac.local)."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # picks the outgoing interface; sends nothing
            ip = s.getsockname()[0]
        if not ip.startswith("127."):
            found.append(ip)
    except OSError:
        pass
    if sys.platform == "darwin":
        try:
            name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True,
                                  text=True, timeout=3).stdout.strip()
            if name:
                found.append(f"{name}.local")
        except (OSError, subprocess.TimeoutExpired):
            pass
    return found


def links(port, key):
    return [f"http://{host}:{port}/?key={key}" for host in lan_addresses()]


def qr_svg(text):
    """A QR code for `text` as an SVG string."""
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    out = io.BytesIO()
    img.save(out)
    return out.getvalue().decode()


NOT_CONNECTED = ("This device isn't connected to Clipper. Scan the QR code in Clipper's "
                 "settings on your Mac.")

NOT_CONNECTED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clipper</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;font:16px/1.5 system-ui,sans-serif;
background:#fafafa;color:#171717;padding:24px;box-sizing:border-box}
@media (prefers-color-scheme:dark){body{background:#0a0a0a;color:#ededed}}
main{max-width:30em}h1{font-size:22px;margin:0 0 8px}p{margin:0 0 10px;color:#6b6b6b}
</style></head><body><main><h1>Connect this device to Clipper</h1>
<p>On your Mac, open Clipper, then Settings &rarr; Phone &amp; iPad, and turn it on.</p>
<p>Scan the QR code with this device's camera. You only need to do this once.</p>
</main></body></html>"""

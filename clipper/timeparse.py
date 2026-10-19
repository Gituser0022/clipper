"""Parse human time strings like "90", "1:30", "01:02:03.5" into seconds."""


def parse_time(value) -> float:
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("empty time value")
        parts = text.split(":")
        if len(parts) > 3:
            raise ValueError(f"invalid time: {value!r}")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            raise ValueError(f"invalid time: {value!r}") from None
        seconds = 0.0
        for n in nums:
            seconds = seconds * 60 + n
    if seconds < 0:
        raise ValueError(f"time cannot be negative: {value!r}")
    return seconds


def format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    frac = seconds - int(seconds)
    base = f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"
    return base + (f".{int(round(frac * 1000)):03d}" if frac >= 0.0005 else "")

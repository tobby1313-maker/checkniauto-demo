"""Process-wide console logging with Windows-safe Unicode handling."""

from __future__ import annotations

import sys
from typing import Any, TextIO


def configure_console_encoding() -> None:
    """Prefer UTF-8 output without making startup depend on stream features."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass


def safe_log(message: Any, *, stream: TextIO | None = None) -> None:
    """Print a message without failing on a restrictive console encoding."""
    output = stream or sys.stdout
    text = str(message)
    try:
        print(text, file=output)
    except UnicodeEncodeError:
        encoding = getattr(output, "encoding", None) or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(
            encoding,
            errors="replace",
        )
        print(safe_text, file=output)


configure_console_encoding()

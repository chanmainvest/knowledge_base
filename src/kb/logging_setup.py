"""Logging helpers."""
from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

from .config import LOG_DIR


_CONFIGURED = False


def _utf8_stream(stream):
    # Force UTF-8 on Windows console so CJK characters do not crash logging
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
        return stream
    except Exception:
        try:
            return io.TextIOWrapper(stream.buffer, encoding="utf-8",
                                    errors="replace", line_buffering=True)
        except Exception:
            return stream


def get_logger(name: str) -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fmt = "%(asctime)s %(levelname)s %(name)s :: %(message)s"
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        h = logging.StreamHandler(_utf8_stream(sys.stdout))
        h.setFormatter(logging.Formatter(fmt))
        root.addHandler(h)
        fh = logging.FileHandler(LOG_DIR / "kb.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter(fmt))
        root.addHandler(fh)
        _CONFIGURED = True
    return logging.getLogger(name)

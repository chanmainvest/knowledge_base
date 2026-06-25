"""Markdown read/write with YAML front-matter."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

_FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


@dataclass
class MdDoc:
    front: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    def dump(self) -> str:
        fm = yaml.safe_dump(self.front, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{fm}\n---\n\n{self.body.strip()}\n"

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.dump(), encoding="utf-8")
        return path


def load_md(path: Path) -> MdDoc:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = _FM_RE.match(text)
    if not m:
        return MdDoc(front={}, body=text)
    fm = yaml.safe_load(m.group(1)) or {}
    return MdDoc(front=fm, body=m.group(2))


def slugify(s: str, maxlen: int = 80) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:maxlen].strip("-") or "x"


def jsonify_dt(d: Any) -> Any:
    if isinstance(d, datetime):
        return d.isoformat()
    return d


def safe_json_dump(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=jsonify_dt, ensure_ascii=False),
                    encoding="utf-8")

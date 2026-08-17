"""Versioned extraction prompt/schema registry.

Each version is a directory ``src/kb/prompts/extraction/<version>/`` holding
a PAIR of files that are versioned together (never independently):

  - ``system.md``   the system prompt. Markdown with a YAML front-matter
                    header (``---`` delimited); only the body after the
                    header is sent to the LLM.
  - ``schema.json`` the JSON Schema passed to ``chat_json`` for structured
                    output.

The directory name is the version, and it is exactly what lands in
``extraction_run.prompt_version`` — so runs made under different prompt/schema
versions are tracked as distinct rows (the table's unique key is
item/provider/model/prompt_version) and never overwrite each other.

To iterate on the prompt or schema: copy the newest version directory to the
next version name, edit the files, re-run. New runs pick up the new version
(the default is the highest version present, overridable per call with
``--prompt-version`` or globally by pinning ``EXTRACTION_PROMPT_VERSION`` in
``.env``); old runs and their persisted predictions stay untouched. The
front-matter ``version`` field must match the directory name — the loader
enforces this so a copy-pasted directory can never silently mis-tag runs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import settings
from .logging_setup import get_logger

log = get_logger("prompts")

PROMPTS_ROOT = Path(__file__).resolve().parent / "prompts" / "extraction"

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)


@dataclass(frozen=True)
class PromptPair:
    """One versioned (system prompt, JSON schema) pair."""
    version: str
    name: str
    system: str
    schema: dict[str, Any]
    path: Path


def _version_sort_key(v: str) -> tuple[int, str]:
    """Natural sort so v2 < v10 (lexicographic would put 'v10' first)."""
    m = re.search(r"(\d+)", v)
    return (int(m.group(1)) if m else 0, v)


def list_versions() -> list[str]:
    """All registered version names, oldest first."""
    if not PROMPTS_ROOT.exists():
        return []
    names = [p.name for p in PROMPTS_ROOT.iterdir()
             if p.is_dir() and not p.name.startswith(".")]
    return sorted(names, key=_version_sort_key)


def default_version() -> str:
    """The version new extractions use when not explicitly overridden:
    ``EXTRACTION_PROMPT_VERSION`` if pinned, else the highest present."""
    versions = list_versions()
    if not versions:
        raise RuntimeError(
            f"no extraction prompt versions found under {PROMPTS_ROOT}; "
            "expected at least one <version>/system.md + schema.json pair")
    pinned = (settings().extraction_prompt_version or "").strip()
    if pinned:
        if pinned not in versions:
            raise ValueError(
                f"EXTRACTION_PROMPT_VERSION={pinned!r} not found; "
                f"available versions: {', '.join(versions)}")
        return pinned
    return versions[-1]


def load(version: str | None = None) -> PromptPair:
    """Load one prompt/schema pair. ``version=None`` means the default."""
    version = version or default_version()
    d = PROMPTS_ROOT / version
    md_path = d / "system.md"
    schema_path = d / "schema.json"
    for p in (md_path, schema_path):
        if not p.exists():
            raise FileNotFoundError(
                f"prompt version {version!r} is incomplete: missing {p.name} "
                f"(expected under {d})")

    raw = md_path.read_text(encoding="utf-8")
    m = _FRONT_MATTER_RE.match(raw)
    if not m:
        raise ValueError(
            f"{md_path} must start with a YAML front-matter block "
            "('---' delimited) followed by the prompt body")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"{md_path}: front-matter must be a YAML mapping")
    fm_version = str(meta.get("version", version))
    if fm_version != version:
        raise ValueError(
            f"{md_path}: front-matter version {fm_version!r} does not match "
            f"its directory name {version!r} — rename one of them so runs "
            "are tagged correctly")
    system = m.group(2).strip()
    if not system:
        raise ValueError(f"{md_path}: empty prompt body")

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(schema, dict) or "type" not in schema:
        raise ValueError(f"{schema_path}: not a usable JSON Schema object")

    return PromptPair(version=version, name=str(meta.get("name", version)),
                      system=system, schema=schema, path=d)

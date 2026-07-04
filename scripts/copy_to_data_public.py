"""Copy a configured subset of data/ markdown into data_public/.

Uses scripts/data_public_config.json to decide which source/channel trees to
publish. By default only copies entries from the last 365 days. Skips files
that already exist in data_public/ with identical content.

Run
---
  uv run python scripts/copy_to_data_public.py
  uv run python scripts/copy_to_data_public.py --start-date 2025-01-01 --end-date 2025-12-31
  uv run python scripts/copy_to_data_public.py --config-json '{"sources":{"youtube":["cpm-group"]}}'
"""
from __future__ import annotations

import argparse
import filecmp
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_DIR = REPO_ROOT / "data_public"

try:
    from kb.config import DATA_DIR as DEFAULT_DATA_DIR  # respects DATA_DIR env/.env
except Exception:  # pragma: no cover — fallback if kb not importable
    DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_CONFIG = Path(__file__).resolve().parent / "data_public_config.json"
README_NAME = "README.md"
DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
SKIP_DIR_NAMES = {"raw", ".browser_profile", "__pycache__"}

# Sources with no per-channel folder (markdown lives under <source>/<year>/).
# After the blog consolidation, all sources have per-channel subdirs.
FLAT_SOURCES: frozenset[str] = frozenset()


def parse_date(text: str) -> date | None:
    match = DATE_PREFIX_RE.match(text)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def source_channels(config: dict, source: str) -> list[str] | None:
    """Return channel handles, or None when the whole source is selected."""
    sources = config.get("sources", {})
    if source not in sources:
        return []
    value = sources[source]
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"sources.{source} must be null or a list of channel handles")
    return value


def iter_markdown_files(source_root: Path, channels: list[str] | None) -> Iterator[Path]:
    if not source_root.is_dir():
        return

    if channels is None:
        roots = [source_root]
    else:
        roots = [source_root / channel for channel in channels]

    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.md"):
            if path.name == README_NAME:
                continue
            if any(part in SKIP_DIR_NAMES or part.startswith(".") for part in path.parts):
                continue
            yield path


def entry_date(path: Path) -> date | None:
    parsed = parse_date(path.name)
    if parsed is not None:
        return parsed
    for part in reversed(path.parts):
        if len(part) == 4 and part.isdigit():
            return None
    return None


def in_date_range(
    path: Path,
    start: date | None,
    end: date | None,
) -> bool:
    entry = entry_date(path)
    if entry is None:
        return False
    if start is not None and entry < start:
        return False
    if end is not None and entry > end:
        return False
    return True


def copy_file(src: Path, dst: Path, dry_run: bool) -> str:
    if dst.exists() and filecmp.cmp(src, dst, shallow=False):
        return "skipped_unchanged"
    if dry_run:
        return "would_copy"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return "copied"


def copy_source(
    source: str,
    channels: list[str] | None,
    *,
    data_dir: Path,
    public_dir: Path,
    start: date | None,
    end: date | None,
    dry_run: bool,
) -> dict[str, int]:
    stats = {
        "copied": 0,
        "skipped_unchanged": 0,
        "skipped_date": 0,
        "would_copy": 0,
    }
    source_root = data_dir / source
    for src in iter_markdown_files(source_root, channels):
        if not in_date_range(src, start, end):
            stats["skipped_date"] += 1
            continue
        rel = src.relative_to(data_dir)
        dst = public_dir / rel
        result = copy_file(src, dst, dry_run)
        stats[result] += 1
    return stats


def merge_stats(total: dict[str, int], part: dict[str, int]) -> None:
    for key, value in part.items():
        total[key] = total.get(key, 0) + value


def build_public_readmes(public_dir: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"Would rebuild README.md indexes under {public_dir}")
        return
    script = REPO_ROOT / "scripts" / "build_data_readmes.py"
    subprocess.run(
        [sys.executable, str(script), str(public_dir)],
        check=True,
        cwd=REPO_ROOT,
    )


def default_date_range() -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=365), today


def main() -> None:
    default_start, default_end = default_date_range()
    parser = argparse.ArgumentParser(
        description="Copy a configured subset of data/ markdown into data_public/.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Source data directory (default: repository data/).",
    )
    parser.add_argument(
        "--public-dir",
        type=Path,
        default=DEFAULT_PUBLIC_DIR,
        help="Destination public data directory (default: data_public/).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="JSON config file listing sources/channels to copy.",
    )
    parser.add_argument(
        "--config-json",
        help="Inline JSON config; replaces the file from --config when set.",
    )
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        default=None,
        help=f"Earliest entry date to copy (default: {default_start.isoformat()}).",
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=None,
        help=f"Latest entry date to copy (default: {default_end.isoformat()}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing files or rebuilding README indexes.",
    )
    args = parser.parse_args()

    if args.config_json:
        config = json.loads(args.config_json)
    else:
        config = load_config(args.config)

    start = args.start_date if args.start_date is not None else default_start
    end = args.end_date if args.end_date is not None else default_end
    if start > end:
        parser.error("--start-date must be on or before --end-date")

    data_dir = args.data_dir.resolve()
    public_dir = args.public_dir.resolve()
    sources = config.get("sources", {})
    if not sources:
        parser.error("config must include a non-empty 'sources' object")

    totals: dict[str, int] = {}
    for source in sorted(sources):
        channels = source_channels(config, source)
        if channels == []:
            continue
        if channels is None:
            parser.error(f"sources.{source} must be a list of channel handles, not null")

        part = copy_source(
            source,
            channels,
            data_dir=data_dir,
            public_dir=public_dir,
            start=start,
            end=end,
            dry_run=args.dry_run,
        )
        merge_stats(totals, part)
        copied = part["copied"] + part.get("would_copy", 0)
        if copied or part["skipped_unchanged"] or part["skipped_date"]:
            print(
                f"{source}: copied={copied} "
                f"unchanged={part['skipped_unchanged']} "
                f"out_of_range={part['skipped_date']}",
            )

    print(
        "Total: "
        f"copied={totals.get('copied', 0) + totals.get('would_copy', 0)} "
        f"unchanged={totals.get('skipped_unchanged', 0)} "
        f"out_of_range={totals.get('skipped_date', 0)} "
        f"({start.isoformat()} .. {end.isoformat()})",
    )

    build_public_readmes(public_dir, args.dry_run)


if __name__ == "__main__":
    main()

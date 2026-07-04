"""Build README indexes for every public folder under data/."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import quote

import yaml

README_NAME = "README.md"
SKIP_DIRS = {"raw", ".browser_profile", "__pycache__"}
FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def markdown_link(path: str, label: str) -> str:
    safe_label = label.replace("\\", r"\\").replace("[", r"\[").replace("]", r"\]")
    return f"[{safe_label}]({quote(path, safe='/#.-_~%')})"


def table_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ").strip()


def title_from_name(path: Path) -> str:
    return path.name.replace("-", " ").replace("_", " ").strip() or path.name


def markdown_title(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    front_match = FRONT_MATTER_RE.match(text)
    if front_match:
        try:
            front = yaml.safe_load(front_match.group(1)) or {}
        except yaml.YAMLError:
            front = {}
        title = front.get("title")
        if title:
            return str(title).strip()
        text = text[front_match.end():]

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()

    return path.stem.replace("-", " ").replace("_", " ").strip() or path.name


def visible_child_dirs(path: Path) -> list[Path]:
    return sorted(
        [child for child in path.iterdir() if child.is_dir() and child.name not in SKIP_DIRS and not child.name.startswith(".")],
        key=lambda child: child.name.casefold(),
    )


def markdown_files(path: Path) -> list[Path]:
    return sorted(
        [child for child in path.glob("*.md") if child.name != README_NAME],
        key=lambda child: child.name.casefold(),
    )


def count_markdown_items(path: Path) -> int:
    total = len(markdown_files(path))
    for child in visible_child_dirs(path):
        total += count_markdown_items(child)
    return total


def build_readme(path: Path, data_root: Path) -> None:
    dirs = visible_child_dirs(path)
    files = markdown_files(path)
    relative = path.relative_to(data_root)
    title = "Data" if relative == Path(".") else title_from_name(path)

    lines = [f"# {title}", ""]

    if dirs:
        lines.extend(["| Folder |", "| --- |"])
        for child in dirs:
            item_count = count_markdown_items(child)
            folder_title = title_from_name(child)
            label = table_cell(f"{folder_title} [{item_count}]")
            lines.append(f"| {markdown_link(f'{child.name}/{README_NAME}', label)} |")
        lines.append("")
    elif files:
        lines.extend(["| Title | File |", "| --- | --- |"])
        for file_path in files:
            lines.append(f"| {table_cell(markdown_title(file_path))} | {markdown_link(file_path.name, file_path.name)} |")
        lines.append("")
    else:
        lines.extend(["No markdown items found.", ""])

    (path / README_NAME).write_text("\n".join(lines), encoding="utf-8")


def build_all(data_root: Path) -> int:
    data_root = data_root.resolve()
    directories = [data_root]
    for child in data_root.rglob("*"):
        if not child.is_dir():
            continue
        try:
            parts = child.relative_to(data_root).parts
        except ValueError:
            continue
        if any(part in SKIP_DIRS or part.startswith(".") for part in parts):
            continue
        directories.append(child)

    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        build_readme(path, data_root)

    return len(directories)


def _default_data_root() -> Path:
    """Return the configured data directory, falling back to repo data/."""
    try:
        from kb.config import DATA_DIR  # respects DATA_DIR env/.env setting
        return DATA_DIR
    except Exception:  # pragma: no cover — fallback if kb not importable
        return Path(__file__).resolve().parents[1] / "data"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build README.md indexes under data/.")
    parser.add_argument(
        "data_root",
        nargs="?",
        default=None,
        type=Path,
        help="Path to the data directory. Defaults to DATA_DIR from .env (or the repository data/ folder).",
    )
    args = parser.parse_args()
    data_root = args.data_root or _default_data_root()
    count = build_all(data_root)
    print(f"Wrote README.md files for {count} folders under {data_root}")


if __name__ == "__main__":
    main()
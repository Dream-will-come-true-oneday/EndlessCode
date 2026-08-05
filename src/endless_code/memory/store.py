"""Markdown 笔记与 MEMORY.md 索引的原子维护。"""

import re
import threading
from datetime import datetime
from pathlib import Path

import yaml

from endless_code.memory.types import NoteType, UpdateAction

_SAFE_FILENAME = re.compile(r"^[a-z_]+\.md$")
_SAFE_SLUG = re.compile(r"^[a-z0-9_]+$")
_INDEX_MAX_BYTES = 25_000
_INDEX_MAX_LINES = 200


class Store:
    """管理单个层级的笔记正文和由正文重建的索引。"""

    def __init__(self, directory: str) -> None:
        self._dir = Path(directory)
        self._lock = threading.Lock()

    @property
    def directory(self) -> Path:
        return self._dir

    def ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def load_index(self) -> str:
        try:
            return (self._dir / "MEMORY.md").read_text(encoding="utf-8")
        except OSError:
            return ""

    def apply(self, actions: list[UpdateAction]) -> None:
        with self._lock:
            self.ensure_dir()
            for action in actions:
                if action.action == "create":
                    self._create(action)
                elif action.action == "update":
                    self._update(action)
                elif action.action == "delete":
                    self._delete(action)
            self._rebuild_index()

    def _create(self, action: UpdateAction) -> None:
        try:
            note_type = NoteType(action.type)
        except ValueError:
            return
        if not _SAFE_SLUG.fullmatch(action.slug):
            return
        filename = f"{note_type.value}_{action.slug}.md"
        self._write_note(
            self._dir / filename, note_type.value, action.title, action.content
        )

    def _update(self, action: UpdateAction) -> None:
        path = self._safe_path(action.filename)
        if path is None or not path.is_file():
            return
        meta, _ = _read_note(path)
        note_type = str(meta.get("type", ""))
        if note_type not in {item.value for item in NoteType}:
            return
        self._write_note(
            path, note_type, action.title, action.content, meta.get("created")
        )

    def _delete(self, action: UpdateAction) -> None:
        path = self._safe_path(action.filename)
        if path is not None:
            path.unlink(missing_ok=True)

    def _safe_path(self, filename: str) -> Path | None:
        if not _SAFE_FILENAME.fullmatch(filename) or filename == "MEMORY.md":
            return None
        return self._dir / filename

    def _write_note(
        self,
        path: Path,
        note_type: str,
        title: str,
        content: str,
        created: object | None = None,
    ) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        frontmatter = {
            "type": note_type,
            "title": title.strip()[:120],
            "created": created or now,
            "updated": now,
        }
        text = f"---\n{yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False)}---\n{content.strip()}\n"
        path.write_text(text, encoding="utf-8")

    def _rebuild_index(self) -> None:
        rows: list[str] = []
        for path in sorted(self._dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            meta, content = _read_note(path)
            note_type = meta.get("type")
            title = meta.get("title")
            if not isinstance(note_type, str) or not isinstance(title, str):
                continue
            summary = " ".join(content.strip().split())[:160]
            rows.append(f"- [{note_type}] {title} — {summary}")
        kept: list[str] = []
        size = 0
        for row in rows[:_INDEX_MAX_LINES]:
            row_size = len((row + "\n").encode("utf-8"))
            if size + row_size > _INDEX_MAX_BYTES:
                break
            kept.append(row)
            size += row_size
        (self._dir / "MEMORY.md").write_text(
            "\n".join(kept) + ("\n" if kept else ""), encoding="utf-8"
        )


def _read_note(path: Path) -> tuple[dict, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, ""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta if isinstance(meta, dict) else {}, text[end + 5 :]

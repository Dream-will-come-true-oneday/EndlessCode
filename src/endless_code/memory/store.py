"""Markdown 笔记与 MEMORY.md 索引的原子维护。"""

import os
import re
import tempfile
import threading
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import yaml

from endless_code.memory.types import Note, NoteType, ScopeOverview, UpdateAction

_SAFE_FILENAME = re.compile(r"^[a-z0-9_]+\.md$")
_SAFE_SLUG = re.compile(r"^[a-z0-9_]+$")
_INDEX_MAX_BYTES = 12_000
_INDEX_MAX_LINES = 200


class Store:
    """管理单个层级的笔记正文和由正文重建的索引。"""

    def __init__(
        self, directory: str, allowed_types: frozenset[NoteType] | None = None
    ) -> None:
        self._dir = Path(directory)
        self._allowed_types = allowed_types or frozenset(NoteType)
        self._lock = threading.RLock()

    @property
    def directory(self) -> Path:
        return self._dir

    def ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def load_index(self) -> str:
        with self._lock:
            return self._refresh_index_locked()

    def list_notes(self) -> list[Note]:
        with self._lock:
            self._refresh_index_locked()
            return self._list_notes_locked()

    def overview(self, scope: str) -> ScopeOverview:
        with self._lock:
            self._refresh_index_locked()
            notes = self._list_notes_locked()
            counts = Counter(note.type.value for note in notes)
            total_bytes = sum(
                path.stat().st_size for path in self._dir.glob("*.md") if path.is_file()
            )
            return ScopeOverview(
                scope=scope,
                directory=self._dir,
                total_bytes=total_bytes,
                counts=dict(sorted(counts.items())),
                notes=tuple(notes),
            )

    def clear(self) -> int:
        with self._lock:
            notes = self._list_notes_locked()
            for note in notes:
                (self._dir / note.filename).unlink(missing_ok=True)
            self._refresh_index_locked()
            return len(notes)

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
            self._refresh_index_locked()

    def _create(self, action: UpdateAction) -> None:
        try:
            note_type = NoteType(action.type)
        except ValueError:
            return
        if note_type not in self._allowed_types:
            return
        if (
            not _SAFE_SLUG.fullmatch(action.slug)
            or not action.title.strip()
            or not action.content.strip()
        ):
            return
        filename = f"{note_type.value}_{action.slug}.md"
        path = self._dir / filename
        if path.exists():
            return
        self._write_note(path, note_type.value, action.title, action.content)

    def _update(self, action: UpdateAction) -> None:
        path = self._safe_path(action.filename)
        if path is None or not path.is_file():
            return
        note = _read_note(path)
        if (
            note is None
            or note.type not in self._allowed_types
            or note.type.value != action.type
            or not action.title.strip()
            or not action.content.strip()
        ):
            return
        self._write_note(
            path,
            note.type.value,
            action.title,
            action.content,
            note.created.isoformat(timespec="seconds"),
        )

    def _delete(self, action: UpdateAction) -> None:
        path = self._safe_path(action.filename)
        note = _read_note(path) if path is not None else None
        if (
            path is not None
            and note is not None
            and note.type in self._allowed_types
            and note.type.value == action.type
        ):
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
        _atomic_write(path, text)

    def _list_notes_locked(self) -> list[Note]:
        if not self._dir.exists():
            return []
        notes: list[Note] = []
        for path in sorted(self._dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            note = _read_note(path)
            if note is not None and note.type in self._allowed_types:
                notes.append(note)
        return notes

    def _refresh_index_locked(self) -> str:
        self.ensure_dir()
        rows: list[str] = []
        for note in self._list_notes_locked():
            summary = " ".join(note.content.strip().split())[:160]
            rows.append(f"- [{note.type.value}] {note.title} — {summary}")
        kept: list[str] = []
        size = 0
        for row in rows[:_INDEX_MAX_LINES]:
            row_size = len((row + "\n").encode("utf-8"))
            if size + row_size > _INDEX_MAX_BYTES:
                break
            kept.append(row)
            size += row_size
        content = "\n".join(kept) + ("\n" if kept else "")
        index_path = self._dir / "MEMORY.md"
        try:
            existing = index_path.read_text(encoding="utf-8")
        except OSError:
            existing = None
        if existing != content:
            _atomic_write(index_path, content)
        return content


def _read_note(path: Path | None) -> Note | None:
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    try:
        note_type = NoteType(str(meta.get("type", "")))
    except ValueError:
        return None
    title = meta.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    prefix = f"{note_type.value}_"
    if not _SAFE_FILENAME.fullmatch(path.name) or not path.name.startswith(prefix):
        return None
    created = _parse_datetime(meta.get("created"))
    updated = _parse_datetime(meta.get("updated"))
    return Note(
        type=note_type,
        title=title.strip(),
        slug=path.stem[len(prefix) :],
        content=text[end + 5 :].strip(),
        filename=path.name,
        created=created,
        updated=updated,
    )


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = datetime.fromtimestamp(0, UTC)
    else:
        parsed = datetime.fromtimestamp(0, UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as output:
            temp_name = output.name
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)

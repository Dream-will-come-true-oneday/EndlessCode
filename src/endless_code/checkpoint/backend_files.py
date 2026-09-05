"""非 git 项目的降级后端：把工作区现存文件按字节复制到会话目录。"""

import shutil
from collections.abc import Callable
from pathlib import Path

from endless_code.checkpoint.metadata import (
    CheckpointMeta,
    append_meta,
    list_meta,
    new_meta,
    next_index,
)

SKIP_DIRS = {".git", ".endless-code", "node_modules", "__pycache__", ".venv", "venv"}


def _iter_workspace_files(workspace: Path) -> list[Path]:
    files: list[Path] = []
    if not workspace.exists():
        return files
    stack = [workspace]
    while stack:
        current = stack.pop()
        for entry in sorted(current.iterdir()):
            if entry.is_dir():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
            elif entry.is_file():
                files.append(entry)
    files.sort()
    return files


class FilesBackend:
    """文件字节快照后端（非 git 项目降级）。"""

    def __init__(
        self,
        workspace: str,
        session_dir: str,
        should_skip: Callable[[Path], bool] | None = None,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        self._session_dir = Path(session_dir)
        self._should_skip = should_skip

    @property
    def kind(self) -> str:
        return "files"

    def _snapshot_root(self) -> Path:
        return self._session_dir / "checkpoints" / "files"

    def _relative(self, path: Path) -> str:
        return path.relative_to(self._workspace).as_posix()

    async def create(
        self, tool: str, target: str, conv_len: int
    ) -> CheckpointMeta | None:
        files = [
            f
            for f in _iter_workspace_files(self._workspace)
            if self._should_skip is None or not self._should_skip(f)
        ]
        index = next_index(self._session_dir)
        snapshot_dir = self._snapshot_root() / str(index)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        manifest: list[str] = []
        try:
            for file_path in files:
                rel = self._relative(file_path)
                dest = snapshot_dir / Path(rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, dest)
                manifest.append(rel)
            (snapshot_dir / ".manifest").write_text(
                "\n".join(manifest), encoding="utf-8"
            )
        except OSError:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            return None
        meta = new_meta(
            tool=tool,
            target=target,
            kind=self.kind,
            ref=str(index),
            conv_len=conv_len,
            index=index,
        )
        append_meta(self._session_dir, meta)
        return meta

    async def restore(self, meta: CheckpointMeta) -> tuple[list[str], list[str]]:
        """恢复到快照状态，返回 (覆盖的文件, 删除的多余文件)。"""
        snapshot_dir = self._snapshot_root() / meta.ref
        manifest_file = snapshot_dir / ".manifest"
        if not manifest_file.exists():
            return [], []
        kept = {
            line
            for line in manifest_file.read_text(encoding="utf-8").splitlines()
            if line
        }
        restored: list[str] = []
        for rel in sorted(kept):
            src = snapshot_dir / Path(rel)
            dest = self._workspace / Path(rel)
            if not src.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            restored.append(rel)
        deleted: list[str] = []
        for file_path in _iter_workspace_files(self._workspace):
            rel = self._relative(file_path)
            if rel not in kept:
                file_path.unlink()
                deleted.append(rel)
        return restored, deleted

    async def clear(self) -> None:
        shutil.rmtree(self._snapshot_root(), ignore_errors=True)

    def last_ref(self) -> str:
        items = list_meta(self._session_dir)
        return items[-1].ref if items else ""

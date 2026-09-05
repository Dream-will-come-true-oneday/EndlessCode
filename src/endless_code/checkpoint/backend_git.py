"""git 项目的 shadow checkpoint 后端：临时索引 + write-tree/commit-tree。

快照提交挂在专属 ref（refs/endless-code/<session_id>/head）下，
不出现在任何分支上；全程不触碰用户的工作区暂存状态。
"""

import asyncio
import os
from pathlib import Path

from endless_code.checkpoint.backend_files import _iter_workspace_files
from endless_code.checkpoint.metadata import (
    CheckpointMeta,
    append_meta,
    list_meta,
    new_meta,
    next_index,
)

GIT_TIMEOUT = 10.0
REF_TEMPLATE = "refs/endless-code/{session_id}/head"
_FALLBACK_IDENTITY = {
    "GIT_AUTHOR_NAME": "endless-code",
    "GIT_AUTHOR_EMAIL": "endless-code@local",
    "GIT_COMMITTER_NAME": "endless-code",
    "GIT_COMMITTER_EMAIL": "endless-code@local",
}


class GitBackend:
    """基于 shadow commit 的工作区快照后端。"""

    def __init__(self, workspace: str, session_dir: str, session_id: str) -> None:
        self._workspace = Path(workspace).resolve()
        self._session_dir = Path(session_dir)
        self._session_id = session_id
        self._index_file = self._session_dir / "tmp-index"
        # git 需要临时索引文件所在目录已存在；独立使用 Manager 时会话目录可能尚未创建
        self._session_dir.mkdir(parents=True, exist_ok=True)

    @property
    def kind(self) -> str:
        return "git"

    @property
    def ref_name(self) -> str:
        return REF_TEMPLATE.format(session_id=self._session_id)

    async def _git(self, *args: str) -> tuple[int, str]:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(self._index_file)
        for key, value in _FALLBACK_IDENTITY.items():
            env.setdefault(key, value)
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(self._workspace),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=GIT_TIMEOUT
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, "git timeout"
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            return proc.returncode or 1, err or out
        return 0, out

    async def create(
        self, tool: str, target: str, conv_len: int
    ) -> CheckpointMeta | None:
        code, _ = await self._git("add", "-A")
        if code != 0:
            return None
        code, tree = await self._git("write-tree")
        if code != 0 or not tree:
            return None

        metas = list_meta(self._session_dir)
        parent = metas[-1].ref if metas else ""
        if parent:
            code, parent_tree = await self._git("rev-parse", f"{parent}^{{tree}}")
            if code == 0 and parent_tree == tree:
                return metas[-1]  # 无变化，复用上一 checkpoint

        commit_args = ["commit-tree", tree, "-m", "endless-code checkpoint"]
        if parent:
            commit_args += ["-p", parent]
        code, sha = await self._git(*commit_args)
        if code != 0 or not sha:
            return None
        code, _ = await self._git("update-ref", self.ref_name, sha)
        if code != 0:
            return None

        meta = new_meta(
            tool=tool,
            target=target,
            kind=self.kind,
            ref=sha,
            conv_len=conv_len,
            index=next_index(self._session_dir),
        )
        append_meta(self._session_dir, meta)
        return meta

    async def restore(self, meta: CheckpointMeta) -> tuple[list[str], list[str]]:
        """恢复工作区到 shadow commit 状态，返回 (覆盖的文件, 删除的多余文件)。"""
        code, _ = await self._git("read-tree", meta.ref)
        if code != 0:
            return [], []
        code, _ = await self._git("checkout-index", "-a", "-f")
        if code != 0:
            return [], []
        code, ls = await self._git("ls-files")
        if code != 0:
            return [], []
        kept = {line for line in ls.splitlines() if line}

        restored = sorted(kept)
        candidates: list[Path] = []
        for file_path in _iter_workspace_files(self._workspace):
            rel = file_path.relative_to(self._workspace).as_posix()
            if rel not in kept:
                candidates.append(file_path)
        ignored = await self._filter_ignored(candidates)
        deleted: list[str] = []
        for file_path in candidates:
            if file_path in ignored:
                continue
            rel = file_path.relative_to(self._workspace).as_posix()
            try:
                file_path.unlink()
            except OSError:
                continue
            deleted.append(rel)
        return restored, deleted

    async def _filter_ignored(self, paths: list[Path]) -> set[Path]:
        """被 .gitignore 忽略的文件不删除（避免误删用户未跟踪文件）。"""
        if not paths:
            return set()
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(self._index_file)
        proc = await asyncio.create_subprocess_exec(
            "git",
            "check-ignore",
            "-z",
            "--stdin",
            cwd=str(self._workspace),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        input_data = "\x00".join(
            p.relative_to(self._workspace).as_posix() for p in paths
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(input_data.encode("utf-8")), timeout=GIT_TIMEOUT
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return set()
        if proc.returncode not in (0, 1):
            return set()
        ignored_rel = {
            line
            for line in stdout.decode("utf-8", errors="replace").split("\x00")
            if line
        }
        return {
            p for p in paths if p.relative_to(self._workspace).as_posix() in ignored_rel
        }

    async def clear(self) -> None:
        await self._git("update-ref", "-d", self.ref_name)

    def last_ref(self) -> str:
        items = list_meta(self._session_dir)
        return items[-1].ref if items else ""


def is_git_repo(workspace: str) -> bool:
    return (Path(workspace) / ".git").exists()

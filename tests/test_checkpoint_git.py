"""GitBackend shadow checkpoint 测试（临时仓库夹具）。"""

import asyncio
import subprocess
from pathlib import Path

import pytest

from endless_code.checkpoint.backend_git import GitBackend, is_git_repo
from endless_code.checkpoint.metadata import list_meta


def _run_git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture()
def git_env(tmp_path):
    workspace = tmp_path / "repo"
    session_dir = tmp_path / "session"
    workspace.mkdir()
    session_dir.mkdir()
    _run_git(workspace, "init")
    _run_git(workspace, "config", "user.email", "test@example.com")
    _run_git(workspace, "config", "user.name", "test")
    return workspace, session_dir


def _write(workspace: Path, rel: str, content: str) -> None:
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestGitBackend:
    async def test_create_does_not_pollute_branch(self, git_env):
        workspace, session_dir = git_env
        _write(workspace, "a.py", "v1\n")
        _run_git(workspace, "add", "a.py")
        _run_git(workspace, "commit", "-m", "init")
        before_log = _run_git(workspace, "log", "--oneline")
        before_status = _run_git(workspace, "status", "--porcelain")

        backend = GitBackend(str(workspace), str(session_dir), "s1")
        meta = await backend.create("write_file", "a.py", conv_len=2)

        assert meta is not None and meta.kind == "git"
        assert _run_git(workspace, "log", "--oneline") == before_log
        assert _run_git(workspace, "status", "--porcelain") == before_status
        assert _run_git(workspace, "rev-parse", backend.ref_name) == meta.ref
        assert len(list_meta(session_dir)) == 1

    async def test_no_change_reuses_last_checkpoint(self, git_env):
        workspace, session_dir = git_env
        _write(workspace, "a.py", "v1\n")
        backend = GitBackend(str(workspace), str(session_dir), "s1")
        m1 = await backend.create("t", "a.py", 0)
        m2 = await backend.create("t", "a.py", 0)
        assert m1 is not None and m2 is not None
        assert m1.ref == m2.ref
        assert m2.index == m1.index
        assert len(list_meta(session_dir)) == 1

    async def test_restore_roundtrip_including_deletions(self, git_env):
        workspace, session_dir = git_env
        _write(workspace, "a.py", "v1\n")
        _write(workspace, "sub/b.py", "keep\n")
        backend = GitBackend(str(workspace), str(session_dir), "s1")
        meta = await backend.create("t", "a.py", 0)
        assert meta is not None

        _write(workspace, "a.py", "v2 changed\n")
        _write(workspace, "new.py", "extra\n")
        (workspace / "sub" / "b.py").unlink()

        restored, deleted = await backend.restore(meta)
        assert "a.py" in restored and "sub/b.py" in restored
        assert "new.py" in deleted
        assert (workspace / "a.py").read_text(encoding="utf-8") == "v1\n"
        assert (workspace / "sub" / "b.py").read_text(encoding="utf-8") == "keep\n"
        assert not (workspace / "new.py").exists()

    async def test_chain_of_checkpoints(self, git_env):
        workspace, session_dir = git_env
        _write(workspace, "a.py", "v1\n")
        backend = GitBackend(str(workspace), str(session_dir), "s1")
        m1 = await backend.create("t", "a.py", 0)
        _write(workspace, "a.py", "v2\n")
        m2 = await backend.create("t", "a.py", 0)
        _write(workspace, "a.py", "v3\n")
        m3 = await backend.create("t", "a.py", 0)
        assert m1 and m2 and m3
        assert len({m1.ref, m2.ref, m3.ref}) == 3
        assert [m.index for m in (m1, m2, m3)] == [1, 2, 3]

        await backend.restore(m1)
        assert (workspace / "a.py").read_text(encoding="utf-8") == "v1\n"

    async def test_clear_removes_ref(self, git_env):
        workspace, session_dir = git_env
        _write(workspace, "a.py", "v1\n")
        backend = GitBackend(str(workspace), str(session_dir), "s1")
        await backend.create("t", "a.py", 0)
        await backend.clear()
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "rev-parse", "--verify", backend.ref_name],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0

    async def test_git_failure_returns_none(self, tmp_path):
        # 非 git 目录 → add 失败 → 返回 None 而不抛异常
        workspace = tmp_path / "plain"
        session_dir = tmp_path / "session"
        workspace.mkdir()
        session_dir.mkdir()
        (workspace / "a.py").write_text("x\n", encoding="utf-8")
        backend = GitBackend(str(workspace), str(session_dir), "s1")
        assert await backend.create("t", "a.py", 0) is None

    async def test_ignored_files_not_deleted_on_restore(self, git_env):
        workspace, session_dir = git_env
        (workspace / ".gitignore").write_text("user-data/\n", encoding="utf-8")
        _write(workspace, "user-data/keep.txt", "user file\n")
        _write(workspace, "a.py", "v1\n")
        backend = GitBackend(str(workspace), str(session_dir), "s1")
        meta = await backend.create("t", "a.py", 0)
        assert meta is not None
        _write(workspace, "ai-made.py", "temp\n")
        _restored, deleted = await backend.restore(meta)
        assert "ai-made.py" in deleted
        assert (workspace / "user-data" / "keep.txt").exists()


def test_is_git_repo(tmp_path):
    assert not is_git_repo(str(tmp_path))
    (tmp_path / ".git").mkdir()
    assert is_git_repo(str(tmp_path))

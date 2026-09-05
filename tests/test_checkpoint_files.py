"""FilesBackend 与元数据 JSONL 测试。"""

import json

import pytest

from endless_code.checkpoint.backend_files import FilesBackend
from endless_code.checkpoint.metadata import (
    CheckpointMeta,
    clear_meta,
    list_meta,
    metadata_path,
)


@pytest.fixture()
def env(tmp_path):
    workspace = tmp_path / "proj"
    session_dir = tmp_path / "session"
    workspace.mkdir()
    session_dir.mkdir()
    return workspace, session_dir


def _write(workspace, rel: str, content: str) -> None:
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestMetadata:
    def test_append_and_list_roundtrip(self, env):
        _workspace, session_dir = env
        meta = CheckpointMeta(
            index=1,
            timestamp=1700000000,
            tool="write_file",
            target="a.py",
            kind="files",
            ref="1",
            conv_len=4,
        )
        from endless_code.checkpoint.metadata import append_meta

        append_meta(session_dir, meta)
        items = list_meta(session_dir)
        assert len(items) == 1
        assert items[0] == meta

    def test_corrupted_lines_skipped(self, env):
        _workspace, session_dir = env
        path = metadata_path(session_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        good = json.dumps(
            {
                "index": 2,
                "timestamp": 1,
                "tool": "t",
                "target": "x",
                "kind": "files",
                "ref": "1",
                "conv_len": 0,
            }
        )
        path.write_text("not-json\n" + good + '\n{"index": "bad"}\n', encoding="utf-8")
        items = list_meta(session_dir)
        assert len(items) == 1
        assert items[0].index == 2

    def test_clear(self, env):
        _workspace, session_dir = env
        path = metadata_path(session_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        clear_meta(session_dir)
        assert list_meta(session_dir) == []


class TestFilesBackend:
    async def test_create_and_restore_roundtrip(self, env):
        workspace, session_dir = env
        _write(workspace, "a.py", "v1\n")
        _write(workspace, "sub/b.py", "keep\n")
        backend = FilesBackend(str(workspace), str(session_dir))
        meta = await backend.create("write_file", "a.py", conv_len=2)
        assert meta is not None
        assert meta.index == 1
        assert meta.kind == "files"

        # 快照后继续修改：改一个、新增一个、删除一个
        _write(workspace, "a.py", "v2 changed\n")
        _write(workspace, "new.py", "extra\n")
        (workspace / "sub" / "b.py").unlink()

        restored, deleted = await backend.restore(meta)
        assert "a.py" in restored and "sub/b.py" in restored
        assert "new.py" in deleted
        assert (workspace / "a.py").read_text(encoding="utf-8") == "v1\n"
        assert (workspace / "sub" / "b.py").read_text(encoding="utf-8") == "keep\n"
        assert not (workspace / "new.py").exists()

    async def test_index_increments(self, env):
        workspace, session_dir = env
        _write(workspace, "a.py", "1\n")
        backend = FilesBackend(str(workspace), str(session_dir))
        m1 = await backend.create("t", "a.py", 0)
        _write(workspace, "a.py", "2\n")
        m2 = await backend.create("t", "a.py", 0)
        assert (m1.index, m2.index) == (1, 2)
        assert len(list_meta(session_dir)) == 2

    async def test_skips_endless_code_and_git_dirs(self, env):
        workspace, session_dir = env
        _write(workspace, ".endless-code/inner/x.txt", "secret\n")
        _write(workspace, ".git/objects/ab", "obj\n")
        _write(workspace, "ok.py", "fine\n")
        backend = FilesBackend(str(workspace), str(session_dir))
        await backend.create("t", "ok.py", 0)
        snapshot = session_dir / "checkpoints" / "files" / "1"
        names = {
            p.relative_to(snapshot).as_posix()
            for p in snapshot.rglob("*")
            if p.is_file()
        }
        assert "ok.py" in names
        assert not any(n.startswith(".endless-code") for n in names)
        assert not any(n.startswith(".git") for n in names)

    async def test_binary_files_survive_roundtrip(self, env):
        workspace, session_dir = env
        blob = workspace / "img.bin"
        blob.write_bytes(bytes(range(256)))
        backend = FilesBackend(str(workspace), str(session_dir))
        meta = await backend.create("t", "img.bin", 0)
        blob.write_bytes(b"corrupted")
        await backend.restore(meta)
        assert blob.read_bytes() == bytes(range(256))

    async def test_clear_removes_snapshots(self, env):
        workspace, session_dir = env
        _write(workspace, "a.py", "x\n")
        backend = FilesBackend(str(workspace), str(session_dir))
        await backend.create("t", "a.py", 0)
        await backend.clear()
        assert not (session_dir / "checkpoints" / "files" / "1").exists()

    async def test_restore_missing_snapshot_is_noop(self, env):
        workspace, session_dir = env
        backend = FilesBackend(str(workspace), str(session_dir))
        meta = CheckpointMeta(1, 0, "t", "x", "files", "999", 0)
        assert await backend.restore(meta) == ([], [])

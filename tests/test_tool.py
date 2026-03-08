"""tool 包单测：注册中心 + 各工具。"""

import asyncio
import json
from pathlib import Path

import pytest

from endless_code.tool import Registry, Result, new_default_registry


@pytest.fixture
def registry() -> Registry:
    return new_default_registry()


class TestRegistry:
    def test_definitions_count(self, registry: Registry) -> None:
        defs = registry.definitions()
        assert len(defs) == 6

    def test_definitions_names(self, registry: Registry) -> None:
        names = [d.name for d in registry.definitions()]
        assert names == ["read_file", "write_file", "edit_file", "bash", "glob", "grep"]

    def test_get_hit(self, registry: Registry) -> None:
        assert registry.get("read_file") is not None

    def test_get_miss(self, registry: Registry) -> None:
        assert registry.get("no_such_tool") is None

    @pytest.mark.asyncio
    async def test_execute_unknown(self, registry: Registry) -> None:
        r = await registry.execute("no_such", "{}")
        assert r.is_error
        assert "未知工具" in r.content


class TestReadFile:
    @pytest.mark.asyncio
    async def test_read_existing(self, registry: Registry, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("line1\nline2\n")
        r = await registry.execute("read_file", json.dumps({"path": str(f)}))
        assert not r.is_error
        assert "1\tline1" in r.content
        assert "2\tline2" in r.content

    @pytest.mark.asyncio
    async def test_read_not_exist(self, registry: Registry) -> None:
        r = await registry.execute("read_file", json.dumps({"path": "/no/such/file.txt"}))
        assert r.is_error
        assert "不存在" in r.content


class TestWriteFile:
    @pytest.mark.asyncio
    async def test_write_nested(self, registry: Registry, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c.txt"
        r = await registry.execute(
            "write_file", json.dumps({"path": str(target), "content": "hello"})
        )
        assert not r.is_error
        assert target.read_text() == "hello"


class TestEditFile:
    @pytest.mark.asyncio
    async def test_edit_unique(self, registry: Registry, tmp_path: Path) -> None:
        f = tmp_path / "e.txt"
        f.write_text("aaa bbb ccc")
        r = await registry.execute(
            "edit_file", json.dumps({"path": str(f), "old_string": "bbb", "new_string": "XXX"})
        )
        assert not r.is_error
        assert f.read_text() == "aaa XXX ccc"

    @pytest.mark.asyncio
    async def test_edit_zero_match(self, registry: Registry, tmp_path: Path) -> None:
        f = tmp_path / "e2.txt"
        f.write_text("aaa bbb ccc")
        r = await registry.execute(
            "edit_file", json.dumps({"path": str(f), "old_string": "zzz", "new_string": "X"})
        )
        assert r.is_error
        assert "未找到" in r.content

    @pytest.mark.asyncio
    async def test_edit_multi_match(self, registry: Registry, tmp_path: Path) -> None:
        f = tmp_path / "e3.txt"
        f.write_text("aaa aaa aaa")
        r = await registry.execute(
            "edit_file", json.dumps({"path": str(f), "old_string": "aaa", "new_string": "X"})
        )
        assert r.is_error
        assert "3" in r.content
        assert "不唯一" in r.content


class TestBash:
    @pytest.mark.asyncio
    async def test_echo(self, registry: Registry) -> None:
        r = await registry.execute("bash", json.dumps({"command": "echo hi"}))
        assert not r.is_error
        assert "hi" in r.content
        assert "exit_code: 0" in r.content

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        reg = new_default_registry()
        r = await reg.execute("bash", json.dumps({"command": "sleep 10"}), timeout=0.5)
        assert r.is_error
        assert "超时" in r.content


class TestGlob:
    @pytest.mark.asyncio
    async def test_glob_py(self, registry: Registry) -> None:
        r = await registry.execute(
            "glob", json.dumps({"pattern": "**/*.py", "path": "src/endless_code"})
        )
        assert not r.is_error
        assert ".py" in r.content


class TestGrep:
    @pytest.mark.asyncio
    async def test_grep_keyword(self, registry: Registry) -> None:
        r = await registry.execute(
            "grep", json.dumps({"pattern": "class Registry", "path": "src/endless_code"})
        )
        assert not r.is_error
        assert "Registry" in r.content

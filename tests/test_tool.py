"""tool 包单测：注册中心 + 各工具。"""

import asyncio
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from endless_code.tool import Registry, Result, new_default_registry
from endless_code.tool.bash import BashTool


def _python_command(code: str) -> str:
    args = [sys.executable, "-c", code]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


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

    def test_read_only_definitions(self, registry: Registry) -> None:
        names = [d.name for d in registry.read_only_definitions()]
        assert names == ["read_file", "glob", "grep"]

    def test_read_only_lookup(self, registry: Registry) -> None:
        assert registry.is_read_only("read_file")
        assert not registry.is_read_only("write_file")
        assert not registry.is_read_only("no_such_tool")

    def test_get_hit(self, registry: Registry) -> None:
        assert registry.get("read_file") is not None

    def test_get_miss(self, registry: Registry) -> None:
        assert registry.get("no_such_tool") is None

    @pytest.mark.asyncio
    async def test_execute_unknown(self, registry: Registry) -> None:
        r = await registry.execute("no_such", "{}")
        assert r.is_error
        assert "未知工具" in r.content

    def test_deferred_metadata_preserves_registration_order(self) -> None:
        class MetadataTool:
            def __init__(self, name: str, read_only: bool) -> None:
                self._name = name
                self.read_only = read_only

            def name(self) -> str:
                return self._name

            def description(self) -> str:
                return f"description:{self._name}"

            def parameters(self) -> dict:
                return {"type": "object"}

            async def execute(self, args: str) -> Result:
                return Result(args)

        registry = Registry()
        registry.register(MetadataTool("local", True))
        registry.register(MetadataTool("mcp__demo__write", False), deferred=True)
        registry.register(MetadataTool("mcp__demo__read", True), deferred=True)

        assert registry.names() == [
            "local",
            "mcp__demo__write",
            "mcp__demo__read",
        ]
        assert registry.deferred_names() == [
            "mcp__demo__write",
            "mcp__demo__read",
        ]
        assert registry.deferred_names(read_only_only=True) == ["mcp__demo__read"]
        assert registry.is_deferred("mcp__demo__write")
        assert not registry.is_deferred("local")
        assert not registry.is_deferred("missing")
        assert registry.definition("mcp__demo__read").description == (
            "description:mcp__demo__read"
        )
        assert registry.definition("missing") is None
        assert [item.name for item in registry.definitions()] == registry.names()


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
        r = await registry.execute(
            "read_file", json.dumps({"path": "/no/such/file.txt"})
        )
        assert r.is_error
        assert "不存在" in r.content

    @pytest.mark.asyncio
    async def test_read_file_truncates_large_input(
        self, registry: Registry, tmp_path: Path
    ) -> None:
        target = tmp_path / "large.txt"
        target.write_text("\n".join(f"line-{index}" for index in range(2100)))
        result = await registry.execute("read_file", json.dumps({"path": str(target)}))
        assert not result.is_error
        assert "[truncated]" in result.content
        assert "line-1999" in result.content
        assert "line-2000" not in result.content


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
            "edit_file",
            json.dumps({"path": str(f), "old_string": "bbb", "new_string": "XXX"}),
        )
        assert not r.is_error
        assert f.read_text() == "aaa XXX ccc"

    @pytest.mark.asyncio
    async def test_edit_zero_match(self, registry: Registry, tmp_path: Path) -> None:
        f = tmp_path / "e2.txt"
        f.write_text("aaa bbb ccc")
        r = await registry.execute(
            "edit_file",
            json.dumps({"path": str(f), "old_string": "zzz", "new_string": "X"}),
        )
        assert r.is_error
        assert "未找到" in r.content

    @pytest.mark.asyncio
    async def test_edit_multi_match(self, registry: Registry, tmp_path: Path) -> None:
        f = tmp_path / "e3.txt"
        f.write_text("aaa aaa aaa")
        r = await registry.execute(
            "edit_file",
            json.dumps({"path": str(f), "old_string": "aaa", "new_string": "X"}),
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
    async def test_nonzero_exit(self, registry: Registry) -> None:
        command = _python_command("raise SystemExit(7)")
        r = await registry.execute("bash", json.dumps({"command": command}))
        assert r.is_error
        assert "exit_code: 7" in r.content

    @pytest.mark.asyncio
    async def test_timeout_stops_child(self, tmp_path: Path) -> None:
        marker = tmp_path / "late-marker.txt"
        code = (
            "import time; from pathlib import Path; "
            f"time.sleep(0.6); Path({str(marker)!r}).write_text('late', encoding='utf-8')"
        )
        command = _python_command(code)
        reg = new_default_registry()
        r = await reg.execute("bash", json.dumps({"command": command}), timeout=0.1)
        assert r.is_error
        assert "超时" in r.content
        await asyncio.sleep(0.8)
        assert not marker.exists()

    @pytest.mark.asyncio
    async def test_bash_truncates_long_output(self, registry: Registry) -> None:
        command = _python_command("print('x' * 31000)")
        result = await registry.execute("bash", json.dumps({"command": command}))
        assert not result.is_error
        assert len(result.content) <= 30020
        assert result.content.endswith("[truncated]")


class TestGlob:
    @pytest.mark.asyncio
    async def test_glob_py(self, registry: Registry) -> None:
        r = await registry.execute(
            "glob", json.dumps({"pattern": "**/*.py", "path": "src/endless_code"})
        )
        assert not r.is_error
        assert ".py" in r.content

    @pytest.mark.asyncio
    async def test_glob_limits_results(
        self, registry: Registry, tmp_path: Path
    ) -> None:
        for index in range(101):
            (tmp_path / f"{index:03}.txt").touch()
        result = await registry.execute(
            "glob", json.dumps({"pattern": "*.txt", "path": str(tmp_path)})
        )
        assert not result.is_error
        assert "[truncated:" in result.content
        assert len(result.content.splitlines()) == 101


class TestGrep:
    @pytest.mark.asyncio
    async def test_grep_keyword(self, registry: Registry) -> None:
        r = await registry.execute(
            "grep",
            json.dumps({"pattern": "class Registry", "path": "src/endless_code"}),
        )
        assert not r.is_error
        assert "Registry" in r.content

    @pytest.mark.asyncio
    async def test_grep_limits_results(
        self, registry: Registry, tmp_path: Path
    ) -> None:
        target = tmp_path / "hits.txt"
        target.write_text("\n".join(f"needle {index}" for index in range(101)))
        result = await registry.execute(
            "grep",
            json.dumps({"pattern": "needle", "path": str(tmp_path)}),
        )
        assert not result.is_error
        assert "[truncated:" in result.content
        assert len(result.content.splitlines()) == 101


def test_tool_descriptions_reinforce_conventions() -> None:
    from endless_code.tool.bash import BashTool
    from endless_code.tool.edit_file import EditFileTool

    assert "read_file" in EditFileTool().description()
    assert "专用工具" in BashTool().description()


class TestBashInputValidation:
    @pytest.mark.asyncio
    async def test_invalid_json(self) -> None:
        result = await BashTool().execute("not json")
        assert result.is_error
        assert "参数 JSON 解析失败" in result.content

    @pytest.mark.asyncio
    async def test_missing_command(self) -> None:
        for raw in ("{}", "", json.dumps({"command": ""})):
            result = await BashTool().execute(raw)
            assert result.is_error
            assert "缺少必填参数" in result.content

    @pytest.mark.asyncio
    async def test_non_string_command_returns_error(self) -> None:
        for raw in (json.dumps({"command": 123}), json.dumps({"command": True})):
            result = await BashTool().execute(raw)
            assert result.is_error
            assert "缺少必填参数" in result.content

    @pytest.mark.asyncio
    async def test_non_object_json_returns_error(self) -> None:
        for raw in ("123", "[1, 2]", '"str"', "null"):
            result = await BashTool().execute(raw)
            assert result.is_error
            assert "JSON 对象" in result.content

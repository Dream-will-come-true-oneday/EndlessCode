"""grep 工具：在文件内容中搜索正则。"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from endless_code.tool import Result


class GrepTool:
    read_only = True

    def name(self) -> str:
        return "grep"

    def description(self) -> str:
        return "用 Python 正则在文件内容中搜索，返回 file:line:content 列表。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python 正则表达式"},
                "path": {"type": "string", "description": "搜索根目录，默认当前目录"},
                "glob": {"type": "string", "description": "文件名过滤 glob（如 *.py）"},
            },
            "required": ["pattern"],
        }

    async def execute(self, args: str) -> Result:
        args = args.strip() or "{}"
        try:
            data = json.loads(args)
        except json.JSONDecodeError as e:
            return Result(content=f"参数 JSON 解析失败: {e}", is_error=True)

        pattern = data.get("pattern")
        if not pattern:
            return Result(content="缺少必填参数: pattern", is_error=True)

        try:
            rx = re.compile(pattern)
        except re.error as e:
            return Result(content=f"正则非法: {e}", is_error=True)

        root = Path(data.get("path") or ".")
        file_glob = data.get("glob") or "*"

        hits: list[str] = []
        for fp in root.rglob(file_glob):
            if not fp.is_file():
                continue
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if len(line) > 1_000_000:
                            continue
                        if rx.search(line):
                            hits.append(f"{fp}:{lineno}:{line.rstrip()}")
                            if len(hits) >= 100:
                                break
            except OSError:
                continue
            if len(hits) >= 100:
                break
            await asyncio.sleep(0)

        if not hits:
            return Result(content="无命中")

        result = "\n".join(hits)
        if len(hits) >= 100:
            result += "\n[truncated: 仅显示前 100 条]"
        return Result(content=result)

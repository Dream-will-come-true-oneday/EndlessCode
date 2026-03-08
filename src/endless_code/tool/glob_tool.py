"""glob 工具：按模式查找文件。"""

import asyncio
import json
from pathlib import Path
from typing import Any

from endless_code.tool import Result


class GlobTool:
    def name(self) -> str:
        return "glob"

    def description(self) -> str:
        return "按 glob 模式查找匹配的文件路径列表（如 **/*.py）。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式，如 **/*.py"},
                "path": {"type": "string", "description": "搜索根目录，默认当前目录"},
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

        root = Path(data.get("path") or ".")
        if not root.exists():
            return Result(content=f"路径不存在: {root}", is_error=True)

        matches: list[str] = []
        count = 0
        for p in root.glob(pattern):
            if p.is_file():
                matches.append(str(p))
                if len(matches) >= 100:
                    break
            count += 1
            if count % 100 == 0:
                await asyncio.sleep(0)

        if not matches:
            return Result(content="无匹配文件")

        result = "\n".join(sorted(matches))
        if len(matches) >= 100:
            result += "\n[truncated: 仅显示前 100 条]"
        return Result(content=result)

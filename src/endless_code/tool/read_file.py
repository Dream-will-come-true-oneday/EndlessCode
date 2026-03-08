"""read_file 工具：读取文件内容并带行号返回。"""

import json
from pathlib import Path
from typing import Any

from endless_code.tool import Result, _truncate


class ReadFileTool:
    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "读取指定路径的文件内容，返回带行号的文本。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要读取的文件路径"},
            },
            "required": ["path"],
        }

    async def execute(self, args: str) -> Result:
        args = args.strip() or "{}"
        try:
            data = json.loads(args)
        except json.JSONDecodeError as e:
            return Result(content=f"参数 JSON 解析失败: {e}", is_error=True)

        path_str = data.get("path")
        if not path_str:
            return Result(content="缺少必填参数: path", is_error=True)

        p = Path(path_str)
        if not p.exists():
            return Result(content=f"文件不存在: {path_str}", is_error=True)
        if p.is_dir():
            return Result(content=f"路径是目录而非文件: {path_str}", is_error=True)

        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return Result(content=f"读取失败: {e}", is_error=True)

        lines = text.splitlines()
        numbered = "\n".join(f"{i:6d}\t{line}" for i, line in enumerate(lines, 1))
        return Result(content=_truncate(numbered, max_lines=2000, max_chars=256_000))

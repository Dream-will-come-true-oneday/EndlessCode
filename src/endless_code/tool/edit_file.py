"""edit_file 工具：唯一匹配替换文件内容。"""

import json
from pathlib import Path
from typing import Any

from endless_code.tool import Result


class EditFileTool:
    def name(self) -> str:
        return "edit_file"

    def description(self) -> str:
        return (
            "对文件中的 old_string 做唯一匹配替换为 new_string。"
            "old_string 必须在文件中恰好出现一次，否则返回错误。"
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要编辑的文件路径"},
                "old_string": {"type": "string", "description": "要替换的原文片段（必须唯一匹配）"},
                "new_string": {"type": "string", "description": "替换后的新文本"},
            },
            "required": ["path", "old_string", "new_string"],
        }

    async def execute(self, args: str) -> Result:
        args = args.strip() or "{}"
        try:
            data = json.loads(args)
        except json.JSONDecodeError as e:
            return Result(content=f"参数 JSON 解析失败: {e}", is_error=True)

        path_str = data.get("path")
        old_string = data.get("old_string")
        new_string = data.get("new_string")

        if not path_str:
            return Result(content="缺少必填参数: path", is_error=True)
        if old_string is None:
            return Result(content="缺少必填参数: old_string", is_error=True)
        if new_string is None:
            return Result(content="缺少必填参数: new_string", is_error=True)

        p = Path(path_str)
        if not p.exists():
            return Result(content=f"文件不存在: {path_str}", is_error=True)

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return Result(content=f"读取失败: {e}", is_error=True)

        n = content.count(old_string)
        if n == 0:
            return Result(content="未找到匹配的内容", is_error=True)
        if n > 1:
            return Result(
                content=f"匹配到 {n} 处，old_string 不唯一，请提供更长上下文使其唯一",
                is_error=True,
            )

        new_content = content.replace(old_string, new_string, 1)
        try:
            p.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return Result(content=f"写入失败: {e}", is_error=True)

        return Result(content=f"已替换 {path_str} 中的内容")

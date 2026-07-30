"""write_file 工具：写入文件，父目录不存在时自动创建。"""

import json
from pathlib import Path
from typing import Any

from endless_code.tool import Result


class WriteFileTool:
    read_only = False

    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "将内容写入指定路径的文件（覆盖），父目录不存在时自动创建。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要写入的文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, args: str) -> Result:
        args = args.strip() or "{}"
        try:
            data = json.loads(args)
        except json.JSONDecodeError as e:
            return Result(content=f"参数 JSON 解析失败: {e}", is_error=True)

        path_str = data.get("path")
        content = data.get("content")
        if not path_str:
            return Result(content="缺少必填参数: path", is_error=True)
        if content is None:
            return Result(content="缺少必填参数: content", is_error=True)

        p = Path(path_str)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except OSError as e:
            return Result(content=f"写入失败: {e}", is_error=True)

        return Result(content=f"已写入 {path_str}（{len(content.encode('utf-8'))} 字节）")

"""bash 工具：执行 shell 命令。"""

import asyncio
import json
from typing import Any

from endless_code.tool import Result, _truncate


class BashTool:
    def name(self) -> str:
        return "bash"

    def description(self) -> str:
        return "在工作目录下执行 shell 命令，返回 stdout、stderr 与退出码。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
            },
            "required": ["command"],
        }

    async def execute(self, args: str) -> Result:
        args = args.strip() or "{}"
        try:
            data = json.loads(args)
        except json.JSONDecodeError as e:
            return Result(content=f"参数 JSON 解析失败: {e}", is_error=True)

        cmd = data.get("command")
        if not cmd:
            return Result(content="缺少必填参数: command", is_error=True)

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()

        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        output = f"exit_code: {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        return Result(content=_truncate(output, max_lines=10000, max_chars=30000))

"""bash 工具：执行 shell 命令。"""

import asyncio
import json
import os
import signal
import subprocess
from typing import Any

from endless_code.tool import Result, _truncate


class BashTool:
    read_only = False

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

        process_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_kwargs["start_new_session"] = True

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **process_kwargs,
        )
        try:
            stdout_b, stderr_b = await proc.communicate()
        except asyncio.CancelledError:
            await _terminate_process_tree(proc)
            raise

        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        output = f"exit_code: {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        return Result(
            content=_truncate(output, max_lines=10000, max_chars=30000),
            is_error=proc.returncode != 0,
        )


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """终止 shell 及其子进程并等待回收。"""
    if proc.returncode is not None:
        return

    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(proc.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.communicate()
        except (FileNotFoundError, OSError):
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=1.0)
        return
    except TimeoutError:
        pass

    if os.name == "nt":
        proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await proc.wait()

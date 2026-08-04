"""运行环境采集与安全渲染。"""

import asyncio
import datetime as dt
import os
import platform as platform_module
from dataclasses import dataclass


@dataclass(frozen=True)
class Environment:
    working_dir: str
    platform: str
    date: str
    git_status: str
    version: str
    model: str

    def render(self) -> str:
        """将非敏感运行环境渲染为动态系统段。"""
        lines = [
            "Runtime environment:",
            f"- Working directory: {self.working_dir}",
            f"- Platform: {self.platform}",
            f"- Date: {self.date}",
            f"- Application version: {self.version}",
            f"- Model: {self.model}",
        ]
        if self.git_status:
            lines.append(f"- Git status: {self.git_status}")
        return "\n".join(lines)


async def _git_status(cwd: str) -> str:
    """在有界时间内获取 Git 状态；失败时返回空字符串。"""
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--porcelain",
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=1.0)
    except (FileNotFoundError, OSError, TimeoutError):
        return ""

    if process.returncode != 0:
        return ""
    status = stdout.decode(errors="replace").strip()
    return status[:2_000]


async def gather_environment(version: str, model: str) -> Environment:
    """收集本回合动态环境，任何 Git 失败均可降级。"""
    cwd = os.getcwd()
    return Environment(
        working_dir=cwd,
        platform=platform_module.platform(),
        date=dt.datetime.now(dt.UTC).date().isoformat(),
        git_status=await _git_status(cwd),
        version=version,
        model=model,
    )

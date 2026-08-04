"""bash 工具：执行 shell 命令。"""

import asyncio
import json
import os
import signal
import subprocess
from typing import Any

from endless_code.tool import Result, _truncate

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _KERNEL32.CreateJobObjectW.restype = wintypes.HANDLE
    _KERNEL32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _KERNEL32.SetInformationJobObject.restype = wintypes.BOOL
    _KERNEL32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _KERNEL32.AssignProcessToJobObject.restype = wintypes.BOOL
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL

    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class _WindowsJob:
    """关闭句柄时终止其中全部进程的 Windows Job。"""

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def close(self) -> None:
        if self._handle is not None:
            _KERNEL32.CloseHandle(self._handle)
            self._handle = None


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
        windows_job = _create_windows_job(proc)
        try:
            stdout_b, stderr_b = await proc.communicate()
        except asyncio.CancelledError:
            await _terminate_process_tree(proc, windows_job)
            raise
        finally:
            if windows_job is not None:
                windows_job.close()

        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        output = f"exit_code: {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        return Result(
            content=_truncate(output, max_lines=10000, max_chars=30000),
            is_error=proc.returncode != 0,
        )


def _create_windows_job(proc: asyncio.subprocess.Process) -> _WindowsJob | None:
    if os.name != "nt":
        return None

    handle = _KERNEL32.CreateJobObjectW(None, None)
    if not handle:
        return None

    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    configured = _KERNEL32.SetInformationJobObject(
        handle,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    popen = proc._transport.get_extra_info("subprocess")
    process_handle = getattr(popen, "_handle", None)
    assigned = process_handle is not None and _KERNEL32.AssignProcessToJobObject(
        handle, int(process_handle)
    )
    if not configured or not assigned:
        _KERNEL32.CloseHandle(handle)
        return None
    return _WindowsJob(handle)


async def _terminate_process_tree(
    proc: asyncio.subprocess.Process,
    windows_job: _WindowsJob | None,
) -> None:
    """终止 shell 及其子进程并等待回收。"""
    if os.name == "nt":
        if windows_job is not None:
            windows_job.close()
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
            if proc.returncode is None:
                proc.kill()
    else:
        if proc.returncode is not None:
            return
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
        if proc.returncode is None:
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await proc.wait()

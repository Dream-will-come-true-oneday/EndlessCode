"""checkpoint 元数据：会话内 checkpoint 时间线的 JSONL 持久化。"""

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

METADATA_FILENAME = "metadata.jsonl"


@dataclass
class CheckpointMeta:
    """一条 checkpoint 的元数据。"""

    index: int  # 会话内序号，从 1 开始
    timestamp: int  # epoch 秒
    tool: str  # 触发工具名
    target: str  # 主要目标（路径或命令摘要）
    kind: str  # "git" | "files"
    ref: str  # git: shadow commit sha；files: 快照子目录名
    conv_len: int  # checkpoint 时的对话消息数（回滚对话用）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CheckpointMeta":
        return cls(
            index=int(data["index"]),
            timestamp=int(data["timestamp"]),
            tool=str(data.get("tool", "")),
            target=str(data.get("target", "")),
            kind=str(data.get("kind", "")),
            ref=str(data.get("ref", "")),
            conv_len=int(data.get("conv_len", 0)),
        )


def metadata_path(session_dir: str | Path) -> Path:
    return Path(session_dir) / "checkpoints" / METADATA_FILENAME


def next_index(session_dir: str | Path) -> int:
    items = list_meta(session_dir)
    return items[-1].index + 1 if items else 1


def append_meta(session_dir: str | Path, meta: CheckpointMeta) -> None:
    """崩溃安全追加一行元数据（单行 JSON，立即刷盘）。"""
    path = metadata_path(session_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(meta.to_dict(), ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()


def list_meta(session_dir: str | Path) -> list[CheckpointMeta]:
    """按序号升序列出全部元数据；损坏行跳过。"""
    path = metadata_path(session_dir)
    if not path.exists():
        return []
    items: list[CheckpointMeta] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(CheckpointMeta.from_dict(json.loads(line)))
        except (ValueError, KeyError, TypeError):
            continue
    items.sort(key=lambda m: m.index)
    return items


def clear_meta(session_dir: str | Path) -> None:
    path = metadata_path(session_dir)
    if path.exists():
        path.write_text("", encoding="utf-8")


def new_meta(
    tool: str, target: str, kind: str, ref: str, conv_len: int, index: int
) -> CheckpointMeta:
    return CheckpointMeta(
        index=index,
        timestamp=int(time.time()),
        tool=tool,
        target=target,
        kind=kind,
        ref=ref,
        conv_len=conv_len,
    )

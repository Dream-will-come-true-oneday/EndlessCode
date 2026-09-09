"""滚动摘要状态：上一版摘要、覆盖点与跨会话持久化。"""

import json
import os
from dataclasses import asdict, dataclass

STATE_FILENAME = "summary-state.json"
_STATE_VERSION = 1


@dataclass
class SummaryState:
    """自动压缩的滚动摘要状态。

    ``covered_messages`` 是当前历史开头已被摘要覆盖的前导消息数（摘要消息与
    固定占位 assistant 消息）；之后的片段才是下次滚动要摘的新内容。
    """

    summary_text: str = ""
    covered_messages: int = 0
    revision: int = 0

    def usable_for_rolling(self, history_len: int) -> bool:
        """有上一版正文且覆盖点落在历史中间时，才能走增量而非全量。"""
        return bool(self.summary_text) and 0 < self.covered_messages < history_len

    def rolling_update(self, summary_text: str, covered: int) -> None:
        self.summary_text = summary_text
        self.covered_messages = max(0, covered)
        self.revision += 1

    def snapshot(self) -> tuple[str, int, int]:
        """当前三元内容的副本，供质量门拒结果时回退。"""
        return (self.summary_text, self.covered_messages, self.revision)

    def rollback(self, snapshot: tuple[str, int, int]) -> None:
        """回退到快照：历史没被替换时，覆盖点不能留在指向新内容的位置。"""
        self.summary_text, self.covered_messages, self.revision = snapshot

    def save(self, session_dir: str) -> None:
        """原子写入会话目录；写入失败静默忽略，状态只影响下一次压缩策略。"""
        payload = {"version": _STATE_VERSION, **asdict(self)}
        target = os.path.join(session_dir, STATE_FILENAME)
        tmp = target + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False)
            os.replace(tmp, target)
        except OSError:
            return

    @classmethod
    def load(cls, session_dir: str) -> "SummaryState":
        """从会话目录加载；缺失、损坏或版本不符一律回退全新状态（不抛出）。"""
        path = os.path.join(session_dir, STATE_FILENAME)
        try:
            with open(path, encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError, ValueError):
            return cls()
        if not isinstance(payload, dict) or payload.get("version") != _STATE_VERSION:
            return cls()
        text = payload.get("summary_text")
        covered = payload.get("covered_messages")
        revision = payload.get("revision")
        if not isinstance(text, str) or not isinstance(covered, int):
            return cls()
        if not isinstance(revision, int) or isinstance(revision, bool):
            return cls()
        return cls(
            summary_text=text,
            covered_messages=max(0, covered),
            revision=revision,
        )

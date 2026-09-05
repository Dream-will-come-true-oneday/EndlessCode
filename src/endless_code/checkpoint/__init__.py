"""checkpoint 门面：统一两套后端，负责元数据、审计与恢复范围语义。"""

import logging
from dataclasses import dataclass, field
from enum import Enum

from endless_code.checkpoint.backend_files import FilesBackend
from endless_code.checkpoint.backend_git import GitBackend, is_git_repo
from endless_code.checkpoint.metadata import (
    CheckpointMeta,
    clear_meta,
    list_meta,
)

logger = logging.getLogger(__name__)


class RestoreScope(Enum):
    """回滚范围：仅文件 / 仅对话 / 两者。"""

    FILES_ONLY = "files_only"
    CONVERSATION_ONLY = "conversation_only"
    BOTH = "both"


@dataclass
class RestoreReport:
    meta: CheckpointMeta | None = None
    restored_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    ok: bool = True
    error: str = ""


class CheckpointManager:
    """会话内 checkpoint 的创建、列出、恢复与清理。"""

    def __init__(
        self,
        workspace: str,
        session_dir: str,
        session_id: str,
        audit: object | None = None,
    ) -> None:
        self._workspace = workspace
        self._session_dir = session_dir
        self._session_id = session_id
        self._audit = audit
        if is_git_repo(workspace):
            self._backend = GitBackend(workspace, session_dir, session_id)
        else:
            self._backend = FilesBackend(workspace, session_dir)

    @property
    def backend_kind(self) -> str:
        return self._backend.kind

    def available(self) -> bool:
        """功能可用性：任何项目都有对应后端，恒为 True。"""
        return True

    def mode_label(self) -> str:
        return "git shadow" if self._backend.kind == "git" else "file snapshot"

    async def create(
        self, tool: str, target: str, conv_len: int
    ) -> CheckpointMeta | None:
        try:
            meta = await self._backend.create(tool, target, conv_len)
        except Exception as exc:
            logger.warning("checkpoint 创建失败: %s", exc, exc_info=True)
            meta = None
        if meta is None:
            self._audit_event(
                "checkpoint_skipped",
                tool=tool,
                target=target,
                reason="backend failed or timed out",
            )
        return meta

    def list(self) -> list[CheckpointMeta]:
        return list_meta(self._session_dir)

    async def restore(self, meta: CheckpointMeta, scope: RestoreScope) -> RestoreReport:
        if scope is RestoreScope.CONVERSATION_ONLY:
            self._audit_event(
                "checkpoint_restored",
                tool="checkpoint",
                target=f"#{meta.index}",
                result_summary=f"scope={scope.value} (对话回滚由会话层执行)",
            )
            return RestoreReport(meta=meta)

        try:
            restored, deleted = await self._backend.restore(meta)
        except Exception as exc:
            logger.warning("checkpoint 恢复失败: %s", exc, exc_info=True)
            self._audit_event(
                "checkpoint_restored",
                tool="checkpoint",
                target=f"#{meta.index}",
                result_summary=f"scope={scope.value} error={exc}",
                is_error=True,
            )
            return RestoreReport(meta=meta, ok=False, error=str(exc))
        self._audit_event(
            "checkpoint_restored",
            tool="checkpoint",
            target=f"#{meta.index}",
            result_summary=(
                f"scope={scope.value} restored={len(restored)} deleted={len(deleted)}"
            ),
        )
        return RestoreReport(meta=meta, restored_files=restored, deleted_files=deleted)

    async def clear(self) -> None:
        try:
            await self._backend.clear()
        except Exception as exc:
            logger.warning("checkpoint 清理失败: %s", exc, exc_info=True)
        clear_meta(self._session_dir)

    def _audit_event(self, event: str, **kwargs: object) -> None:
        writer = self._audit
        if writer is None:
            return
        try:
            writer.record(event, **kwargs)
        except Exception:
            logger.warning("checkpoint 审计写入失败: %s", event, exc_info=True)

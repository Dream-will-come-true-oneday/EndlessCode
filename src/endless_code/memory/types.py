"""记忆文件与 LLM 更新操作的数据类型。"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class NoteType(str, Enum):
    USER_PREFERENCE = "user_preference"
    CORRECTION_FEEDBACK = "correction_feedback"
    PROJECT_KNOWLEDGE = "project_knowledge"
    REFERENCE_MATERIAL = "reference_material"


@dataclass(frozen=True)
class UpdateAction:
    action: str
    type: str = ""
    title: str = ""
    slug: str = ""
    content: str = ""
    filename: str = ""


@dataclass(frozen=True)
class Note:
    type: NoteType
    title: str
    slug: str
    content: str
    filename: str
    created: datetime
    updated: datetime


@dataclass(frozen=True)
class ScopeOverview:
    scope: str
    directory: Path
    total_bytes: int
    counts: dict[str, int]
    notes: tuple[Note, ...]


@dataclass(frozen=True)
class MemoryOverview:
    user: ScopeOverview
    project: ScopeOverview

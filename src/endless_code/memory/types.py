"""记忆文件与 LLM 更新操作的数据类型。"""

from dataclasses import dataclass
from enum import Enum


class NoteType(str, Enum):
    USER_PREFERENCE = "user_preference"
    CORRECTION_FEEDBACK = "correction_feedback"
    PROJECT_KNOWLEDGE = "project_knowledge"
    REFERENCE_MATERIAL = "reference_material"


@dataclass(frozen=True)
class UpdateAction:
    action: str
    level: str
    type: str = ""
    title: str = ""
    slug: str = ""
    content: str = ""
    filename: str = ""

import asyncio
from pathlib import Path

import pytest

from endless_code.agent import Agent, new_session_runtime
from endless_code.conversation import Conversation
from endless_code.llm import Message, StreamEvent
from endless_code.memory import Manager, NoteType, Store, UpdateAction
from endless_code.tool import Registry


class MemoryProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, response: str) -> None:
        self.response = response
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield StreamEvent(text=self.response)
        yield StreamEvent(done=True)


def _create(note_type: NoteType, title: str, slug: str, content: str) -> UpdateAction:
    return UpdateAction(
        action="create",
        type=note_type.value,
        title=title,
        slug=slug,
        content=content,
    )


def test_store_creates_updates_deletes_and_refreshes_index(tmp_path: Path) -> None:
    store = Store(str(tmp_path), frozenset({NoteType.PROJECT_KNOWLEDGE}))
    store.apply(
        [
            _create(
                NoteType.PROJECT_KNOWLEDGE, "API 规则", "api_v2_rules", "使用 JSON"
            ),
            _create(NoteType.USER_PREFERENCE, "错误层级", "wrong_scope", "不应创建"),
        ]
    )
    filename = "project_knowledge_api_v2_rules.md"
    assert (tmp_path / filename).is_file()
    assert not (tmp_path / "user_preference_wrong_scope.md").exists()
    assert "API 规则" in (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    store.apply(
        [
            _create(
                NoteType.PROJECT_KNOWLEDGE,
                "重复创建",
                "api_v2_rules",
                "不得覆盖原文",
            )
        ]
    )
    assert "使用 JSON" in (tmp_path / filename).read_text(encoding="utf-8")
    assert "不得覆盖原文" not in (tmp_path / filename).read_text(encoding="utf-8")

    store.apply(
        [
            UpdateAction(
                action="update",
                type=NoteType.PROJECT_KNOWLEDGE.value,
                title="新规则",
                content="使用 YAML",
                filename=filename,
            )
        ]
    )
    assert "使用 YAML" in (tmp_path / filename).read_text(encoding="utf-8")
    path = tmp_path / filename
    path.write_text(
        path.read_text(encoding="utf-8").replace("新规则", "手工修改规则"),
        encoding="utf-8",
    )
    assert "手工修改规则" in store.load_index()

    store.apply(
        [
            UpdateAction(
                action="delete",
                type=NoteType.USER_PREFERENCE.value,
                filename=filename,
            )
        ]
    )
    assert path.exists()
    store.apply(
        [
            UpdateAction(
                action="delete",
                type=NoteType.PROJECT_KNOWLEDGE.value,
                filename=filename,
            )
        ]
    )
    assert not (tmp_path / filename).exists()


@pytest.mark.asyncio
async def test_manager_uses_tool_free_request_and_updates_index(tmp_path: Path) -> None:
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))
    provider = MemoryProvider(
        '{"user_preference":[{"action":"create","title":"中文","slug":"simplified_chinese","content":"始终使用简体中文"}],'
        '"correction_feedback":[],"project_knowledge":[],"reference_material":[]}'
    )
    manager.set_provider(provider, provider.model)

    await manager.update_async([Message(role="user", content="记住，用中文")])
    assert provider.requests[0].tools == []
    assert "existing.md" in provider.requests[0].messages[0].content
    assert '"level":' not in provider.requests[0].messages[0].content
    assert "中文" in manager.load_index()
    assert (tmp_path / "user" / "user_preference_simplified_chinese.md").is_file()
    assert not (tmp_path / "project" / "user_preference_simplified_chinese.md").exists()


def test_manager_recall_is_bilingual_bounded_and_deterministic(tmp_path: Path) -> None:
    project = Store(
        str(tmp_path / "project"),
        frozenset({NoteType.PROJECT_KNOWLEDGE, NoteType.REFERENCE_MATERIAL}),
    )
    user = Store(
        str(tmp_path / "user"),
        frozenset({NoteType.USER_PREFERENCE, NoteType.CORRECTION_FEEDBACK}),
    )
    project.apply(
        [
            _create(
                NoteType.PROJECT_KNOWLEDGE,
                "PostgreSQL migration 命名",
                "migration_naming",
                "迁移文件使用 timestamp_description 格式。",
            ),
            _create(
                NoteType.REFERENCE_MATERIAL,
                "部署链接",
                "deployment_link",
                "部署文档位于 internal.example。",
            ),
        ]
    )
    user.apply(
        [
            _create(
                NoteType.CORRECTION_FEEDBACK,
                "不要猜测 migration 名称",
                "verify_migration_name",
                "生成 migration 前先检查项目命名规范。",
            )
        ]
    )
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))

    first = manager.recall("请按 PostgreSQL migration 命名规范创建文件")
    second = manager.recall("请按 PostgreSQL migration 命名规范创建文件")
    assert first == second
    assert "migration_naming.md" in first
    assert "verify_migration_name.md" in first
    assert "deployment_link.md" not in first
    assert len(first.encode("utf-8")) <= 8192
    assert manager.recall("完全无关的量子问题") == ""

    project.apply(
        [
            _create(
                NoteType.REFERENCE_MATERIAL,
                f"sharedterm reference {index}",
                f"sharedterm_{index}",
                f"sharedterm content {index}",
            )
            for index in range(8)
        ]
    )
    limited = manager.recall("sharedterm")
    assert limited.count("### [") == 6
    assert len(limited.encode("utf-8")) <= 8192


def test_manager_overview_and_clear_preserve_unknown_files(tmp_path: Path) -> None:
    project = Store(str(tmp_path / "project"), frozenset({NoteType.PROJECT_KNOWLEDGE}))
    user = Store(str(tmp_path / "user"), frozenset({NoteType.USER_PREFERENCE}))
    project.apply(
        [_create(NoteType.PROJECT_KNOWLEDGE, "技术栈", "stack", "Python 3.12")]
    )
    user.apply([_create(NoteType.USER_PREFERENCE, "语言", "language", "使用中文")])
    unknown = tmp_path / "user" / "notes.md"
    unknown.write_text("keep", encoding="utf-8")
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))

    overview = manager.overview()
    assert overview.user.counts == {"user_preference": 1}
    assert overview.project.counts == {"project_knowledge": 1}
    assert overview.user.total_bytes > 0
    assert manager.clear("user") == 1
    assert unknown.read_text(encoding="utf-8") == "keep"
    assert manager.overview().project.notes


class BlockingMemoryProvider(MemoryProvider):
    def __init__(self) -> None:
        super().__init__("{}")
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, request):
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        yield StreamEvent(
            text=(
                '{"user_preference":[{"action":"create","title":"late",'
                '"slug":"late_write","content":"must not return"}],'
                '"correction_feedback":[],"project_knowledge":[],'
                '"reference_material":[]}'
            )
        )
        yield StreamEvent(done=True)


@pytest.mark.asyncio
async def test_manager_schedule_is_nonblocking_and_clear_cancels_pending(
    tmp_path: Path,
) -> None:
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))
    provider = BlockingMemoryProvider()
    manager.set_provider(provider, provider.model)

    manager.schedule_update([Message(role="user", content="preference")])
    await asyncio.wait_for(provider.started.wait(), timeout=1)
    replacement = MemoryProvider("{}")
    manager.set_provider(replacement, replacement.model)
    assert manager.clear("all") == 0
    provider.release.set()
    await manager.drain(timeout=1)
    assert len(provider.requests) == 1
    assert replacement.requests == []
    assert not manager.overview().user.notes


class EndToEndMemoryProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.main_requests = []

    async def stream(self, request):
        if "You update durable user and project memory" in request.system.stable:
            yield StreamEvent(
                text=(
                    '{"user_preference":[],"correction_feedback":['
                    '{"action":"create","title":"先检查 migration 命名",'
                    '"slug":"verify_migration_naming","content":'
                    '"创建 migration 前必须先检查项目现有命名规范。"}],'
                    '"project_knowledge":[],"reference_material":[]}'
                )
            )
        else:
            self.main_requests.append(request)
            yield StreamEvent(text="done")
        yield StreamEvent(done=True)


async def _run_agent(agent: Agent, conv: Conversation) -> None:
    async for _event in agent.run(conv):
        pass


@pytest.mark.asyncio
async def test_cross_session_correction_is_inherited_and_recalled(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    user_dir = tmp_path / "user"
    first_provider = EndToEndMemoryProvider()
    first_manager = Manager(str(project_dir), str(user_dir))
    first_manager.set_provider(first_provider, first_provider.model)
    first_conv = Conversation()
    first_conv.add_user("你刚才猜错了 migration 名称，以后先检查命名规范")
    await _run_agent(
        Agent(
            first_provider,
            Registry(),
            runtime=new_session_runtime(str(tmp_path)),
            memory_manager=first_manager,
        ),
        first_conv,
    )
    await first_manager.drain(timeout=1)
    note_path = user_dir / "correction_feedback_verify_migration_naming.md"
    assert note_path.is_file()

    second_provider = EndToEndMemoryProvider()
    second_manager = Manager(str(project_dir), str(user_dir))
    second_manager.set_provider(second_provider, second_provider.model)
    second_conv = Conversation()
    second_conv.add_user("再创建一个 migration，遵循现有命名规范")
    await _run_agent(
        Agent(
            second_provider,
            Registry(),
            runtime=new_session_runtime(str(tmp_path)),
            memory_manager=second_manager,
        ),
        second_conv,
    )

    request = second_provider.main_requests[0]
    assert "先检查 migration 命名" in request.messages[0].content
    assert "verify_migration_naming" in request.reminder
    await second_manager.drain(timeout=1)

from pathlib import Path

import pytest

from endless_code.llm import StreamEvent
from endless_code.memory import Manager, Store, UpdateAction, has_memory_signal


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


def test_store_creates_updates_and_deletes_notes(tmp_path: Path) -> None:
    store = Store(str(tmp_path))
    store.apply(
        [
            UpdateAction(
                "create",
                "project",
                "project_knowledge",
                "API 规则",
                "api_rules",
                "使用 JSON",
            )
        ]
    )
    filename = "project_knowledge_api_rules.md"
    assert (tmp_path / filename).is_file()
    assert "API 规则" in (tmp_path / "MEMORY.md").read_text(encoding="utf-8")

    store.apply(
        [
            UpdateAction(
                "update",
                "project",
                title="新规则",
                content="使用 YAML",
                filename=filename,
            )
        ]
    )
    assert "使用 YAML" in (tmp_path / filename).read_text(encoding="utf-8")
    store.apply([UpdateAction("delete", "project", filename=filename)])
    assert not (tmp_path / filename).exists()


@pytest.mark.asyncio
async def test_manager_uses_tool_free_request_and_updates_index(tmp_path: Path) -> None:
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))
    provider = MemoryProvider(
        '[{"action":"create","level":"user","type":"user_preference","title":"中文","slug":"simplified_chinese","content":"始终使用简体中文"}]'
    )
    manager.set_provider(provider, provider.model)
    from endless_code.llm import Message

    await manager.update_async([Message(role="user", content="记住，用中文")])
    assert provider.requests[0].tools == []
    assert "中文" in manager.load_index()
    assert has_memory_signal([Message(role="user", content="Please remember this")])

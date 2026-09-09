"""TUI 评测辅助：构造 App 上下文与状态等待。"""

import asyncio

from endless_code.config import ProviderConfig
from endless_code.permission import Mode, new_engine
from endless_code.tui.app import EndlessCodeApp, SessionState

TEST_SECRET = "sk-eval-secret-123456789"


def make_config(*, api_key: str = TEST_SECRET) -> ProviderConfig:
    return ProviderConfig(
        name="fake",
        protocol="openai",
        api_key=api_key,
        model="fake-model",
    )


def make_engine(root: str, *, start_mode: Mode = Mode.BYPASS) -> object:
    engine, _ = new_engine(root)
    engine._start_mode = start_mode
    return engine


def chat_text(app: EndlessCodeApp) -> str:
    return "\n".join(line.text for line in app._chat.lines)


async def wait_for_state(app, pilot, state: SessionState, attempts: int = 100) -> None:
    for _ in range(attempts):
        await pilot.pause()
        if app.state is state:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"TUI did not reach {state}")

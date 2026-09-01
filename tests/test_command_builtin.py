"""分发器与内置命令测试：FakeHost 全程不导入渲染框架。"""

import pytest

from endless_code.command.builtin import register_builtin_commands
from endless_code.command.dispatcher import Dispatcher
from endless_code.command.registry import Registry
from endless_code.command.types import CommandKind, CommandSpec, SessionInfo
from endless_code.permission import Mode


class FakeHost:
    """实现 CommandHost 全部 16 个方法，记录所有调用。"""

    def __init__(self) -> None:
        self.notices: list[str] = []
        self.errors: list[str] = []
        self.sent_messages: list[str] = []
        self.quit_called = False
        self.mode = Mode.DEFAULT
        self.set_mode_calls: list[Mode] = []
        self.session_info = SessionInfo(
            version="0.1.0",
            provider="fake",
            model="fake-model",
            mode="DEFAULT",
            tokens_in=11,
            tokens_out=22,
            session_id="sess-1",
            message_count=3,
        )
        self.memory_index_value = ""
        self.last_error_value = ""
        self.last_reply_value = ""
        self.audit_called = False
        self.compact_called = False
        self.resume_called = False
        self.plan_called = False
        self.execute_called = False
        self.clear_called = False

    def show_notice(self, text: str) -> None:
        self.notices.append(text)

    def show_error(self, text: str) -> None:
        self.errors.append(text)

    def send_user_message(self, text: str) -> None:
        self.sent_messages.append(text)

    def get_last_error(self) -> str:
        return self.last_error_value

    def get_last_reply(self) -> str:
        return self.last_reply_value

    def get_mode(self) -> Mode:
        return self.mode

    def set_mode(self, mode: Mode) -> None:
        self.mode = mode
        self.set_mode_calls.append(mode)

    def get_session_info(self) -> SessionInfo:
        return self.session_info

    def get_memory_index(self) -> str:
        return self.memory_index_value

    def show_audit(self) -> None:
        self.audit_called = True

    def start_compact(self) -> None:
        self.compact_called = True

    def start_resume(self) -> None:
        self.resume_called = True

    def enter_plan(self) -> None:
        self.plan_called = True

    def start_execute(self) -> None:
        self.execute_called = True

    def clear_session(self) -> None:
        self.clear_called = True

    def quit_app(self) -> None:
        self.quit_called = True


def new_dispatcher() -> tuple[Dispatcher, FakeHost]:
    registry = Registry()
    register_builtin_commands(registry)
    host = FakeHost()
    return Dispatcher(registry, host), host


def test_plain_text_is_not_consumed() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("普通消息") is False
    assert host.notices == [] and host.errors == []


def test_bare_slash_is_illegal_input() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/") is True
    assert host.errors and host.sent_messages == []


def test_unknown_command_guides_to_help() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/nomatch") is True
    assert any("/help" in text for text in host.notices)


def test_help_is_case_insensitive_and_lists_all_visible() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/HELP") is True
    joined = "\n".join(host.notices)
    assert "/exit" in joined and "/review" in joined


def test_help_with_arg_shows_details() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/help /permissions") is True
    assert any("用法：/permissions [模式名]" in text for text in host.notices)


def test_help_unknown_arg_notices() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/help /nope") is True
    assert any("未知命令" in text for text in host.notices)


def test_exit_and_quit_alias() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/exit") is True
    assert host.quit_called
    dispatcher2, host2 = new_dispatcher()
    assert dispatcher2.try_dispatch("/quit") is True
    assert host2.quit_called


def test_compact_resume_audit_delegate() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/compact") is True
    assert host.compact_called
    assert dispatcher.try_dispatch("/resume") is True
    assert host.resume_called
    assert dispatcher.try_dispatch("/audit") is True
    assert host.audit_called


def test_plan_and_do_delegate() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/plan") is True
    assert host.plan_called
    assert dispatcher.try_dispatch("/do") is True
    assert host.execute_called


def test_clear_requires_confirmation_then_clears() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/clear") is True
    assert not host.clear_called
    assert any("/clear yes" in text for text in host.notices)
    assert dispatcher.try_dispatch("/clear yes") is True
    assert host.clear_called


def test_permissions_without_args_shows_current_mode() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/permissions") is True
    assert any(
        "default" in text and "bypassPermissions" in text for text in host.notices
    )


def test_permissions_switches_mode() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/permissions plan") is True
    assert host.set_mode_calls == [Mode.PLAN]
    assert any("plan" in text for text in host.notices)


def test_permissions_rejects_unknown_mode() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/permissions nope") is True
    assert host.set_mode_calls == []
    assert any("default" in text for text in host.errors + host.notices)


def test_memory_with_and_without_index() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/memory") is True
    assert any("未配置" in text for text in host.notices)
    host.memory_index_value = "## project_info\n- 记住 X"
    assert dispatcher.try_dispatch("/mem") is True
    assert any("记住 X" in text for text in host.notices)


def test_status_outputs_all_fields() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/status") is True
    joined = "\n".join(host.notices)
    for expected in ("0.1.0", "fake", "fake-model", "sess-1", "3"):
        assert expected in joined


def test_review_sends_prompt_message() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/review 重点看并发") is True
    assert len(host.sent_messages) == 1
    assert "审查" in host.sent_messages[0]
    assert "重点看并发" in host.sent_messages[0]


def test_fix_without_error_notices() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/fix") is True
    assert host.sent_messages == []
    assert any("没有可修复" in text for text in host.notices)


def test_fix_with_error_sends_message() -> None:
    dispatcher, host = new_dispatcher()
    host.last_error_value = "TypeError: boom"
    assert dispatcher.try_dispatch("/fix 补充") is True
    assert len(host.sent_messages) == 1
    assert "TypeError: boom" in host.sent_messages[0]
    assert "补充" in host.sent_messages[0]


def test_explain_without_reply_notices() -> None:
    dispatcher, host = new_dispatcher()
    assert dispatcher.try_dispatch("/explain") is True
    assert host.sent_messages == []
    assert any("没有可解释" in text for text in host.notices)


def test_explain_with_reply_sends_message() -> None:
    dispatcher, host = new_dispatcher()
    host.last_reply_value = "这是上一轮回复内容"
    assert dispatcher.try_dispatch("/explain") is True
    assert len(host.sent_messages) == 1
    assert "这是上一轮回复内容" in host.sent_messages[0]


def test_handler_exception_is_contained() -> None:
    registry = Registry()
    host = FakeHost()

    def boom(host_, args: str) -> None:
        raise RuntimeError("炸了")

    registry.register(
        CommandSpec(
            name="/boom",
            description="",
            usage="/boom",
            kind=CommandKind.LOCAL,
            handler=boom,
        )
    )
    dispatcher = Dispatcher(registry, host)
    assert dispatcher.try_dispatch("/boom") is True
    assert any("RuntimeError" in text for text in host.errors)
    # 异常被兜住后，后续命令仍可正常分发
    register_builtin_commands(registry)
    assert dispatcher.try_dispatch("/status") is True
    assert host.notices


def test_complete_unique_match_appends_space() -> None:
    dispatcher, _ = new_dispatcher()
    text, candidates = dispatcher.complete("/stat")
    assert text == "/status "
    assert candidates == []


def test_complete_multiple_matches_returns_candidates() -> None:
    dispatcher, _ = new_dispatcher()
    text, candidates = dispatcher.complete("/pe")
    assert text == "/pe"
    assert candidates == ["/perm", "/permissions"]


def test_complete_no_match_or_args_keeps_text() -> None:
    dispatcher, _ = new_dispatcher()
    assert dispatcher.complete("/zzz") == ("/zzz", [])
    assert dispatcher.complete("/help x") == ("/help x", [])
    assert dispatcher.complete("普通文本") == ("普通文本", [])


def test_complete_excludes_hidden() -> None:
    registry = Registry()
    register_builtin_commands(registry)
    registry.register(
        CommandSpec(
            name="/hush",
            description="",
            usage="/hush",
            kind=CommandKind.LOCAL,
            handler=lambda host, args: None,
            hidden=True,
        )
    )
    host = FakeHost()
    dispatcher = Dispatcher(registry, host)
    assert dispatcher.complete("/hus") == ("/hus", [])


@pytest.mark.parametrize(
    "name",
    [
        "/help",
        "/exit",
        "/compact",
        "/clear",
        "/plan",
        "/do",
        "/resume",
        "/audit",
        "/memory",
        "/permissions",
        "/status",
        "/review",
        "/fix",
        "/explain",
    ],
)
def test_all_builtin_commands_registered(name: str) -> None:
    registry = Registry()
    register_builtin_commands(registry)
    assert registry.lookup(name) is not None

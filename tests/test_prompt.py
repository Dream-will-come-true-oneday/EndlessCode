import pytest

from endless_code.prompt import (
    DEFAULT_STYLE_NAME,
    SYSTEM_PROMPT,
    Environment,
    Module,
    all_styles,
    assemble_system,
    build_system_prompt,
    find_style,
    fixed_modules,
    plan_reminder,
    style_content,
    system_reminder,
)
from endless_code.prompt import environment as environment_module


def test_module_assemble_order_and_optional_content() -> None:
    modules = [
        Module("later", 20, "later"),
        Module("empty", 15, ""),
        Module("first", 10, "first"),
    ]
    assert assemble_system(modules).splitlines() == ["first", "", "later"]


def test_extension_module_participates_without_assembly_change() -> None:
    result = assemble_system(fixed_modules() + [Module("extension", 15, "extension")])
    assert (
        result.index("endless-code")
        < result.index("extension")
        < result.index("Work carefully")
    )


def test_environment_render_contains_only_expected_fields() -> None:
    rendered = Environment(
        "/work", "test-platform", "2026-08-04", "", "1.2.3", "model-a"
    ).render()
    assert "Working directory: /work" in rendered
    assert "Platform: test-platform" in rendered
    assert "Date: 2026-08-04" in rendered
    assert "Application version: 1.2.3" in rendered
    assert "Model: model-a" in rendered


@pytest.mark.asyncio
async def test_git_fallback(monkeypatch) -> None:
    async def unavailable(_: str) -> str:
        return ""

    monkeypatch.setattr(environment_module, "_git_status", unavailable)
    result = await environment_module.gather_environment("1.0", "model")
    assert result.git_status == ""


def test_reminder_is_tagged_and_plan_variants_differ() -> None:
    assert system_reminder("notice") == "<system-reminder>notice</system-reminder>"
    assert plan_reminder(True).startswith("<system-reminder>")
    assert plan_reminder(True) != plan_reminder(False)


def test_tool_conventions_are_in_stable_prompt() -> None:
    prompt = build_system_prompt()
    assert "Prefer dedicated read_file" in prompt
    assert "always read the target content" in prompt
    assert "Some MCP tools are not loaded" in prompt
    assert "ToolSearch" in prompt


def test_prompt_includes_non_empty_instructions_and_memory() -> None:
    prompt = build_system_prompt("project rule", "remember this")
    assert "project rule" in prompt
    assert "remember this" in prompt
    assert prompt.index("project rule") < prompt.index("remember this")


def test_default_style_keeps_prompt_byte_identical() -> None:
    """默认/未知样式不注入任何模块，提示与改动前逐字节相同（N1/N3）。"""
    expected = build_system_prompt()
    assert build_system_prompt("", "", DEFAULT_STYLE_NAME) == expected
    assert build_system_prompt("", "", "nope") == expected
    assert expected == SYSTEM_PROMPT


def test_non_default_styles_inject_after_tone_module() -> None:
    for name in ("concise", "explanatory", "learning"):
        prompt = build_system_prompt("", "", name)
        assert "precedence over the tone guidance" in prompt
        assert prompt.index("markdown only when it improves") < prompt.index(
            "precedence over the tone guidance"
        )


def test_style_content_ordering_with_instructions_and_memory() -> None:
    prompt = build_system_prompt("project rule", "remember this", "concise")
    assert prompt.index("precedence over the tone guidance") < prompt.index(
        "project rule"
    )


def test_find_style_is_case_insensitive_and_strips() -> None:
    assert find_style(" Concise ").name == "concise"
    assert find_style("nope") is None
    assert style_content("nope") == ""
    assert len(all_styles()) == 4

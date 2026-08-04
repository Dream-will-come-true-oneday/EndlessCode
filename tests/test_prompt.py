import pytest

from endless_code.prompt import (
    Environment,
    Module,
    assemble_system,
    build_system_prompt,
    fixed_modules,
    plan_reminder,
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

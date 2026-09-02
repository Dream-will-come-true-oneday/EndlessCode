"""Registry 注册、冲突检测、查找与补全测试。"""

import pytest

from endless_code.command.registry import Registry
from endless_code.command.types import CommandError, CommandKind, CommandSpec


def make_spec(
    name: str,
    aliases: tuple[str, ...] = (),
    hidden: bool = False,
) -> CommandSpec:
    return CommandSpec(
        name=name,
        description=f"{name} 描述",
        usage=name,
        kind=CommandKind.LOCAL,
        handler=lambda host, args: None,
        aliases=aliases,
        hidden=hidden,
    )


def test_lookup_by_name_and_aliases() -> None:
    registry = Registry()
    spec = make_spec("/alpha", aliases=("/a", "/al"))
    registry.register(spec)
    assert registry.lookup("/alpha") is spec
    assert registry.lookup("/a") is spec
    assert registry.lookup("/al") is spec


def test_lookup_is_case_insensitive() -> None:
    registry = Registry()
    spec = make_spec("/alpha")
    registry.register(spec)
    assert registry.lookup("/ALPHA") is spec
    assert registry.lookup(" /Alpha ") is spec


def test_duplicate_name_conflict_reports_existing() -> None:
    registry = Registry()
    registry.register(make_spec("/alpha", aliases=("/a",)))
    with pytest.raises(CommandError) as exc_info:
        registry.register(make_spec("/alpha"))
    assert "/alpha" in str(exc_info.value)


def test_alias_conflicts_with_other_name() -> None:
    registry = Registry()
    registry.register(make_spec("/alpha"))
    with pytest.raises(CommandError) as exc_info:
        registry.register(make_spec("/beta", aliases=("/alpha",)))
    assert "/alpha" in str(exc_info.value)


def test_alias_conflicts_with_other_alias() -> None:
    registry = Registry()
    registry.register(make_spec("/alpha", aliases=("/a",)))
    with pytest.raises(CommandError):
        registry.register(make_spec("/beta", aliases=("/a",)))


def test_invalid_names_rejected() -> None:
    registry = Registry()
    with pytest.raises(CommandError):
        registry.register(make_spec("alpha"))
    with pytest.raises(CommandError):
        registry.register(make_spec("/Alpha"))


def test_visible_excludes_hidden_and_is_sorted() -> None:
    registry = Registry()
    registry.register(make_spec("/zeta"))
    registry.register(make_spec("/alpha", hidden=True))
    registry.register(make_spec("/mid"))
    names = [spec.name for spec in registry.visible()]
    assert names == ["/mid", "/zeta"]


def test_completions_match_names_and_aliases() -> None:
    registry = Registry()
    registry.register(make_spec("/permissions", aliases=("/perm",)))
    registry.register(make_spec("/memory", aliases=("/mem",)))
    assert registry.completions("/per") == ["/perm", "/permissions"]
    assert registry.completions("/mem") == ["/mem", "/memory"]
    assert registry.completions("/zzz") == []


def test_completions_exclude_hidden() -> None:
    registry = Registry()
    registry.register(make_spec("/secret", hidden=True))
    assert registry.completions("/sec") == []


def test_suggest_matches_name_and_alias_without_duplicates() -> None:
    registry = Registry()
    spec = make_spec("/permissions", aliases=("/perm",))
    registry.register(spec)
    assert registry.suggest("/per") == [spec]


def test_suggest_is_case_insensitive_and_sorted() -> None:
    registry = Registry()
    registry.register(make_spec("/memory", aliases=("/mem",)))
    registry.register(make_spec("/mode"))
    registry.register(make_spec("/mid"))
    specs = registry.suggest("/M")
    assert [spec.name for spec in specs] == ["/memory", "/mid", "/mode"]
    assert registry.suggest("/mem") == [specs[0]]


def test_suggest_excludes_hidden_and_empty_result() -> None:
    registry = Registry()
    registry.register(make_spec("/secret", hidden=True))
    registry.register(make_spec("/open"))
    assert registry.suggest("/sec") == []
    assert registry.suggest("/zzz") == []

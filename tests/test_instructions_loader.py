from pathlib import Path

from endless_code.instructions import Loader


def test_loader_merges_three_levels_in_priority_order(tmp_path: Path) -> None:
    config = tmp_path / ".endless-code"
    user = tmp_path / "user"
    config.mkdir()
    user.mkdir()
    (tmp_path / "ENDLESSCODE.md").write_text("root", encoding="utf-8")
    (config / "ENDLESSCODE.md").write_text("config", encoding="utf-8")
    (user / "ENDLESSCODE.md").write_text("user", encoding="utf-8")

    assert Loader(str(tmp_path), str(user)).load() == "root\n\nconfig\n\nuser"


def test_loader_expands_include_and_rejects_escape_and_cycles(tmp_path: Path) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (tmp_path / "ENDLESSCODE.md").write_text(
        "@include rules/a.md\n@include ../outside.md", encoding="utf-8"
    )
    (rules / "a.md").write_text("rule\n@include b.md", encoding="utf-8")
    (rules / "b.md").write_text("@include a.md", encoding="utf-8")

    result = Loader(str(tmp_path), str(tmp_path / "user")).load()
    assert "rule" in result
    assert "检测到环路" in result
    assert "路径超出允许范围" in result


def test_loader_stops_after_five_levels_and_skips_binary(tmp_path: Path) -> None:
    for index in range(1, 7):
        path = tmp_path / f"{index}.md"
        path.write_text(
            f"@include {index + 1}.md" if index < 6 else "deep", encoding="utf-8"
        )
    (tmp_path / "ENDLESSCODE.md").write_text("@include 1.md", encoding="utf-8")
    assert "超过最大嵌套深度" in Loader(str(tmp_path)).load()

    (tmp_path / "ENDLESSCODE.md").write_text("@include binary.md", encoding="utf-8")
    (tmp_path / "binary.md").write_bytes(b"a\x00b")
    assert "文件不可读" in Loader(str(tmp_path)).load()


def test_loader_ignores_legacy_mewcode_filename(tmp_path: Path) -> None:
    (tmp_path / "MEWCODE.md").write_text("legacy", encoding="utf-8")

    assert Loader(str(tmp_path), str(tmp_path / "user")).load() == ""

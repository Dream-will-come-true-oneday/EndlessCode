"""checkpoint/diff 纯函数测试。"""

from endless_code.checkpoint.diff import (
    BINARY_PLACEHOLDER,
    diff_stat,
    file_diff,
    truncate_diff,
)


class TestFileDiffModified:
    def test_modified_has_plus_and_minus(self):
        diff = file_diff("a.py", "line1\nline2\n", "line1\nline2x\n")
        assert "-line2" in diff
        assert "+line2x" in diff
        assert "a.py" in diff

    def test_context_lines(self):
        diff = file_diff("a.py", "a\nb\nc\nd\ne\n", "a\nb\nc\nd\nx\n")
        assert "@@" in diff
        assert " a\n" in diff or " a" in diff

    def test_no_change_returns_empty(self):
        assert file_diff("a.py", "same\n", "same\n") == ""

    def test_header_lines(self):
        diff = file_diff("a.py", "x\n", "y\n")
        assert diff.splitlines()[0].startswith("---")
        assert diff.splitlines()[1].startswith("+++")


class TestFileDiffCreated:
    def test_new_file_all_plus(self):
        diff = file_diff("new.py", None, "hello\nworld\n")
        assert "-" not in [line[0] for line in diff.splitlines()[1:]]
        assert "+hello" in diff
        assert "新建文件" in diff

    def test_new_empty_file(self):
        assert file_diff("new.py", None, "") == ""


class TestFileDiffDeleted:
    def test_deleted_all_minus(self):
        diff = file_diff("old.py", "bye\n", None)
        assert "删除文件" in diff
        assert "-bye" in diff


class TestBinary:
    def test_binary_old(self):
        assert file_diff("bin", "abc\x00def\n", "x\n") == BINARY_PLACEHOLDER

    def test_binary_new(self):
        assert file_diff("bin", "x\n", "abc\x00def\n") == BINARY_PLACEHOLDER


class TestTruncate:
    def test_lines_limit(self):
        diff = "\n".join(f"+line{i}" for i in range(200))
        result = truncate_diff(diff, max_lines=10)
        assert "[diff truncated" in result
        assert "共 200 行" in result
        assert len(result.splitlines()) == 11

    def test_chars_limit(self):
        diff = "+x" * 10000
        result = truncate_diff(diff, max_chars=100)
        assert "[diff truncated" in result
        assert len(result) <= 100 + 40

    def test_no_truncation(self):
        diff = "+a\n-b\n"
        assert truncate_diff(diff) == diff


class TestDiffStat:
    def test_counts(self):
        diff = "--- a/f\n+++ b/f\n@@\n+a\n+b\n-c\n d\n"
        assert diff_stat(diff) == "+2 -1"

    def test_empty(self):
        assert diff_stat("") == "+0 -0"

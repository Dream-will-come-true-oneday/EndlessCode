"""加载 MEWCODE.md，并安全展开其局部 include。"""

from dataclasses import dataclass
from pathlib import Path

_INCLUDE_PREFIX = "@include "


@dataclass(frozen=True)
class Loader:
    """按固定优先级加载项目与用户指令。"""

    project_root: str
    user_home: str | None = None
    max_depth: int = 5

    def load(self) -> str:
        project_root = Path(self.project_root).expanduser().resolve()
        user_root = (
            Path(self.user_home).expanduser().resolve()
            if self.user_home is not None
            else Path.home() / ".config" / "endless-code"
        )
        candidates = [
            (project_root / "MEWCODE.md", project_root),
            (project_root / ".endless-code" / "MEWCODE.md", project_root),
            (user_root / "MEWCODE.md", user_root),
        ]
        contents = [
            self._load_file(path, boundary, 1, set())
            for path, boundary in candidates
            if path.is_file()
        ]
        return "\n\n".join(content for content in contents if content)

    def _load_file(
        self, path: Path, boundary: Path, depth: int, visited: set[Path]
    ) -> str:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return ""
        if not self._is_within(resolved, boundary):
            return f"<!-- @include 路径超出允许范围，已跳过: {path} -->"
        if resolved in visited:
            return f"<!-- @include 检测到环路，已跳过: {path} -->"
        try:
            raw = resolved.read_bytes()
        except OSError:
            return ""
        if b"\x00" in raw[:512]:
            return f"<!-- @include 文件不可读，已跳过: {path} -->"
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return f"<!-- @include 文件不可读，已跳过: {path} -->"

        next_visited = visited | {resolved}
        lines: list[str] = []
        for line in text.splitlines():
            if not line.startswith(_INCLUDE_PREFIX) or line != line.strip():
                lines.append(line)
                continue
            include_path = line[len(_INCLUDE_PREFIX) :].strip()
            if not include_path:
                lines.append(line)
                continue
            candidate = (resolved.parent / include_path).resolve()
            if depth >= self.max_depth:
                lines.append(line)
                lines.append(
                    f"<!-- @include 超过最大嵌套深度，已跳过: {include_path} -->"
                )
            elif not self._is_within(candidate, boundary):
                lines.append(
                    f"<!-- @include 路径超出允许范围，已跳过: {include_path} -->"
                )
            elif candidate in next_visited:
                lines.append(f"<!-- @include 检测到环路，已跳过: {include_path} -->")
            elif candidate.is_file():
                lines.append(
                    self._load_file(candidate, boundary, depth + 1, next_visited)
                )
            # 缺失文件按规格静默跳过。
        return "\n".join(lines)

    @staticmethod
    def _is_within(path: Path, boundary: Path) -> bool:
        try:
            path.relative_to(boundary.resolve())
        except ValueError:
            return False
        return True

"""路径沙箱：先解析符号链接，再做前缀判断（N2）。"""

import os
from pathlib import Path
from typing import Protocol


class _Rooted(Protocol):
    root: str


def resolve_root(root: str) -> str:
    """把项目根解析为绝对路径；目录不存在时抛 FileNotFoundError。"""
    return str(Path(root).expanduser().resolve(strict=True))


def eval_symlinks_or_ancestor(abs_path: str) -> str:
    """存在则解析符号链接；不存在则回退到最近已存在祖先后拼接剩余段。"""
    path = Path(abs_path)
    if path.exists():
        return str(path.resolve(strict=True))

    current = path
    tail: list[str] = []
    while not current.exists() and current != current.parent:
        tail.append(current.name)
        current = current.parent
    resolved = current.resolve(strict=True)
    for part in reversed(tail):
        resolved = resolved / part
    return str(resolved)


def sandbox_ok(engine: _Rooted, path: str) -> bool:
    """目标路径是否落在项目根内。"""
    root = engine.root
    if not path:
        target = root
    elif os.path.isabs(path):
        target = path
    else:
        target = os.path.join(root, path)

    resolved = eval_symlinks_or_ancestor(target)
    root_norm = os.path.normcase(os.path.abspath(root))
    resolved_norm = os.path.normcase(os.path.abspath(resolved))
    return resolved_norm == root_norm or resolved_norm.startswith(root_norm + os.sep)

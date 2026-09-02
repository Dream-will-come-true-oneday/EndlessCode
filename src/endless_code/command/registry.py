"""命令注册中心：注册、冲突检测、查找与补全。"""

from endless_code.command.types import CommandError, CommandSpec


class Registry:
    """集中式命令注册中心。"""

    def __init__(self) -> None:
        self._specs: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> None:
        """注册命令；名称或别名冲突时抛出 :class:`CommandError`。"""
        name = spec.name
        if not name.startswith("/") or name != name.lower():
            raise CommandError(f"非法命令名：{name}（必须以 '/' 开头且为小写）")
        keys = [name, *spec.aliases]
        for key in keys:
            existing = self._specs.get(key)
            if existing is not None:
                raise CommandError(
                    f"命令冲突：{key} 已被 {existing.name} 占用，无法注册 {name}"
                )
        for key in keys:
            self._specs[key] = spec

    def lookup(self, name: str) -> CommandSpec | None:
        """大小写不敏感按名称或别名查找。"""
        return self._specs.get(name.strip().lower())

    def visible(self) -> list[CommandSpec]:
        """按名称排序返回非隐藏命令。"""
        specs = {spec.name: spec for spec in self._specs.values() if not spec.hidden}
        return [specs[key] for key in sorted(specs)]

    def completions(self, prefix: str) -> list[str]:
        """前缀匹配的全部可见命令名与别名，排序去重返回。"""
        needle = prefix.strip().lower()
        return sorted(
            key
            for key, spec in self._specs.items()
            if not spec.hidden and key.startswith(needle)
        )

    def suggest(self, prefix: str) -> list[CommandSpec]:
        """前缀匹配名称或别名的可见命令，按名称排序去重。

        返回 CommandSpec 列表（非字符串），便于调用方直接渲染描述与别名。
        """
        needle = prefix.strip().lower()
        matched = {
            spec.name: spec
            for key, spec in self._specs.items()
            if not spec.hidden and key.startswith(needle)
        }
        return [matched[key] for key in sorted(matched)]

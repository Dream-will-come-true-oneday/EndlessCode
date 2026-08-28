"""In-process MCP tool directory with deterministic search and activation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from endless_code.mcp.tool import McpTool

_WORD_RE = re.compile(r"[\w-]+", re.UNICODE)


@dataclass
class CatalogEntry:
    """A locally discovered MCP tool and the metadata used for search."""

    name: str
    server: str
    remote_name: str
    description: str
    schema: dict[str, Any]
    read_only: bool
    tool: McpTool | None = None
    active: bool = False


class McpCatalog:
    """Directory of all MCP schemas; activation is process-local."""

    def __init__(self) -> None:
        self._entries: dict[str, CatalogEntry] = {}

    def add(self, tool: McpTool) -> None:
        full_name = tool.name()
        server, remote = _split_name(full_name, getattr(tool, "remote_name", ""))
        self._entries[full_name] = CatalogEntry(
            name=full_name,
            server=server,
            remote_name=remote,
            description=tool.description(),
            schema=tool.parameters(),
            read_only=bool(tool.read_only),
            tool=tool,
            active=bool(getattr(tool, "exposed", False)),
        )

    def add_all(self, tools: list[McpTool]) -> None:
        for tool in tools:
            self.add(tool)

    register = add

    def entries(self) -> list[CatalogEntry]:
        return [self._entries[name] for name in sorted(self._entries)]

    def get(self, name: str) -> CatalogEntry | None:
        return self._entries.get(name)

    def is_active(self, name: str) -> bool:
        entry = self.get(name)
        return bool(entry and entry.active)

    def activate(self, names: list[str]) -> list[CatalogEntry]:
        """Activate known entries and return the entries newly or already active."""
        activated: list[CatalogEntry] = []
        for name in names:
            entry = self._entries.get(name)
            if entry is None:
                continue
            entry.active = True
            if entry.tool is not None:
                entry.tool.exposed = True
            activated.append(entry)
        return activated

    def search(self, query: str = "", limit: int = 5) -> list[CatalogEntry]:
        """Stable keyword search over server, name, and description."""
        terms = [term.lower() for term in _WORD_RE.findall(query or "")]
        scored: list[tuple[int, CatalogEntry]] = []
        for entry in self.entries():
            haystack = (
                f"{entry.server} {entry.name} {entry.remote_name} {entry.description}"
            ).lower()
            # Count each query term once, then use occurrence count as a
            # deterministic tie breaker before the name.
            matched = sum(term in haystack for term in terms)
            occurrences = sum(haystack.count(term) for term in terms)
            if terms and matched == 0:
                continue
            scored.append((matched * 1000 + occurrences, entry))
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [entry for _, entry in scored[: max(0, limit)]]

    def search_and_activate(
        self, query: str = "", limit: int = 5
    ) -> list[CatalogEntry]:
        matches = self.search(query, limit)
        return self.activate([entry.name for entry in matches])


def _split_name(full_name: str, remote_name: str) -> tuple[str, str]:
    if full_name.startswith("mcp__"):
        tail = full_name[5:]
        server, separator, name = tail.partition("__")
        if separator:
            return server, name
    return "", remote_name or full_name

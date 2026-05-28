"""MCP 客户端配置：两层合并、${VAR} 展开与字段校验。"""

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


@dataclass
class ServerConfig:
    """单个 MCP server 的完整定义（已展开 ${VAR}、已校验）。"""

    type: Literal["stdio", "http"]
    command: str = ""  # stdio 必填
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""  # http 必填
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    """mcp_servers 在内存中的归一化形式（已合并）。"""

    servers: dict[str, ServerConfig] = field(default_factory=dict)


@dataclass
class _RawServer:
    """未校验的原始 server 字段（全部可选）。"""

    type: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _warn(message: str) -> None:
    print(f"[mcp] warn: {message}", file=sys.stderr)


def _load_file(path: Path) -> dict[str, _RawServer]:
    """加载单个配置文件的 mcp_servers 段；缺失/非法返回空。"""
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        _warn(f"load {path} failed: {exc}")
        return {}

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _warn(f"load {path} failed: root must be a mapping")
        return {}
    servers_raw = raw.get("mcp_servers") or {}
    if not isinstance(servers_raw, dict):
        _warn(f"load {path} failed: mcp_servers must be a mapping")
        return {}

    servers: dict[str, _RawServer] = {}
    for name, value in servers_raw.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            continue
        servers[name] = _RawServer(
            type=str(value.get("type", "") or ""),
            command=str(value.get("command", "") or ""),
            args=_as_str_list(value.get("args")),
            env=_as_str_dict(value.get("env")),
            url=str(value.get("url", "") or ""),
            headers=_as_str_dict(value.get("headers")),
        )
    return servers


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float))]


def _as_str_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, val in value.items():
        if isinstance(key, str) and isinstance(val, str):
            out[key] = val
    return out


def _expand_vars(s: str) -> tuple[str, list[str]]:
    """展开字符串中的 ${VAR}；返回 (展开结果, 未定义变量名列表)。"""
    undefined: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        var_name = m.group(1)
        if var_name not in os.environ:
            undefined.append(var_name)
            return ""
        return os.environ[var_name]

    return _VAR_PATTERN.sub(_replace, s), undefined


def _apply_expansion(name: str, srv: _RawServer) -> None:
    """对 env / headers 的值做 ${VAR} 展开，未定义变量告警（限一次）。"""
    warned: set[str] = set()
    for mapping in (srv.env, srv.headers):
        for key in mapping:
            expanded, undefined = _expand_vars(mapping[key])
            mapping[key] = expanded
            for var in undefined:
                if var not in warned:
                    warned.add(var)
                    _warn(f"undefined env var ${{{var}}} referenced by server {name}")


def _merge_servers(
    user: dict[str, _RawServer], project: dict[str, _RawServer]
) -> dict[str, _RawServer]:
    """同名 server 项目级完整覆盖用户级。"""
    merged = dict(user)
    merged.update(project)
    return merged


def _validate_server(name: str, srv: _RawServer) -> ServerConfig | None:
    """校验单个 server；非法返回 None 并告警。"""
    if srv.type not in ("stdio", "http"):
        _warn(f"skip server {name}: type must be 'stdio' or 'http'")
        return None
    if srv.type == "stdio":
        if not srv.command:
            _warn(f"skip server {name}: stdio server requires 'command'")
            return None
    elif srv.type == "http" and not srv.url:
        _warn(f"skip server {name}: http server requires 'url'")
        return None
    return ServerConfig(
        type=srv.type,  # type: ignore[arg-type]
        command=srv.command,
        args=list(srv.args),
        env=dict(srv.env),
        url=srv.url,
        headers=dict(srv.headers),
    )


def load_config(root: str) -> Config:
    """加载并合并两层配置；永不抛出。

    - 用户级：``~/.config/endless-code/mcp.yaml``
    - 项目级：``<root>/.endless-code/mcp.yaml``
    - 文件缺失视为空层；格式非法跳过该层 + stderr 告警。
    """
    try:
        user_path = Path.home() / ".config" / "endless-code" / "mcp.yaml"
    except Exception:  # noqa: BLE001
        user_path = None

    project_path = Path(root) / ".endless-code" / "mcp.yaml"

    user_servers: dict[str, _RawServer] = {}
    if user_path is not None:
        user_servers = _load_file(user_path)
    project_servers = _load_file(project_path)

    for name, srv in user_servers.items():
        _apply_expansion(name, srv)
    for name, srv in project_servers.items():
        _apply_expansion(name, srv)

    merged = _merge_servers(user_servers, project_servers)

    servers: dict[str, ServerConfig] = {}
    for name, srv in merged.items():
        validated = _validate_server(name, srv)
        if validated is not None:
            servers[name] = validated

    return Config(servers=servers)

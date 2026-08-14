"""配置层：ProviderConfig、Config 数据结构、加载与校验。"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


@dataclass
class ProviderConfig:
    """单个 AI 供应商的配置。"""

    name: str
    protocol: Literal["anthropic", "deepseek", "openai"]
    api_key: str  # 原始值，可能含 $ENV_VAR 引用
    model: str
    base_url: str | None = None
    thinking: bool = False
    context_window: int = 0

    def resolve_api_key(self) -> str:
        """展开 api_key 中的环境变量引用，返回可用的密钥。"""
        return _expand_env(self.api_key)


@dataclass
class Config:
    """完整配置，包含多个供应商。"""

    providers: list[ProviderConfig] = field(default_factory=list)


class ConfigError(Exception):
    """配置相关错误。"""


DEFAULT_ANTHROPIC_CONTEXT_WINDOW = 1_000_000
DEFAULT_OPENAI_CONTEXT_WINDOW = 1_000_000


def effective_context_window(provider: ProviderConfig) -> int:
    """返回 Provider 的显式或协议默认上下文窗口。"""
    if provider.context_window > 0:
        return provider.context_window
    if provider.protocol == "anthropic":
        return DEFAULT_ANTHROPIC_CONTEXT_WINDOW
    return DEFAULT_OPENAI_CONTEXT_WINDOW


def _expand_env(value: str) -> str:
    """展开字符串中的环境变量引用 ``$VAR_NAME``。"""
    if not isinstance(value, str):
        return value

    def _replace(m: re.Match[str]) -> str:
        var_name = m.group(1)
        env_val = os.environ.get(var_name)
        if env_val is None:
            raise ConfigError(
                f"环境变量未设置：{var_name}（请设置该环境变量或在配置中使用明文值）"
            )
        return env_val

    return re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", _replace, value)


def _find_config_file() -> Path:
    """按优先级查找配置文件。

    1. 当前目录 ``.endless-code/config.yaml``
    2. 用户目录 ``~/.config/endless-code/config.yaml``
    """
    cwd_path = Path.cwd() / ".endless-code" / "config.yaml"
    if cwd_path.exists():
        return cwd_path

    home_path = Path.home() / ".config" / "endless-code" / "config.yaml"
    if home_path.exists():
        return home_path

    raise ConfigError(
        "配置文件未找到。请在以下任一位置创建 .endless-code/config.yaml：\n"
        f"  1. {cwd_path}\n"
        f"  2. {home_path}\n"
        "可从 .endless-code/config.yaml.example 复制一份作为起点。"
    )


def _validate_provider(idx: int, raw: dict) -> ProviderConfig:
    """校验单个 provider 配置并返回 ProviderConfig。"""
    name = raw.get("name")
    protocol = raw.get("protocol")
    api_key = raw.get("api_key")
    model = raw.get("model")

    if not name:
        raise ConfigError(f"第 {idx + 1} 个 provider 缺少必填字段：name")
    if not protocol:
        raise ConfigError(f"provider「{name}」缺少必填字段：protocol")
    if protocol not in ("anthropic", "deepseek", "openai"):
        raise ConfigError(
            f"provider「{name}」的 protocol 值不合法：{protocol}（只支持 anthropic、deepseek 或 openai）"
        )
    if not api_key:
        raise ConfigError(f"provider「{name}」缺少必填字段：api_key")
    if not model:
        raise ConfigError(f"provider「{name}」缺少必填字段：model")
    context_window = raw.get("context_window", 0)
    if not isinstance(context_window, int) or isinstance(context_window, bool):
        raise ConfigError(f"provider「{name}」的 context_window 必须是整数")
    if context_window < 0:
        raise ConfigError(f"provider「{name}」的 context_window 不能小于 0")

    return ProviderConfig(
        name=str(name),
        protocol=protocol,
        api_key=str(api_key),  # 保留原始值（可能含 $VAR），使用时再展开
        model=str(model),
        base_url=raw.get("base_url"),
        thinking=bool(raw.get("thinking", False)),
        context_window=context_window,
    )


def load(path: str | None = None) -> Config:
    """加载并校验配置文件，返回 Config 对象。

    若 *path* 为 None，则按优先级自动查找配置文件。
    """
    if path is None:
        file_path = _find_config_file()
    else:
        file_path = Path(path)
        if not file_path.exists():
            raise ConfigError(f"配置文件不存在：{file_path}")

    try:
        with open(file_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件 YAML 格式错误：{e}") from e

    if raw is None:
        raise ConfigError("配置文件为空，请至少配置一个 provider。")

    raw_providers = raw.get("providers")
    if not raw_providers or not isinstance(raw_providers, list):
        raise ConfigError("配置文件缺少 providers 列表或格式不正确。")

    providers = [_validate_provider(i, p) for i, p in enumerate(raw_providers)]

    return Config(providers=providers)

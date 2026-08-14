import pytest

from endless_code.config import (
    ConfigError,
    ProviderConfig,
    _validate_provider,
    effective_context_window,
    load,
)


def test_protocols_accept_all_supported_values() -> None:
    for protocol in ("anthropic", "deepseek", "openai"):
        config = _validate_provider(
            0,
            {
                "name": protocol,
                "protocol": protocol,
                "api_key": "key",
                "model": "model",
            },
        )
        assert config.protocol == protocol


def test_invalid_protocol_is_structured_error() -> None:
    with pytest.raises(ConfigError, match="protocol"):
        _validate_provider(
            0,
            {"name": "bad", "protocol": "other", "api_key": "key", "model": "model"},
        )


def test_env_api_key_and_base_url(monkeypatch) -> None:
    monkeypatch.setenv("ENDLESS_TEST_KEY", "resolved-secret")
    config = ProviderConfig(
        "openai", "openai", "$ENDLESS_TEST_KEY", "model", "https://proxy.example/v1"
    )
    assert config.resolve_api_key() == "resolved-secret"
    assert config.base_url == "https://proxy.example/v1"


def test_missing_env_key_does_not_expose_value() -> None:
    config = ProviderConfig("anthropic", "anthropic", "$MISSING_TEST_KEY", "model")
    with pytest.raises(ConfigError, match="MISSING_TEST_KEY"):
        config.resolve_api_key()


def test_load_preserves_provider_order_and_base_url(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """providers:
  - name: claude
    protocol: anthropic
    api_key: test
    model: claude-test
  - name: openai-compatible
    protocol: openai
    api_key: test
    model: gpt-test
    base_url: https://proxy.example/v1
  - name: deepseek
    protocol: deepseek
    api_key: test
    model: deepseek-chat
""",
        encoding="utf-8",
    )
    config = load(str(path))
    assert [item.protocol for item in config.providers] == [
        "anthropic",
        "openai",
        "deepseek",
    ]
    assert config.providers[1].base_url == "https://proxy.example/v1"


def test_provider_factory_constructs_all_protocols() -> None:
    from endless_code.llm import new_provider

    providers = [
        new_provider(ProviderConfig(protocol, protocol, "test", "model"))
        for protocol in ("anthropic", "deepseek", "openai")
    ]
    assert [provider.name for provider in providers] == [
        "anthropic",
        "deepseek",
        "openai",
    ]


def test_context_window_defaults_and_explicit_value() -> None:
    assert (
        effective_context_window(ProviderConfig("a", "anthropic", "k", "m"))
        == 1_000_000
    )
    assert (
        effective_context_window(ProviderConfig("o", "openai", "k", "m")) == 1_000_000
    )
    assert (
        effective_context_window(ProviderConfig("d", "deepseek", "k", "m")) == 1_000_000
    )
    assert (
        effective_context_window(
            ProviderConfig("a", "anthropic", "k", "m", context_window=80_000)
        )
        == 80_000
    )

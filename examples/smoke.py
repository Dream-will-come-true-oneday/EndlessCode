"""Run two minimal requests and print token/cache usage without exposing secrets."""

import argparse
import asyncio
import sys
from pathlib import Path

from endless_code import __version__
from endless_code.agent import Agent
from endless_code.config import ConfigError, load
from endless_code.conversation import Conversation
from endless_code.llm import new_provider
from endless_code.permission import Mode, new_engine
from endless_code.security import redact_sensitive
from endless_code.tool import new_default_registry


async def _run(provider_name: str | None) -> int:
    try:
        config = load()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    if not config.providers:
        print("Configuration error: no providers configured", file=sys.stderr)
        return 1

    selected = next(
        (
            item
            for item in config.providers
            if provider_name is None
            or item.name == provider_name
            or item.protocol == provider_name
        ),
        None,
    )
    if selected is None:
        print("Configuration error: provider not found", file=sys.stderr)
        return 1
    try:
        secret = selected.resolve_api_key()
        provider = new_provider(selected)
    except Exception as exc:  # noqa: BLE001
        print(
            "Provider error: " + redact_sensitive(exc, {selected.api_key}),
            file=sys.stderr,
        )
        return 1
    secrets = {secret} if secret else set()
    engine, _ = new_engine(str(Path.cwd().resolve()))
    agent = Agent(provider, new_default_registry(), __version__, engine)
    conversation = Conversation()
    for index, prompt in enumerate(("Reply with 'ready'.", "Reply with 'ok'."), 1):
        conversation.add_user(prompt)
        usage = None
        try:
            async for event in agent.run(conversation, mode=Mode.BYPASS):
                if event.err is not None:
                    print(
                        "Provider error: " + redact_sensitive(event.err, secrets),
                        file=sys.stderr,
                    )
                    return 1
                if event.usage is not None:
                    usage = event.usage
        except Exception as exc:  # noqa: BLE001
            print("Provider error: " + redact_sensitive(exc, secrets), file=sys.stderr)
            return 1
        if usage is None:
            print(f"round={index} input=0 output=0 cache_write=0 cache_read=0")
        else:
            print(
                f"round={index} input={usage.input_tokens} output={usage.output_tokens} "
                f"cache_write={usage.cache_write} cache_read={usage.cache_read}"
            )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", help="provider name or protocol")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.provider)))

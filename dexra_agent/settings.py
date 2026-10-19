"""Agent settings from the environment. Trading settings live in dexra_core.config."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dexra_core.config import ConfigError

DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-5.6-luna"}


@dataclass(frozen=True)
class AgentSettings:
    llm_provider: str
    llm_model: str
    use_dexra_mcp: bool
    dexra_mcp_url: str
    require_approval: bool
    buy_amount_sol: float


def _text(env: Mapping[str, str], name: str) -> str | None:
    value = (env.get(name) or "").strip()
    return value or None


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = (_text(env, name) or "").lower()
    if not value:
        return default
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    raise ConfigError(f'{name} must be true or false, got "{value}"')


def load_settings(env: Mapping[str, str] | None = None) -> AgentSettings:
    """Reads the agent's settings; raises ConfigError on invalid values."""
    env = os.environ if env is None else env
    provider = (_text(env, "LLM_PROVIDER") or "anthropic").lower()
    if provider not in DEFAULT_MODELS:
        raise ConfigError(f'LLM_PROVIDER must be anthropic or openai, got "{provider}"')
    base = (_text(env, "DEXRA_BASE_URL") or "https://api.dexra.dev").rstrip("/")
    try:
        amount = float(_text(env, "BUY_AMOUNT_SOL") or "0.01")
    except ValueError:
        raise ConfigError("BUY_AMOUNT_SOL must be a number") from None
    return AgentSettings(
        llm_provider=provider,
        llm_model=_text(env, "LLM_MODEL") or DEFAULT_MODELS[provider],
        use_dexra_mcp=_flag(env, "USE_DEXRA_MCP", False),
        dexra_mcp_url=_text(env, "DEXRA_MCP_URL") or f"{base}/mcp",
        require_approval=_flag(env, "REQUIRE_APPROVAL", True),
        buy_amount_sol=amount,
    )

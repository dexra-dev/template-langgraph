"""Free-form ReAct mode: an LLM with the same tools, built with langchain.agents.create_agent."""

from __future__ import annotations

import os
import warnings
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver

from dexra_core.config import Config

from .prompts import SYSTEM_PROMPT
from .settings import AgentSettings
from .tools import make_tools

# Our tools own these names; a remote tool must never shadow them, buy and sell above all.
LOCAL_NAMES = {"check_token", "preflight", "fresh_launches", "buy", "sell"}


def build_model(settings: AgentSettings) -> BaseChatModel:
    """The chat model for LLM_PROVIDER; keys come from ANTHROPIC_API_KEY or OPENAI_API_KEY."""
    if settings.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=settings.llm_model)
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model_name=settings.llm_model, timeout=None, stop=None)


def keep_remote_tool(tool: BaseTool) -> bool:
    """Only read-only remote tools that don't collide with ours, so trades stay on the core guard."""
    annotations = ((tool.metadata or {}).get("mcp") or {}).get("tool", {}).get("annotations") or {}
    return tool.name not in LOCAL_NAMES and annotations.get("read_only_hint") is True


async def load_dexra_mcp_tools(settings: AgentSettings, api_key: str | None) -> list[BaseTool]:
    """Read-only tools from Dexra's remote MCP server (Streamable HTTP, bearer auth)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # langchain.mcp is in beta and says so on import
        from fastmcp.client import Client
        from langchain.mcp import MCPAdapter

    # The adapter's tools keep their client alive after the context exits.
    async with MCPAdapter(Client(settings.dexra_mcp_url, auth=api_key)) as adapter:
        tools = await adapter.list_tools()
    return [t for t in tools if keep_remote_tool(t)]


def build_agent(
    model: BaseChatModel | str,
    config: Config | None = None,
    extra_tools: list[BaseTool] | None = None,
    checkpointer: Any | None = None,
    approve_trades: bool = False,
):
    """A ReAct agent with the five trading tools plus any extra (read-only) tools.

    approve_trades pauses before every buy and sell for a human yes or no; the CLI turns it on
    for live trading when REQUIRE_APPROVAL is true.
    """
    extra = [t for t in extra_tools or [] if t.name not in LOCAL_NAMES]
    gate = InterruptOnConfig(allowed_decisions=["approve", "reject"])
    middleware = (
        [HumanInTheLoopMiddleware(interrupt_on={"buy": gate, "sell": gate}, description_prefix="Live trade")] if approve_trades else []
    )
    return create_agent(
        model,
        [*make_tools(config), *extra],
        system_prompt=SYSTEM_PROMPT,
        middleware=middleware,
        checkpointer=checkpointer or InMemorySaver(),
        name="dexra_trader",
    )


def has_llm_key(settings: AgentSettings) -> bool:
    name = "OPENAI_API_KEY" if settings.llm_provider == "openai" else "ANTHROPIC_API_KEY"
    return bool(os.environ.get(name))

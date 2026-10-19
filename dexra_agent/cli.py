"""Command line entry points: `dexra-graph` (the explicit pipeline) and `dexra-agent` (free-form chat)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

from langgraph.types import Command

from dexra_core.config import ConfigError, load_config

from .settings import load_settings


def _ask(question: str) -> bool:
    if not sys.stdin.isatty():
        print(f"{question} -> no terminal to ask, declining")
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def graph_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dexra-graph", description="scan -> check -> decide -> trade -> report")
    parser.add_argument("--mint", help="check and buy this mint; without it, scan new launches")
    parser.add_argument("--amount", type=float, help="SOL to spend (default BUY_AMOUNT_SOL)")
    parser.add_argument("--minutes", type=int, default=30, help="how far back to scan new launches")
    parser.add_argument("--json", action="store_true", help="print the final state as JSON")
    args = parser.parse_args(argv)

    from .graph import build_graph

    try:
        settings = load_settings()
        config = load_config()
    except ConfigError as error:
        sys.exit(f"config error: {error}")

    graph = build_graph(config, require_approval=settings.require_approval)
    run = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state: Any = {
        "mint": args.mint,
        "amount_sol": args.amount if args.amount is not None else settings.buy_amount_sol,
        "minutes": args.minutes,
    }
    result = graph.invoke(state, run)
    while "__interrupt__" in result:
        request = result["__interrupt__"][0].value
        for reason in request.get("reasons", []):
            print(f"  - {reason}")
        result = graph.invoke(Command(resume=_ask(request["question"])), run)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result["report"])


async def _chat(agent: Any, run: dict, text: str) -> None:
    result = await agent.ainvoke({"messages": [{"role": "user", "content": text}]}, run)
    while "__interrupt__" in result:
        decisions = []
        for action in result["__interrupt__"][0].value["action_requests"]:
            ok = _ask(f"{action['name']} {json.dumps(action['args'])}?")
            decisions.append({"type": "approve"} if ok else {"type": "reject", "message": "The user declined."})
        result = await agent.ainvoke(Command(resume={"decisions": decisions}), run)
    print(result["messages"][-1].text)


async def _agent(prompt: str | None) -> None:
    from .agent import build_agent, build_model, has_llm_key, load_dexra_mcp_tools

    try:
        settings = load_settings()
        config = load_config()
    except ConfigError as error:
        sys.exit(f"config error: {error}")
    if not has_llm_key(settings):
        key = "OPENAI_API_KEY" if settings.llm_provider == "openai" else "ANTHROPIC_API_KEY"
        sys.exit(f"{key} is not set. The graph mode (dexra-graph) runs without an LLM.")

    extra = await load_dexra_mcp_tools(settings, config.dexra_api_key) if settings.use_dexra_mcp else []
    agent = build_agent(
        build_model(settings),
        config,
        extra_tools=extra,
        approve_trades=config.live and settings.require_approval,
    )
    mode = "LIVE" if config.live else f"dry run ({config.dry_run_reason})"
    print(f"{settings.llm_provider}:{settings.llm_model}, {mode}, {len(extra)} Dexra MCP tools")
    run = {"configurable": {"thread_id": str(uuid.uuid4())}}
    if prompt:
        await _chat(agent, run, prompt)
        return
    while True:
        try:
            text = input("> ").strip()
        except EOFError:
            return
        if text in ("exit", "quit"):
            return
        if text:
            await _chat(agent, run, text)


def agent_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dexra-agent", description="chat with a Dexra-guarded trading agent")
    parser.add_argument("prompt", nargs="?", help="one message; without it, start a chat")
    args = parser.parse_args(argv)
    asyncio.run(_agent(args.prompt))

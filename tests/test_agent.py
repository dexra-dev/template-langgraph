"""The ReAct mode with a scripted fake model: no LLM is called."""

import uuid

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command

from conftest import BONK, DEXRA, JUP, dexra_result, make_config
from dexra_agent.agent import build_agent, keep_remote_tool
from dexra_agent.settings import load_settings
from dexra_core.config import ConfigError


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def scripted(*messages):
    return ScriptedModel(messages=iter(messages))


def call(name, args, id="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id, "type": "tool_call"}])


def thread():
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


def test_agent_buy_is_blocked_by_the_guard(mock):
    mock.get(f"{DEXRA}/token_deep_check").mock(return_value=httpx.Response(200, json=dexra_result("avoid", ["mint authority active"])))
    mock.get(f"{DEXRA}/swap_preflight").mock(return_value=httpx.Response(200, json=dexra_result("ok")))
    order = mock.get(f"{JUP}/order")
    model = scripted(call("buy", {"mint": BONK, "amount_sol": 0.01}), AIMessage(content="Blocked: mint authority active."))
    agent = build_agent(model, make_config())
    result = agent.invoke({"messages": [{"role": "user", "content": "buy BONK"}]}, thread())
    tool_msg = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert '"status": "blocked"' in tool_msg.content and "mint authority active" in tool_msg.content
    assert result["messages"][-1].content == "Blocked: mint authority active."
    assert order.call_count == 0


def test_live_agent_asks_before_buying_and_reject_skips_the_tool(mock):
    t = mock.get(f"{DEXRA}/token_deep_check").mock(return_value=httpx.Response(200, json=dexra_result("ok")))
    model = scripted(call("buy", {"mint": BONK, "amount_sol": 0.01}), AIMessage(content="Not bought."))
    agent = build_agent(model, make_config(), approve_trades=True)
    cfg = thread()
    result = agent.invoke({"messages": [{"role": "user", "content": "buy BONK"}]}, cfg)
    request = result["__interrupt__"][0].value
    assert request["action_requests"][0]["name"] == "buy"
    result = agent.invoke(Command(resume={"decisions": [{"type": "reject", "message": "no"}]}), cfg)
    assert result["messages"][-1].content == "Not bought."
    assert t.call_count == 0


def test_remote_tools_are_filtered():
    def make(name, read_only):
        meta = {"mcp": {"tool": {"annotations": {"read_only_hint": read_only}}}}
        return StructuredTool.from_function(lambda: None, name=name, description="d", metadata=meta)

    assert keep_remote_tool(make("wallet_check", True))
    assert not keep_remote_tool(make("buy", True))
    assert not keep_remote_tool(make("swap_execute", False))
    assert not keep_remote_tool(make("swap_unknown", None))


def test_settings_defaults_and_errors():
    s = load_settings({})
    assert (s.llm_provider, s.llm_model, s.use_dexra_mcp, s.require_approval) == ("anthropic", "claude-sonnet-5-5", False, True)
    assert s.dexra_mcp_url == "https://api.dexra.dev/mcp"
    assert load_settings({"DEXRA_BASE_URL": "http://127.0.0.1:3000/"}).dexra_mcp_url == "http://127.0.0.1:3000/mcp"
    assert load_settings({"LLM_PROVIDER": "openai"}).llm_model == "gpt-5.6-luna"
    with pytest.raises(ConfigError):
        load_settings({"LLM_PROVIDER": "other"})

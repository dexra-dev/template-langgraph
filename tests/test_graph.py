import uuid

import httpx
from langgraph.types import Command
from solders.keypair import Keypair

from conftest import BONK, DEXRA, JUP, dexra_result, make_config
from dexra_agent.graph import build_graph

OTHER = "So11111111111111111111111111111111111111112"


def checks(mock, token="ok", preflight="ok"):
    t = mock.get(f"{DEXRA}/token_deep_check")
    p = mock.get(f"{DEXRA}/swap_preflight")
    for route, reply in ((t, token), (p, preflight)):
        if isinstance(reply, httpx.Response):
            route.mock(return_value=reply)
        else:
            route.mock(return_value=httpx.Response(200, json=dexra_result(reply, [f"{reply} reason"])))
    return t, p


def live_config(**env):
    return make_config(DRY_RUN="false", JUPITER_API_KEY="jup", SOLANA_PRIVATE_KEY=str(Keypair()), **env)


def run(graph, state, thread=None):
    """Invokes the graph and returns (final state, node names in order)."""
    cfg = {"configurable": {"thread_id": thread or str(uuid.uuid4())}}
    nodes = []
    for update in graph.stream(state, cfg, stream_mode="updates"):
        nodes += [k for k in update if k != "__interrupt__"]
    return graph.get_state(cfg).values, nodes, cfg


def test_given_mint_ok_runs_the_whole_path_in_dry_run(mock):
    t, p = checks(mock)
    order = mock.get(f"{JUP}/order")
    state, nodes, _ = run(build_graph(make_config()), {"mint": BONK, "amount_sol": 0.01})
    assert nodes == ["scan", "check", "decide", "trade", "report"]
    assert state["decision"]["allowed"] is True
    assert state["trade"]["status"] == "dry_run" and state["trade"]["signature"] is None
    assert "auto: dry run" in state["approval_note"]
    assert "Dry run: nothing was sent" in state["report"]
    # check once, then the core buy checks again right before trading
    assert t.call_count == 2 and p.call_count == 2
    assert order.call_count == 0


def test_avoid_verdict_goes_straight_to_report(mock):
    t, _ = checks(mock, token="avoid")
    order = mock.get(f"{JUP}/order")
    state, nodes, _ = run(build_graph(live_config(), require_approval=False), {"mint": BONK, "amount_sol": 0.01})
    assert nodes == ["scan", "check", "decide", "report"]
    assert state["decision"]["allowed"] is False and state.get("trade") is None
    assert "BLOCKED" in state["report"] and "avoid reason" in state["report"]
    assert t.call_count == 1 and order.call_count == 0


def test_dexra_failure_fails_closed(mock):
    checks(mock, preflight=httpx.Response(402, json={"error": "payment_required"}))
    state, nodes, _ = run(build_graph(make_config()), {"mint": BONK, "amount_sol": 0.01})
    assert "trade" not in nodes
    assert any("swap_preflight failed" in r for r in state["decision"]["reasons"])


def test_amount_over_cap_is_blocked_before_any_call(mock):
    t, p = checks(mock)
    state, nodes, _ = run(build_graph(make_config()), {"mint": BONK, "amount_sol": 1})
    assert nodes == ["scan", "check", "decide", "report"]
    assert t.call_count == 0 and p.call_count == 0
    assert "MAX_TRADE_SOL" in state["report"]


def test_scan_picks_the_first_passing_launch(mock):
    launches = [
        {"mint": OTHER, "verdict": "avoid", "token": {"symbol": "BAD"}},
        {"mint": BONK, "verdict": "ok", "token": {"symbol": "BONK"}},
    ]
    scan = mock.get(f"{DEXRA}/new_launches").mock(
        return_value=httpx.Response(200, json={"result": {"summary": "2 launches", "count": 2, "launches": launches}})
    )
    t, _ = checks(mock)
    state, nodes, _ = run(build_graph(make_config()), {"amount_sol": 0.01, "minutes": 15})
    assert scan.calls.last.request.url.params["minutes"] == "15"
    assert state["mint"] == BONK and "picked BONK" in state["scan_note"]
    assert t.calls.last.request.url.params["mint"] == BONK
    assert nodes[-1] == "report" and state["trade"]["status"] == "dry_run"


def test_empty_scan_reports_without_checking(mock):
    mock.get(f"{DEXRA}/new_launches").mock(
        return_value=httpx.Response(200, json={"result": {"summary": "0 launches", "count": 0, "launches": []}})
    )
    t, _ = checks(mock)
    state, nodes, _ = run(build_graph(make_config()), {"amount_sol": 0.01})
    assert nodes == ["scan", "report"] and t.call_count == 0
    assert "none with a passing verdict" in state["report"]


def test_live_buy_waits_for_a_human_and_decline_sends_nothing(mock):
    checks(mock)
    order = mock.get(f"{JUP}/order")
    graph = build_graph(live_config())
    state, nodes, cfg = run(graph, {"mint": BONK, "amount_sol": 0.01})
    pending = graph.get_state(cfg)
    assert nodes == ["scan", "check"] and pending.next == ("decide",)
    payload = pending.tasks[0].interrupts[0].value
    assert payload["mint"] == BONK and payload["verdicts"] == {"token": "ok", "preflight": "ok"}

    graph.invoke(Command(resume=False), cfg)
    final = graph.get_state(cfg).values
    assert final["approved"] is False and final.get("trade") is None
    assert "declined by a human" in final["report"]
    assert order.call_count == 0


def test_live_buy_approved_reaches_jupiter(mock):
    checks(mock)
    order = mock.get(f"{JUP}/order").mock(
        return_value=httpx.Response(200, json={"requestId": "r", "transaction": "", "errorCode": 1, "errorMessage": "Insufficient funds"})
    )
    execute = mock.post(f"{JUP}/execute")
    graph = build_graph(live_config())
    _, _, cfg = run(graph, {"mint": BONK, "amount_sol": 0.01})
    result = graph.invoke(Command(resume="yes"), cfg)
    assert result["approved"] is True
    assert result["trade"]["mode"] == "live" and result["trade"]["status"] == "failed"
    assert order.call_count == 1 and execute.call_count == 0
    assert order.calls.last.request.url.params["taker"]

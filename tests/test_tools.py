import httpx

from conftest import BONK, DEXRA, JUP, dexra_result, make_config
from dexra_agent.tools import make_tools


def tools(**env):
    return {t.name: t for t in make_tools(make_config(**env))}


def test_tool_names():
    assert set(tools()) == {"check_token", "preflight", "fresh_launches", "buy", "sell"}


def test_check_token_and_preflight_call_dexra(mock):
    t = mock.get(f"{DEXRA}/token_deep_check").mock(return_value=httpx.Response(200, json=dexra_result("ok")))
    p = mock.get(f"{DEXRA}/swap_preflight").mock(return_value=httpx.Response(200, json=dexra_result("caution")))
    ts = tools(DEXRA_API_KEY="dxr_test")
    assert ts["check_token"].invoke({"mint": BONK})["verdict"] == "ok"
    assert ts["preflight"].invoke({"mint": BONK, "side": "buy", "amount": 0.02})["verdict"] == "caution"
    assert t.calls.last.request.headers["authorization"] == "Bearer dxr_test"
    assert p.calls.last.request.url.params["amount"] == "0.02"


def test_dexra_errors_come_back_as_data(mock):
    mock.get(f"{DEXRA}/token_deep_check").mock(return_value=httpx.Response(402, json={"error": "payment_required"}))
    out = tools()["check_token"].invoke({"mint": BONK})
    assert "payment_required" in out["error"]


def test_fresh_launches_are_trimmed(mock):
    launch = {"mint": BONK, "verdict": "ok", "reasons": ["r"], "token": {"symbol": "BONK"}, "first_buyers": {"big": "x"}}
    mock.get(f"{DEXRA}/new_launches").mock(
        return_value=httpx.Response(200, json={"result": {"summary": "s", "count": 1, "launches": [launch]}})
    )
    out = tools()["fresh_launches"].invoke({"minutes": 10})
    (brief,) = out["launches"]
    assert (brief["mint"], brief["symbol"], brief["verdict"], brief["reasons"]) == (BONK, "BONK", "ok", ["r"])
    assert "first_buyers" not in brief


def test_buy_tool_goes_through_the_guard(mock):
    mock.get(f"{DEXRA}/token_deep_check").mock(return_value=httpx.Response(200, json=dexra_result("avoid", ["freeze authority"])))
    mock.get(f"{DEXRA}/swap_preflight").mock(return_value=httpx.Response(200, json=dexra_result("ok")))
    order = mock.get(f"{JUP}/order")
    out = tools()["buy"].invoke({"mint": BONK, "amount_sol": 0.01})
    assert out["status"] == "blocked" and "freeze authority" in " ".join(out["decision"]["reasons"])
    assert order.call_count == 0


def test_sell_tool_dry_run(mock):
    mock.get(f"{DEXRA}/describe_address").mock(return_value=httpx.Response(200, json={"result": {"stats": {"decimals": 5}}}))
    mock.get(f"{DEXRA}/swap_preflight").mock(return_value=httpx.Response(200, json=dexra_result("avoid")))
    out = tools()["sell"].invoke({"mint": BONK, "amount_atomic": 100000})
    assert out["status"] == "dry_run" and out["input"]["amount"] == 1.0

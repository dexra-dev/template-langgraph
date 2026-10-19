import httpx
import pytest

from conftest import BONK, DEXRA, dexra_result, make_config
from dexra_core.guard import decide_buy, decide_sell


def route(mock, token, preflight):
    t = mock.get(f"{DEXRA}/token_deep_check")
    p = mock.get(f"{DEXRA}/swap_preflight")
    for r, reply in ((t, token), (p, preflight)):
        if isinstance(reply, Exception):
            r.mock(side_effect=reply)
        elif isinstance(reply, httpx.Response):
            r.mock(return_value=reply)
        else:
            r.mock(return_value=httpx.Response(200, json=reply))
    return t, p


def test_ok_and_ok_allows_and_sends_sol_amount(mock):
    _, p = route(mock, dexra_result("ok", ["fine"]), dexra_result("ok"))
    d = decide_buy(BONK, 0.01, make_config())
    assert d.allowed and d.verdicts == {"token": "ok", "preflight": "ok"}
    params = p.calls.last.request.url.params
    assert (params["side"], params["amount"], params["mint"]) == ("buy", "0.01", BONK)


@pytest.mark.parametrize(
    "token,pre",
    [("avoid", "ok"), ("ok", "avoid"), ("unknown", "ok"), ("ok", "unknown"), ("caution", "ok"), ("ok", "caution"), (None, "ok")],
)
def test_bad_verdicts_block_by_default(mock, token, pre):
    route(mock, dexra_result(token, ["why"]), dexra_result(pre, ["why"]))
    d = decide_buy(BONK, 0.01, make_config())
    assert not d.allowed
    assert any("blocks the buy" in r for r in d.reasons)
    assert any(r.endswith(": why") for r in d.reasons)


def test_allow_caution_never_lets_avoid_or_unknown_through(mock):
    cfg = make_config(ALLOW_CAUTION="true")
    route(mock, dexra_result("caution"), dexra_result("caution"))
    assert decide_buy(BONK, 0.01, cfg).allowed
    route(mock, dexra_result("caution"), dexra_result("avoid"))
    assert not decide_buy(BONK, 0.01, cfg).allowed
    route(mock, dexra_result("unknown"), dexra_result("caution"))
    assert not decide_buy(BONK, 0.01, cfg).allowed


def test_amount_caps_checked_before_any_call(mock):
    t, p = route(mock, dexra_result("ok"), dexra_result("ok"))
    for amount in (0, -1, float("nan"), float("inf"), 0.0500001, 1):
        assert not decide_buy(BONK, amount, make_config()).allowed, amount
    assert decide_buy(BONK, 0.2, make_config(MAX_TRADE_SOL="0.1")).reasons == ["amount 0.2 SOL is above MAX_TRADE_SOL 0.1"]
    assert not decide_buy("not-a-mint", 0.01, make_config()).allowed
    assert t.call_count == 0 and p.call_count == 0
    assert decide_buy(BONK, 0.05, make_config()).allowed


def test_dexra_error_fails_closed(mock):
    route(mock, httpx.Response(402, json={"error": "payment_required", "context": "This call needs a payment"}), dexra_result("ok"))
    d = decide_buy(BONK, 0.01, make_config())
    assert not d.allowed and d.verdicts["token"] is None
    assert "token_deep_check failed" in d.reasons[0] and "payment_required (HTTP 402)" in d.reasons[0]


def test_network_error_fails_closed(mock):
    route(mock, dexra_result("ok"), httpx.ConnectError("refused"))
    d = decide_buy(BONK, 0.01, make_config())
    assert not d.allowed
    assert any("swap_preflight failed" in r for r in d.reasons)


def test_sell_allowed_on_avoid_with_reasons(mock):
    _, p = route(mock, dexra_result("ok"), dexra_result("avoid", ["token is non-transferable"]))
    d = decide_sell(BONK, 1000, make_config())
    assert d.allowed and d.verdicts["preflight"] == "avoid"
    assert "swap_preflight: token is non-transferable" in d.reasons
    assert p.calls.last.request.url.params["side"] == "sell"


def test_sell_goes_ahead_when_dexra_fails_and_refuses_zero(mock):
    route(mock, dexra_result("ok"), httpx.Response(500, json={"error": "internal", "context": "boom"}))
    d = decide_sell(BONK, 5, make_config())
    assert d.allowed and "selling anyway" in d.reasons[0]
    assert not decide_sell(BONK, 0, make_config()).allowed

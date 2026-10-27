import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

import whitelist_gate
from conftest import BONK, DEXRA
from whitelist_gate import dexra_allows_buy

ON = {"api_key": "dxr_test", "base_url": DEXRA}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answer(verdict, reasons=()):
    return {"result": {"verdict": verdict, "reasons": list(reasons)}}


@pytest.fixture
def dexra(monkeypatch):
    """Routes urlopen by path to a dict answer or an exception and records each request."""
    replies, calls = {}, []

    def urlopen(request, timeout):
        calls.append(request)
        reply = replies[urlsplit(request.full_url).path]
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(json.dumps(reply).encode())

    monkeypatch.setattr(whitelist_gate, "urlopen", urlopen)

    def set_replies(token, preflight):
        replies.update({"/token_deep_check": token, "/swap_preflight": preflight})
        return calls

    return set_replies


def test_without_a_key_the_gate_is_off_and_makes_no_call(dexra, monkeypatch):
    monkeypatch.delenv("DEXRA_API_KEY", raising=False)
    calls = dexra(answer("avoid"), answer("avoid"))
    r = dexra_allows_buy(BONK, 0.01, base_url=DEXRA)
    assert r.allowed and not r.checked
    assert calls == []


def test_ok_and_ok_allows_and_asks_preflight_for_a_buy_in_sol(dexra):
    calls = dexra(answer("ok", ["fine"]), answer("ok"))
    r = dexra_allows_buy(BONK, 0.01, **ON)
    assert r.allowed and r.checked
    assert r.verdicts == {"token": "ok", "preflight": "ok"}
    assert "token_deep_check: fine" in r.reasons
    pre = next(c for c in calls if "/swap_preflight" in c.full_url)
    assert parse_qs(urlsplit(pre.full_url).query) == {"mint": [BONK], "side": ["buy"], "amount": ["0.01"]}
    assert pre.get_header("Authorization") == "Bearer dxr_test"


@pytest.mark.parametrize("token,pre", [("avoid", "ok"), ("ok", "unknown"), ("caution", "ok"), (None, "ok")])
def test_anything_but_ok_blocks(dexra, token, pre):
    dexra(answer(token), answer(pre))
    assert not dexra_allows_buy(BONK, 0.01, **ON).allowed


def test_allow_caution_lets_caution_pass_but_not_avoid(dexra):
    dexra(answer("caution"), answer("ok"))
    assert dexra_allows_buy(BONK, 0.01, allow_caution=True, **ON).allowed
    dexra(answer("caution"), answer("avoid"))
    assert not dexra_allows_buy(BONK, 0.01, allow_caution=True, **ON).allowed


@pytest.mark.parametrize(
    "error",
    [
        HTTPError(DEXRA, 402, "Payment Required", {}, io.BytesIO(b'{"error": "payment_required"}')),
        HTTPError(DEXRA, 503, "Unavailable", {}, io.BytesIO(b"not json")),
        URLError("connection refused"),
    ],
)
def test_a_dexra_error_blocks_by_default_and_allows_with_fail_open(dexra, error):
    dexra(answer("ok"), error)
    r = dexra_allows_buy(BONK, 0.01, **ON)
    assert not r.allowed
    assert any(x.startswith("swap_preflight failed") and x.endswith("blocking") for x in r.reasons)
    assert dexra_allows_buy(BONK, 0.01, fail_open=True, **ON).allowed


def test_the_api_error_kind_is_in_the_reason(dexra):
    dexra(HTTPError(DEXRA, 402, "Payment Required", {}, io.BytesIO(b'{"error": "payment_required"}')), answer("ok"))
    r = dexra_allows_buy(BONK, 0.01, **ON)
    assert "token_deep_check failed (payment_required (HTTP 402)), blocking" in r.reasons

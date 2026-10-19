import base64
import json

import httpx
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

from conftest import BONK, DEXRA, JUP, SOL, dexra_result, make_config, unsigned_transaction
from dexra_core.trade import buy, sell


def ok_checks(mock):
    mock.get(f"{DEXRA}/token_deep_check").mock(return_value=httpx.Response(200, json=dexra_result("ok")))
    mock.get(f"{DEXRA}/swap_preflight").mock(return_value=httpx.Response(200, json=dexra_result("ok")))


def quote(**extra):
    body = {
        "requestId": "req-1",
        "inputMint": SOL,
        "outputMint": BONK,
        "inAmount": "10000000",
        "outAmount": "356918106200",
        "otherAmountThreshold": "353000000000",
        "slippageBps": 100,
        "priceImpact": -0.01,
        "router": "metis",
        "feeBps": 10,
        "transaction": None,
        **extra,
    }
    return httpx.Response(200, json=body)


def live(wallet: Keypair, **env):
    return make_config(DRY_RUN="false", JUPITER_API_KEY="jup", SOLANA_PRIVATE_KEY=str(wallet), **env)


def test_dry_run_without_jupiter_key_never_quotes_or_executes(mock):
    ok_checks(mock)
    order = mock.get(f"{JUP}/order")
    execute = mock.post(f"{JUP}/execute")
    r = buy(BONK, 0.01, make_config())
    assert (r["status"], r["ok"], r["input"]["amountAtomic"], r["quote"], r["signature"]) == ("dry_run", True, "10000000", None, None)
    assert order.call_count == 0 and execute.call_count == 0
    json.dumps(r)


def test_dry_run_with_jupiter_key_quotes_without_taker(mock):
    ok_checks(mock)
    order = mock.get(f"{JUP}/order").mock(return_value=quote())
    execute = mock.post(f"{JUP}/execute")
    r = buy(BONK, 0.01, make_config(JUPITER_API_KEY="jup", SOLANA_PRIVATE_KEY=str(Keypair())))
    assert r["status"] == "dry_run" and r["quote"]["outAmount"] == "356918106200"
    req = order.calls.last.request
    assert "taker" not in req.url.params
    assert (req.url.params["amount"], req.url.params["slippageBps"], req.headers["x-api-key"]) == ("10000000", "100", "jup")
    assert execute.call_count == 0


def test_dry_run_false_without_wallet_stays_dry(mock):
    ok_checks(mock)
    mock.get(f"{JUP}/order").mock(return_value=quote())
    execute = mock.post(f"{JUP}/execute")
    r = buy(BONK, 0.01, make_config(DRY_RUN="false", JUPITER_API_KEY="jup"))
    assert r["mode"] == "dry_run" and "SOLANA_PRIVATE_KEY is not set" in r["message"]
    assert execute.call_count == 0


def test_live_buy_orders_with_taker_then_executes_signed_tx(mock):
    wallet = Keypair()
    unsigned = unsigned_transaction(wallet)
    ok_checks(mock)
    order = mock.get(f"{JUP}/order").mock(return_value=quote(transaction=unsigned))
    execute = mock.post(f"{JUP}/execute").mock(
        return_value=httpx.Response(200, json={"status": "Success", "signature": "SIG123", "code": 0})
    )
    r = buy(BONK, 0.01, live(wallet))
    assert r["status"] == "executed", r["message"]
    assert (r["signature"], r["explorerUrl"]) == ("SIG123", "https://solscan.io/tx/SIG123")
    params = order.calls.last.request.url.params
    assert (params["taker"], params["inputMint"], params["outputMint"]) == (str(wallet.pubkey()), SOL, BONK)
    assert order.calls.last.request.headers["x-api-key"] == "jup"

    body = json.loads(execute.calls.last.request.content)
    assert body["requestId"] == "req-1" and body["signedTransaction"] != unsigned
    signed = VersionedTransaction.from_bytes(base64.b64decode(body["signedTransaction"]))
    assert signed.signatures[0].verify(wallet.pubkey(), to_bytes_versioned(signed.message))
    assert str(wallet) not in json.dumps(r)


def test_live_buy_blocked_never_reaches_jupiter(mock):
    mock.get(f"{DEXRA}/token_deep_check").mock(return_value=httpx.Response(200, json=dexra_result("avoid", ["mint authority active"])))
    mock.get(f"{DEXRA}/swap_preflight").mock(return_value=httpx.Response(200, json=dexra_result("ok")))
    order = mock.get(f"{JUP}/order")
    r = buy(BONK, 0.01, live(Keypair()))
    assert (r["status"], r["mode"]) == ("blocked", "live")
    assert order.call_count == 0


def test_live_buy_over_cap_calls_nothing(mock):
    r = buy(BONK, 0.5, live(Keypair()))
    assert r["status"] == "blocked"
    assert len(mock.calls) == 0


def test_order_without_transaction_fails_without_executing(mock):
    ok_checks(mock)
    mock.get(f"{JUP}/order").mock(return_value=quote(transaction="", errorCode=1, errorMessage="Insufficient funds"))
    execute = mock.post(f"{JUP}/execute")
    r = buy(BONK, 0.01, live(Keypair()))
    assert r["status"] == "failed" and "Insufficient funds" in r["message"]
    assert execute.call_count == 0


def test_bad_private_key_is_not_echoed(mock):
    ok_checks(mock)
    cfg = make_config(DRY_RUN="false", JUPITER_API_KEY="jup", SOLANA_PRIVATE_KEY="not0base58!")
    r = buy(BONK, 0.01, cfg)
    assert r["status"] == "error" and "not0base58!" not in r["message"]


def test_live_sell_converts_units_and_sells_on_avoid(mock):
    wallet = Keypair()
    mock.get(f"{DEXRA}/describe_address").mock(return_value=httpx.Response(200, json={"result": {"stats": {"decimals": 5}}}))
    pre = mock.get(f"{DEXRA}/swap_preflight").mock(return_value=httpx.Response(200, json=dexra_result("avoid", ["transfer fee 50%"])))
    order = mock.get(f"{JUP}/order").mock(return_value=quote(transaction=unsigned_transaction(wallet)))
    mock.post(f"{JUP}/execute").mock(
        return_value=httpx.Response(200, json={"status": "Failed", "signature": "SIGF", "code": -1000, "error": "Failed to land"})
    )
    r = sell(BONK, "250000", live(wallet))
    assert r["decision"]["allowed"] and r["decision"]["verdicts"]["preflight"] == "avoid"
    assert r["input"]["amount"] == 2.5
    assert (pre.calls.last.request.url.params["amount"], pre.calls.last.request.url.params["side"]) == ("2.5", "sell")
    params = order.calls.last.request.url.params
    assert (params["inputMint"], params["outputMint"], params["amount"]) == (BONK, SOL, "250000")
    assert r["status"] == "failed" and r["execution"]["code"] == -1000


def test_never_raises_on_invalid_config(mock, monkeypatch):
    monkeypatch.setenv("SLIPPAGE_BPS", "5000")
    r = buy(BONK, 0.01)
    assert r["status"] == "error" and "SLIPPAGE_BPS" in r["message"]
    assert sell(BONK, "abc")["status"] == "error"
    assert len(mock.calls) == 0

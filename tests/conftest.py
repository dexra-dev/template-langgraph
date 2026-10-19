import base64

import pytest
import respx
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from dexra_core.config import load_config

BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
SOL = "So11111111111111111111111111111111111111112"
DEXRA = "http://dexra.test"
JUP = "https://api.jup.ag/swap/v2"
MEMO = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")


def make_config(**env):
    return load_config({"DEXRA_BASE_URL": DEXRA, **env})


def dexra_result(verdict, reasons=(), **extra):
    return {"result": {"verdict": verdict, "reasons": list(reasons), "summary": f"summary {verdict}", **extra}}


def unsigned_transaction(payer: Keypair) -> str:
    """An unsigned v0 transaction paid by payer, base64, like the one /order returns."""
    message = MessageV0.try_compile(payer.pubkey(), [Instruction(MEMO, b"\x01\x02", [])], [], Hash.default())
    tx = VersionedTransaction.populate(message, [Signature.default()])
    return base64.b64encode(bytes(tx)).decode()


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router

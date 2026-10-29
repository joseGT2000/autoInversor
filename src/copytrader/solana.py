"""Cliente RPC de Solana (Helius) y parser de swaps a partir de cambios de balance.

Usamos RPC estándar (1 crédito/llamada) en vez de la Enhanced API (100 créditos). El parser no
depende del DEX: mira cuánto SOL/stable y cuánto token ganó o perdió la wallet en la transacción.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
STABLES = {USDC, USDT}
QUOTE_MINTS = {WSOL} | STABLES
LAMPORTS = 1_000_000_000
# Movimientos de SOL menores que esto (rent de cuentas, fees) no cuentan como pago de un swap
MIN_QUOTE_SOL = 0.001


class CreditBudgetExceeded(RuntimeError):
    pass


class SolanaRPC:
    def __init__(self, api_key: str, credit_budget: int, rps: float = 8.0, batch_size: int = 10):
        self._url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
        self._client = httpx.Client(timeout=60)
        # Helius cuenta cada llamada de un lote contra el límite de RPS: esperamos por crédito
        self._seconds_per_credit = 1 / rps
        self._next_allowed = 0.0
        self.batch_size = batch_size
        self.credit_budget = credit_budget
        self.credits_used = 0

    def _post(self, payload, credits: int):
        if self.credits_used + credits > self.credit_budget:
            raise CreditBudgetExceeded(f"{self.credits_used}/{self.credit_budget} créditos usados")
        for attempt in range(6):
            wait = self._next_allowed - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            resp = self._client.post(self._url, json=payload)
            self._next_allowed = time.monotonic() + credits * self._seconds_per_credit
            if resp.status_code != 429:
                break
            time.sleep(2 ** attempt)
        if resp.status_code >= 400:
            # Sin la URL: lleva la API key
            raise RuntimeError(f"Helius HTTP {resp.status_code}")
        self.credits_used += credits
        return resp.json()

    def call(self, method: str, params: list):
        data = self._post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, 1)
        if "error" in data:
            raise RuntimeError(f"{method}: {data['error']}")
        return data["result"]

    def signatures(self, address: str, *, limit: int = 1000, before: str | None = None,
                   until: str | None = None) -> list[dict]:
        opts = {"limit": limit}
        if before:
            opts["before"] = before
        if until:
            opts["until"] = until
        return self.call("getSignaturesForAddress", [address, opts])

    def signatures_since(self, address: str, since_ts: float, *, until: str | None = None,
                         max_pages: int = 10) -> list[dict]:
        """Firmas (más recientes primero) hasta since_ts o hasta la firma `until`."""
        out, before = [], None
        for _ in range(max_pages):
            page = self.signatures(address, before=before, until=until)
            out.extend(s for s in page if (s.get("blockTime") or 0) >= since_ts)
            if len(page) < 1000 or (page[-1].get("blockTime") or 0) < since_ts:
                break
            before = page[-1]["signature"]
        return out

    def transactions(self, signatures: list[str]) -> list[dict | None]:
        out = []
        opts = {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}
        for i in range(0, len(signatures), self.batch_size):
            chunk = signatures[i:i + self.batch_size]
            batch = [{"jsonrpc": "2.0", "id": j, "method": "getTransaction", "params": [sig, opts]}
                     for j, sig in enumerate(chunk)]
            results = sorted(self._post(batch, len(chunk)), key=lambda r: r["id"])
            out.extend(r.get("result") for r in results)
        return out


@dataclass(frozen=True)
class Swap:
    signature: str
    wallet: str
    block_time: datetime
    side: str           # buy | sell (del token, contra SOL/stable)
    token_mint: str
    token_amount: float
    sol_amount: float   # valor en SOL (stables convertidos con sol_usd)
    post_balance: float | None = None  # saldo del token tras el swap (para replicar ventas parciales)


def _token_deltas(meta: dict, owner: str) -> dict[str, float]:
    deltas: dict[str, float] = {}
    for sign, key in ((-1, "preTokenBalances"), (1, "postTokenBalances")):
        for b in meta.get(key) or []:
            if b.get("owner") == owner:
                amount = float(b["uiTokenAmount"]["uiAmountString"] or 0)
                deltas[b["mint"]] = deltas.get(b["mint"], 0.0) + sign * amount
    return deltas


def parse_swap(tx: dict, wallet: str, sol_usd: float) -> Swap | None:
    """Devuelve el swap token<->SOL/stable que hizo `wallet` en `tx`, o None si no es eso."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return None
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
    if wallet not in keys:
        return None
    idx = keys.index(wallet)

    deltas = _token_deltas(meta, wallet)
    quote_sol = (meta["postBalances"][idx] - meta["preBalances"][idx]) / LAMPORTS
    quote_sol += deltas.pop(WSOL, 0.0)
    for mint in STABLES:
        quote_sol += deltas.pop(mint, 0.0) / sol_usd

    tokens = {m: d for m, d in deltas.items() if abs(d) > 0}
    if len(tokens) != 1 or abs(quote_sol) < MIN_QUOTE_SOL:
        return None  # transferencia, airdrop, token->token, LP... no es un swap simple
    mint, amount = next(iter(tokens.items()))
    if amount > 0 and quote_sol < 0:
        side = "buy"
    elif amount < 0 and quote_sol > 0:
        side = "sell"
    else:
        return None

    return Swap(
        signature=tx["transaction"]["signatures"][0],
        wallet=wallet,
        block_time=datetime.fromtimestamp(tx["blockTime"], tz=timezone.utc),
        side=side,
        token_mint=mint,
        token_amount=abs(amount),
        sol_amount=abs(quote_sol),
        post_balance=sum(float(b["uiTokenAmount"]["uiAmountString"] or 0) for b in meta.get("postTokenBalances") or []
                         if b.get("owner") == wallet and b["mint"] == mint),
    )

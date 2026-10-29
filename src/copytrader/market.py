"""Datos de mercado gratuitos y sin API key: Jupiter (tokens/precios) y DexScreener (pools)."""
import time

import httpx

from copytrader.solana import WSOL

JUP = "https://lite-api.jup.ag"
DEXSCREENER = "https://api.dexscreener.com"

_client = httpx.Client(timeout=30)


def sol_usd() -> float:
    data = _client.get(f"{JUP}/price/v3", params={"ids": WSOL}).raise_for_status().json()
    return float(data[WSOL]["usdPrice"])


def trending_tokens(interval: str = "24h", limit: int = 50) -> list[dict]:
    """Tokens en tendencia según Jupiter. Incluye liquidity, organicScore, audit, holderCount..."""
    url = f"{JUP}/tokens/v2/toptrending/{interval}"
    return _client.get(url, params={"limit": limit}).raise_for_status().json()


def birdeye_top_traders(api_key: str, limit: int = 50, min_trades: int = 10) -> list[str]:
    """Wallets con más PnL realizado de la última semana en Solana (Birdeye, plan gratis: 1 req/s, 10 por página).

    Se ordena por PnL realizado: el PnL total está dominado por valoraciones no realizadas de tokens sin liquidez.
    """
    out = []
    for offset in range(0, limit, 10):
        resp = _client.get(
            "https://public-api.birdeye.so/trader/gainers-losers",
            params={"type": "1W", "sort_by": "realized_pnl", "sort_type": "desc", "offset": offset, "limit": 10},
            headers={"X-API-KEY": api_key, "x-chain": "solana", "accept": "application/json"},
        ).raise_for_status()
        items = (resp.json().get("data") or {}).get("items") or []
        out.extend(i["address"] for i in items if i.get("address") and (i.get("trade_count") or 0) >= min_trades)
        if len(items) < 10:
            break
        time.sleep(1.1)
    return out


def main_pool(mint: str) -> str | None:
    pairs = _client.get(f"{DEXSCREENER}/token-pairs/v1/solana/{mint}").raise_for_status().json()
    if not pairs:
        return None
    return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)["pairAddress"]

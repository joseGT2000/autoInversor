"""Datos de mercado gratuitos: Jupiter (tokens/precios/cotizaciones), DexScreener (pools) y Birdeye (traders)."""
import time

import httpx

from copytrader.solana import WSOL

JUP = "https://lite-api.jup.ag"
DEXSCREENER = "https://api.dexscreener.com"
EURC = "HzwqbKZw8HxMN6bF2yFZNrht3c2iXXzpKcFu7uBEDKtr"

_client = httpx.Client(timeout=30)


def _get(url: str, **kwargs) -> httpx.Response:
    """GET con reintentos ante 429 (lite-api de Jupiter tiene un límite de peticiones bajo)."""
    for attempt in range(5):
        resp = _client.get(url, **kwargs)
        if resp.status_code != 429:
            return resp
        time.sleep(2 ** attempt)
    return resp


def sol_usd() -> float:
    return prices([WSOL])[WSOL]["usdPrice"]


def prices(mints: list[str]) -> dict[str, dict]:
    """{mint: {"usdPrice", "decimals", "liquidity"}} de Jupiter. Los mints sin precio no aparecen."""
    out = {}
    unique = list(dict.fromkeys(mints))
    for i in range(0, len(unique), 50):
        data = _get(f"{JUP}/price/v3", params={"ids": ",".join(unique[i:i + 50])}).raise_for_status().json()
        out.update({m: v for m, v in data.items() if v})
    return out


def quote(input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict | None:
    """Cotización real de Jupiter (ruta, outAmount, priceImpactPct). None si no hay ruta posible."""
    resp = _get(f"{JUP}/swap/v1/quote", params={
        "inputMint": input_mint, "outputMint": output_mint, "amount": amount_raw, "slippageBps": slippage_bps})
    if resp.status_code == 400:
        return None
    return resp.raise_for_status().json()


def token_info(mint: str) -> dict | None:
    """Mismo formato que trending_tokens (liquidity, audit, mintAuthority...)."""
    found = _get(f"{JUP}/tokens/v2/search", params={"query": mint}).raise_for_status().json()
    return next((t for t in found if t["id"] == mint), None)


def trending_tokens(interval: str = "24h", limit: int = 50) -> list[dict]:
    """Tokens en tendencia según Jupiter. Incluye liquidity, organicScore, audit, holderCount..."""
    url = f"{JUP}/tokens/v2/toptrending/{interval}"
    return _get(url, params={"limit": limit}).raise_for_status().json()


def birdeye_top_traders(api_key: str, limit: int = 50, min_trades: int = 10) -> list[str]:
    """Wallets con más PnL realizado de la última semana en Solana (Birdeye, plan gratis: 1 req/s, 10 por página).

    Se ordena por PnL realizado: el PnL total está dominado por valoraciones no realizadas de tokens sin liquidez.
    """
    out = []
    for offset in range(0, limit, 10):
        resp = _get(
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
    pairs = _get(f"{DEXSCREENER}/token-pairs/v1/solana/{mint}").raise_for_status().json()
    if not pairs:
        return None
    return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)["pairAddress"]

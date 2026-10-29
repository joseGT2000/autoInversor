"""Mantiene el webhook "raw" de Helius apuntando a la Edge Function con las wallets vigiladas.

Vigiladas = top N actual + wallets de las que tenemos posiciones (para copiar sus ventas).
Solo se llama a la API cuando la lista cambia: cada creación/edición cuesta 100 créditos.
"""
import logging

import httpx

from copytrader.config import Config

log = logging.getLogger(__name__)
API = "https://api-mainnet.helius-rpc.com/v0/webhooks"


def watched_wallets(conn, top_n: int) -> list[str]:
    rows = conn.execute(
        """select address from wallet_scores
           where day = (select max(day) from wallet_scores) and rank <= %s
           union
           select distinct source_wallet from positions""", (top_n,)).fetchall()
    return sorted(r["address"] for r in rows)


def sync(conn, cfg: Config) -> None:
    if not (cfg.webhook_url and cfg.webhook_secret):
        log.info("webhook: WEBHOOK_URL/WEBHOOK_SECRET sin configurar, se omite")
        return
    addresses = watched_wallets(conn, cfg.portfolio.top_n)
    state = conn.execute("select webhook_id, addresses from webhook_state where id = 1").fetchone()
    if state and sorted(state["addresses"]) == addresses:
        return
    if not addresses:
        log.info("webhook: no hay wallets que vigilar todavía")
        return

    params = {"api-key": cfg.helius_api_key}
    body = {"webhookURL": cfg.webhook_url, "transactionTypes": ["ANY"], "accountAddresses": addresses,
            "webhookType": "raw", "txnStatus": "success", "authHeader": cfg.webhook_secret}
    with httpx.Client(timeout=30) as client:
        webhook_id = state["webhook_id"] if state else None
        if webhook_id is None:
            existing = client.get(API, params=params)
            _check(existing)
            webhook_id = next((h["webhookID"] for h in existing.json() if h["webhookURL"] == cfg.webhook_url), None)
        resp = (client.put(f"{API}/{webhook_id}", params=params, json=body) if webhook_id
                else client.post(API, params=params, json=body))
        _check(resp)
        webhook_id = resp.json()["webhookID"]

    conn.execute(
        """insert into webhook_state (id, webhook_id, addresses) values (1, %s, %s)
           on conflict (id) do update set webhook_id = excluded.webhook_id, addresses = excluded.addresses,
               updated_at = now()""", (webhook_id, addresses))
    log.info("webhook %s: vigilando %d wallets", webhook_id, len(addresses))


def _check(resp: httpx.Response) -> None:
    # Sin la URL en el error: lleva la API key
    if resp.status_code >= 400:
        raise RuntimeError(f"Helius webhooks HTTP {resp.status_code}: {resp.text[:200]}")

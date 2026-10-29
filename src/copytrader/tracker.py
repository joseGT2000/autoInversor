"""Módulo 1 + 4: descubre y puntúa wallets, incorpora el feedback de la simulación. Diario.

1. Descubrimiento: wallets que han hecho swaps en varios tokens trending (Jupiter + DexScreener)
   más las `manual_wallets` de config.yaml.
2. Escaneo: historial de swaps vía Helius RPC, incremental (solo firmas nuevas) y con
   presupuesto de créditos por ejecución.
3. Score: métricas sobre los swaps guardados + feedback del rendimiento de copiarlas.
"""
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from copytrader import market, webhook
from copytrader.config import Config
from copytrader.db import connect
from copytrader.scoring import (PERMANENT_EXCLUSIONS, compute_metrics, copy_score, exclusion_reason,
                                final_score, onchain_score)
from copytrader.solana import QUOTE_MINTS, CreditBudgetExceeded, SolanaRPC, Swap, parse_swap

log = logging.getLogger(__name__)


def run(cfg: Config) -> None:
    tc = cfg.tracker
    rpc = SolanaRPC(cfg.helius_api_key, credit_budget=tc.helius_credit_budget)
    sol_usd = market.sol_usd()
    with connect(cfg.database_url) as conn:
        try:
            candidates = discover(conn, rpc, cfg, sol_usd)
            upsert_wallets(conn, candidates, "trending")
            if cfg.birdeye_api_key:
                top = market.birdeye_top_traders(cfg.birdeye_api_key, tc.birdeye_top_traders, tc.min_trades_30d // 2)
                log.info("birdeye: %d candidatas", len(top))
                upsert_wallets(conn, top, "birdeye")
            upsert_wallets(conn, tc.manual_wallets, "manual")
            conn.commit()
            for address in wallets_to_scan(conn, cfg):
                scan_wallet(conn, rpc, address, cfg, sol_usd)
                conn.commit()
        except CreditBudgetExceeded as e:
            log.warning("presupuesto de Helius agotado, se puntúa con lo escaneado: %s", e)
            conn.rollback()
        ranking = score_all(conn, cfg)
        conn.commit()
        webhook.sync(conn, cfg)
    log.info("créditos Helius usados: %d", rpc.credits_used)
    for row in ranking[: cfg.portfolio.top_n]:
        log.info("top %d  %s  score=%.3f", row["rank"], row["address"], row["final_score"])


# --- 1. Descubrimiento -------------------------------------------------------------------------

def discover(conn, rpc: SolanaRPC, cfg: Config, sol_usd: float) -> list[str]:
    tc = cfg.tracker
    tokens = [t for t in market.trending_tokens(tc.trending_interval, tc.trending_tokens)
              if t["id"] not in QUOTE_MINTS and (t.get("liquidity") or 0) >= tc.min_token_liquidity_usd]
    seen: dict[str, set[str]] = defaultdict(set)
    for token in tokens:
        save_token_risk(conn, token)
        pool = market.main_pool(token["id"])
        if not pool:
            continue
        # 1000 firmas cuestan 1 crédito: muestreamos repartido en el tiempo, no solo lo último
        # (los últimos swaps de un pool trending son casi todos bots de volumen)
        sigs = [s["signature"] for s in rpc.signatures(pool, limit=1000) if not s["err"]]
        step = max(len(sigs) // tc.pool_tx_sample, 1)
        for tx in rpc.transactions(sigs[::step][: tc.pool_tx_sample]):
            if not tx:
                continue
            signer = _fee_payer(tx)
            swap = parse_swap(tx, signer, sol_usd)
            if swap and swap.token_mint == token["id"] and swap.sol_amount >= tc.min_discovery_swap_sol:
                seen[signer].add(token["id"])
    found = [w for w, toks in seen.items() if len(toks) >= tc.min_token_appearances]
    log.info("descubrimiento: %d tokens, %d traders, %d candidatas", len(tokens), len(seen), len(found))
    return found


def _fee_payer(tx: dict) -> str:
    key = tx["transaction"]["message"]["accountKeys"][0]
    return key["pubkey"] if isinstance(key, dict) else key


def save_token_risk(conn, token: dict) -> None:
    audit = token.get("audit") or {}
    conn.execute(
        """insert into token_risk (token_mint, liquidity_usd, mint_authority, freeze_authority,
                                   top10_holders_pct, raw)
           values (%s, %s, %s, %s, %s, %s)
           on conflict (token_mint) do update set checked_at = now(),
               liquidity_usd = excluded.liquidity_usd, mint_authority = excluded.mint_authority,
               freeze_authority = excluded.freeze_authority,
               top10_holders_pct = excluded.top10_holders_pct, raw = excluded.raw""",
        (token["id"], token.get("liquidity"), token.get("mintAuthority") is not None,
         not audit.get("freezeAuthorityDisabled", False), audit.get("topHoldersPercentage"),
         json.dumps({k: token.get(k) for k in ("symbol", "organicScore", "holderCount", "mcap", "audit")})),
    )


def upsert_wallets(conn, addresses, source: str) -> None:
    for address in addresses:
        conn.execute("insert into wallets (address, source) values (%s, %s) on conflict do nothing",
                     (address, source))


# --- 2. Escaneo ---------------------------------------------------------------------------------

def wallets_to_scan(conn, cfg: Config) -> list[str]:
    """Prioridad: top actual y manuales, luego nunca escaneadas, luego las más antiguas."""
    rows = conn.execute(
        """select w.address
           from wallets w
           left join wallet_scores s on s.address = w.address
                and s.day = (select max(day) from wallet_scores)
           where not w.excluded
           order by (s.rank is not null and s.rank <= %s) desc,
                    (w.source in ('manual', 'birdeye')) desc,
                    w.last_scanned_at asc nulls first
           limit %s""",
        (cfg.portfolio.top_n, cfg.tracker.max_wallets_scanned_per_run),
    ).fetchall()
    return [r["address"] for r in rows]


def scan_wallet(conn, rpc: SolanaRPC, address: str, cfg: Config, sol_usd: float) -> None:
    tc = cfg.tracker
    state = conn.execute("select last_signature, first_activity from wallets where address = %s",
                         (address,)).fetchone()
    since = datetime.now(timezone.utc) - timedelta(days=tc.lookback_days)
    sigs = rpc.signatures_since(address, since.timestamp(), until=state["last_signature"])
    if not sigs:
        conn.execute("update wallets set last_scanned_at = now() where address = %s", (address,))
        return

    # Filtro barato antes de descargar transacciones: firmas por día
    per_day = defaultdict(int)
    for s in sigs:
        per_day[datetime.fromtimestamp(s["blockTime"], timezone.utc).date()] += 1
    if max(per_day.values()) > tc.max_trades_per_day * 1.5:
        conn.execute("update wallets set excluded = true, exclude_reason = 'bot', last_scanned_at = now() "
                     "where address = %s", (address,))
        return

    ok = [s["signature"] for s in sigs if not s["err"]][: tc.max_tx_per_wallet]
    swaps = [sw for tx in rpc.transactions(ok) if tx and (sw := parse_swap(tx, address, sol_usd))]
    for sw in swaps:
        conn.execute(
            """insert into wallet_trades (signature, address, block_time, side, token_mint,
                                          token_amount, sol_amount)
               values (%s, %s, %s, %s, %s, %s, %s) on conflict do nothing""",
            (sw.signature, sw.wallet, sw.block_time, sw.side, sw.token_mint, sw.token_amount, sw.sol_amount),
        )
    oldest = datetime.fromtimestamp(sigs[-1]["blockTime"], timezone.utc)
    conn.execute(
        """update wallets set last_signature = %s, last_scanned_at = now(),
               first_activity = least(coalesce(first_activity, %s), %s)
           where address = %s""",
        (sigs[0]["signature"], oldest, oldest, address),
    )
    log.info("escaneada %s: %d firmas, %d swaps", address, len(sigs), len(swaps))


# --- 3. Score + feedback ------------------------------------------------------------------------

def score_all(conn, cfg: Config) -> list[dict]:
    tc = cfg.tracker
    since = datetime.now(timezone.utc) - timedelta(days=tc.lookback_days)
    trades = defaultdict(list)
    for r in conn.execute(
        """select t.* from wallet_trades t join wallets w using (address)
           where not w.excluded and t.block_time >= %s""", (since,)):
        trades[r["address"]].append(Swap(r["signature"], r["address"], r["block_time"], r["side"],
                                         r["token_mint"], float(r["token_amount"]), float(r["sol_amount"])))
    first_activity = {r["address"]: r["first_activity"]
                      for r in conn.execute("select address, first_activity from wallets")}
    copy_rois = copy_performance(conn, since, tc.min_copy_closed)

    rows = []
    for address, swaps in trades.items():
        m = compute_metrics(swaps)
        if first_activity.get(address):
            m.history_days = max(m.history_days,
                                 (datetime.now(timezone.utc) - first_activity[address]).total_seconds() / 86400)
        reason = exclusion_reason(m, tc)
        if reason in PERMANENT_EXCLUSIONS:
            conn.execute("update wallets set excluded = true, exclude_reason = %s where address = %s",
                         (reason, address))
        onchain = onchain_score(m, tc)
        copy = copy_score(copy_rois.get(address))
        rows.append({"address": address, "onchain_score": onchain, "copy_score": copy,
                     "final_score": final_score(onchain, copy, tc) if reason is None else 0.0,
                     "metrics": {**m.to_dict(), "exclusion": reason}, "eligible": reason is None})

    eligible = sorted((r for r in rows if r["eligible"]), key=lambda r: r["final_score"], reverse=True)
    for i, r in enumerate(eligible, start=1):
        r["rank"] = i
    today = date.today()
    for r in rows:
        conn.execute(
            """insert into wallet_scores (address, day, onchain_score, copy_score, final_score, rank, metrics)
               values (%s, %s, %s, %s, %s, %s, %s)
               on conflict (address, day) do update set onchain_score = excluded.onchain_score,
                   copy_score = excluded.copy_score, final_score = excluded.final_score,
                   rank = excluded.rank, metrics = excluded.metrics""",
            (r["address"], today, r["onchain_score"], r["copy_score"], r["final_score"], r.get("rank"),
             json.dumps(r["metrics"])),
        )
    log.info("puntuadas %d wallets, %d elegibles", len(rows), len(eligible))
    return eligible


def copy_performance(conn, since: datetime, min_closed: int) -> dict[str, float]:
    """ROI realizado de nuestras copias simuladas por wallet de origen (feedback)."""
    sims = defaultdict(list)
    for r in conn.execute(
        """select id, source_wallet, executed_at, side, token_mint, token_amount, value
           from sim_trades where status = 'filled' and executed_at >= %s""", (since,)):
        sims[r["source_wallet"]].append(Swap(str(r["id"]), r["source_wallet"], r["executed_at"], r["side"],
                                             r["token_mint"], float(r["token_amount"]), float(r["value"])))
    out = {}
    for wallet, swaps in sims.items():
        m = compute_metrics(swaps)
        if m.n_closed >= min_closed:
            out[wallet] = m.roi
    return out

"""Exporta el estado de la BD a site/data.json para el dashboard de GitHub Pages.

La web es pública y estática: nunca accede a Supabase, solo lee este JSON que genera Actions.
"""
import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from copytrader import market
from copytrader.config import Config
from copytrader.db import connect
from copytrader.solana import WSOL

log = logging.getLogger(__name__)
OUT = Path("site/data.json")


def _default(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    raise TypeError(type(v))


def run(cfg: Config) -> None:
    px = market.prices([WSOL, market.EURC])
    usd_to_base = 1 / px[market.EURC]["usdPrice"] if cfg.portfolio.currency == "EUR" else 1.0

    with connect(cfg.database_url) as conn:
        flow = conn.execute(
            """select coalesce(sum(case when side = 'buy' then -(value + fee) else value - fee end), 0) as f
               from sim_trades where status = 'filled'""").fetchone()["f"]
        cash = cfg.portfolio.initial_capital + float(flow)

        positions = conn.execute(
            """select p.token_mint, p.source_wallet, p.token_amount, p.cost_basis, p.opened_at,
                      r.raw->>'symbol' as symbol, r.liquidity_usd
               from positions p left join token_risk r using (token_mint) order by p.opened_at desc""").fetchall()
        prices = market.prices([p["token_mint"] for p in positions]) if positions else {}
        for p in positions:
            price = prices.get(p["token_mint"])
            p["value"] = float(p["token_amount"]) * price["usdPrice"] * usd_to_base if price else 0.0
            p["pnl"] = p["value"] - float(p["cost_basis"])
        equity = cash + sum(p["value"] for p in positions)

        trades = conn.execute(
            """select t.executed_at, t.side, t.status, t.token_mint, r.raw->>'symbol' as symbol, t.source_wallet,
                      t.value, t.fee, t.price_impact_pct, t.delay_s, t.allocator_reason, t.source_signature
               from sim_trades t left join token_risk r using (token_mint)
               order by t.executed_at desc limit 300""").fetchall()

        ranking = conn.execute(
            """select s.address, w.source, s.rank, s.final_score, s.onchain_score, s.copy_score, s.metrics
               from wallet_scores s join wallets w using (address)
               where s.day = (select max(day) from wallet_scores)
               order by s.rank nulls last, s.final_score desc""").fetchall()

        audits = conn.execute(
            "select day, equity, metrics, proposals, report_md from audit_reports order by day desc limit 90").fetchall()

        wallets = conn.execute(
            """select source, coalesce(exclude_reason, 'activa') as estado, count(*) as n
               from wallets group by 1, 2 order by 1, 2""").fetchall()
        webhook = conn.execute("select addresses, updated_at from webhook_state where id = 1").fetchone()
        settings = conn.execute("select config from settings where id = 1").fetchone()

    now = datetime.now(timezone.utc)
    # Curva: capital inicial en el primer dato conocido, cierre diario de cada auditoría y valor actual
    firsts = [t for t in (audits[-1]["day"] if audits else None, trades[-1]["executed_at"] if trades else None) if t]
    start = min(firsts, key=lambda t: t if isinstance(t, datetime) else datetime.combine(t, datetime.min.time(), timezone.utc)) if firsts else now
    curve = [{"t": start, "equity": cfg.portfolio.initial_capital},
             *({"t": a["day"], "equity": a["equity"]} for a in reversed(audits)),
             {"t": now, "equity": equity}]
    data = {
        "generated_at": now,
        "currency": cfg.portfolio.currency,
        "initial_capital": cfg.portfolio.initial_capital,
        "portfolio": {"equity": equity, "cash": cash, "positions_value": equity - cash,
                      "return": equity / cfg.portfolio.initial_capital - 1},
        "equity_curve": curve,
        "positions": positions,
        "trades": trades,
        "ranking": ranking,
        "audits": audits,
        "wallets": wallets,
        "webhook": webhook,
        "config": settings["config"] if settings else cfg.model_dump(mode="json"),
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, default=_default, ensure_ascii=False), encoding="utf-8")
    log.info("export: %s (%d operaciones, %d posiciones, %d wallets en ranking)",
             OUT, len(trades), len(positions), len(ranking))

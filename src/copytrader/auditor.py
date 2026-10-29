"""Módulo 3: audita todas las operaciones simuladas y propone mejoras. Diario.

Unidad de análisis: la "ronda" = todo lo que hicimos con un token copiando a una wallet
(compras, ventas y lo que queda abierto valorado a precio de Jupiter). Así cada euro de PnL
se atribuye a una wallet, a la regla del allocator que abrió la ronda y al riesgo del token.
"""
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from copytrader import market
from copytrader.config import Config
from copytrader.db import connect
from copytrader.solana import WSOL

log = logging.getLogger(__name__)
REPORTS_DIR = Path("reports")
MIN_SAMPLE = 5  # rondas mínimas para proponer cambios sobre un grupo


@dataclass
class Round:
    wallet: str
    token: str
    entry_reason: str
    cost: float = 0.0        # compras + fees
    proceeds: float = 0.0    # ventas - fees
    open_value: float = 0.0  # lo que queda, a precio de mercado
    risk: dict = field(default_factory=dict)

    @property
    def pnl(self) -> float:
        return self.proceeds + self.open_value - self.cost

    @property
    def closed(self) -> bool:
        return self.open_value == 0


def risk_bucket(risk: dict) -> str:
    if not risk:
        return "desconocido"
    liq = risk.get("liquidity_usd") or 0
    top10 = risk.get("top10_holders_pct") or 0
    if risk.get("mint_authority") or risk.get("freeze_authority") or top10 > 50 or liq < 50_000:
        return "alto"
    if liq < 250_000 or top10 > 30:
        return "medio"
    return "bajo"


def group_stats(rounds: list[Round], key) -> dict[str, dict]:
    groups = defaultdict(list)
    for r in rounds:
        groups[key(r)].append(r)
    out = {}
    for k, rs in groups.items():
        cost = sum(r.cost for r in rs)
        pnl = sum(r.pnl for r in rs)
        closed = [r for r in rs if r.closed]
        out[k] = {"n": len(rs), "pnl": round(pnl, 2), "roi": round(pnl / cost, 4) if cost else 0.0,
                  "win_rate": round(sum(r.pnl > 0 for r in closed) / len(closed), 2) if closed else None}
    return dict(sorted(out.items(), key=lambda kv: kv[1]["pnl"], reverse=True))


def max_drawdown(equity: list[float]) -> float:
    peak, dd = float("-inf"), 0.0
    for e in equity:
        peak = max(peak, e)
        dd = max(dd, (peak - e) / peak if peak > 0 else 0.0)
    return dd


def proposals(by_reason: dict, by_risk: dict, by_wallet: dict, trades: dict, cfg: Config) -> list[str]:
    """Reglas simples y explicables. Solo se proponen con muestra suficiente."""
    out = []
    fm = by_reason.get("first_mover")
    if fm and fm["n"] >= MIN_SAMPLE and fm["roi"] < 0:
        out.append(f"Las entradas `first_mover` pierden ({fm['roi']:.0%} en {fm['n']} rondas): bajar "
                   f"`allocator.first_mover_pct` de {cfg.allocator.first_mover_pct} a "
                   f"{round((cfg.allocator.first_mover_pct or 0) * 0.5, 2)}.")
    high = by_risk.get("alto")
    if high and high["n"] >= MIN_SAMPLE and high["roi"] < -0.2:
        out.append(f"Tokens de riesgo alto: ROI {high['roi']:.0%} en {high['n']} rondas. Valorar filtrar "
                   "tokens con liquidez < 50k $, mint/freeze authority activa o top10 > 50 %.")
    if trades["buys"] >= MIN_SAMPLE and trades["failed"] / trades["buys"] > 0.2:
        out.append(f"{trades['failed']} de {trades['buys']} copias fallaron por falta de ruta en Jupiter: "
                   "subir `tracker.min_token_liquidity_usd` o añadir un filtro de liquidez al copier.")
    for wallet, s in by_wallet.items():
        if s["n"] >= MIN_SAMPLE and s["roi"] < -0.3:
            out.append(f"Copiar a `{wallet}` pierde {s['roi']:.0%} en {s['n']} rondas; el feedback del tracker "
                       "ya la penaliza, valorar sacarla a mano si sigue en el top.")
    if trades["avg_delay_s"] and trades["avg_delay_s"] > 900:
        out.append(f"Retraso medio de copia {trades['avg_delay_s'] / 60:.0f} min: el cron de 15 min se queda "
                   "corto; valorar webhooks de Helius para copiar en tiempo real.")
    if not out:
        out.append("Sin cambios propuestos: todavía no hay muestra suficiente o los resultados no lo justifican.")
    return out


def load_rounds(conn, sol_eur_per_usd: float) -> list[Round]:
    rounds: dict[tuple[str, str], Round] = {}
    for t in conn.execute(
        """select source_wallet, token_mint, side, value, fee, allocator_reason
           from sim_trades where status = 'filled' order by executed_at"""):
        key = (t["source_wallet"], t["token_mint"])
        r = rounds.setdefault(key, Round(*key, entry_reason=(t["allocator_reason"] or "?").split(":")[0]))
        if t["side"] == "buy":
            r.cost += float(t["value"] + t["fee"])
        else:
            r.proceeds += float(t["value"] - t["fee"])

    positions = conn.execute("select token_mint, source_wallet, token_amount, cost_basis from positions").fetchall()
    px = market.prices([p["token_mint"] for p in positions]) if positions else {}
    for p in positions:
        r = rounds.get((p["source_wallet"], p["token_mint"]))
        if r:
            price = px.get(p["token_mint"])
            r.open_value = (float(p["token_amount"]) * price["usdPrice"] * sol_eur_per_usd if price
                            else 0.0)  # sin precio = sin liquidez: se valora a 0 (conservador)

    risks = {r["token_mint"]: r for r in conn.execute(
        "select token_mint, liquidity_usd, mint_authority, freeze_authority, top10_holders_pct from token_risk")}
    for r in rounds.values():
        risk = risks.get(r.token)
        r.risk = {k: float(v) if isinstance(v, Decimal) else v for k, v in risk.items()} if risk else {}
    return list(rounds.values())


def trade_counts(conn) -> dict:
    row = conn.execute(
        """select count(*) filter (where side = 'buy') as buys,
                  count(*) filter (where side = 'buy' and status = 'filled') as filled,
                  count(*) filter (where status = 'failed') as failed,
                  count(*) filter (where status = 'skipped') as skipped,
                  coalesce(sum(fee) filter (where status = 'filled'), 0) as fees,
                  avg(delay_s) filter (where status = 'filled') as avg_delay_s,
                  avg(price_impact_pct) filter (where status = 'filled') as avg_impact
           from sim_trades""").fetchone()
    return {k: float(v) if v is not None else None for k, v in row.items()}


def skip_reasons(conn) -> dict[str, int]:
    return {r["allocator_reason"]: r["n"] for r in conn.execute(
        """select allocator_reason, count(*) as n from sim_trades where status = 'skipped'
           group by 1 order by 2 desc""")}


def render(day: date, cfg: Config, equity: float, cash: float, dd: float, trades: dict, by_wallet: dict,
           by_reason: dict, by_risk: dict, skips: dict, props: list[str]) -> str:
    cur = cfg.portfolio.currency
    ret = equity / cfg.portfolio.initial_capital - 1

    def pct(v: float | None) -> str:
        return "-" if v is None else f"{v:.0%}"

    def table(title: str, stats: dict) -> str:
        if not stats:
            return f"### {title}\n\nSin datos.\n"
        rows = "\n".join(f"| `{k}` | {s['n']} | {s['pnl']:+.2f} | {s['roi']:+.1%} | {pct(s['win_rate'])} |"
                         for k, s in stats.items())
        return f"### {title}\n\n| | Rondas | PnL ({cur}) | ROI | Win-rate |\n|---|---|---|---|---|\n{rows}\n"

    delay = f"{trades['avg_delay_s'] / 60:.1f} min" if trades["avg_delay_s"] else "-"
    impact = f"{trades['avg_impact']:.2%}" if trades["avg_impact"] is not None else "-"
    return f"""# Auditoría {day.isoformat()}

**Cartera: {equity:.2f} {cur}** ({ret:+.1%} sobre {cfg.portfolio.initial_capital:.0f} {cur}) · cash {cash:.2f} · drawdown máx {dd:.1%}

Compras intentadas {trades['buys']:.0f} · ejecutadas {trades['filled']:.0f} · fallidas {trades['failed']:.0f} · omitidas {trades['skipped']:.0f}
Fees {trades['fees']:.2f} {cur} · retraso medio {delay} · price impact medio {impact}

## Propuestas

{chr(10).join(f'- {p}' for p in props)}

## Desglose

{table('Por wallet', by_wallet)}
{table('Por regla de entrada del allocator', by_reason)}
{table('Por riesgo del token', by_risk)}
### Omitidas por motivo

{chr(10).join(f'- `{k}`: {v}' for k, v in skips.items()) or 'Ninguna.'}
"""


def run(cfg: Config) -> None:
    px = market.prices([WSOL, market.EURC])
    eur_usd = px[market.EURC]["usdPrice"] if cfg.portfolio.currency == "EUR" else 1.0
    today = date.today()

    with connect(cfg.database_url) as conn:
        rounds = load_rounds(conn, 1 / eur_usd)
        trades = trade_counts(conn)
        flow = conn.execute(
            """select coalesce(sum(case when side = 'buy' then -(value + fee) else value - fee end), 0) as f
               from sim_trades where status = 'filled'""").fetchone()["f"]
        cash = cfg.portfolio.initial_capital + float(flow)
        equity = cash + sum(r.open_value for r in rounds)
        history = [float(r["equity"]) for r in conn.execute(
            "select equity from audit_reports where day < %s order by day", (today,))]
        dd = max_drawdown([cfg.portfolio.initial_capital, *history, equity])

        by_wallet = group_stats(rounds, lambda r: r.wallet)
        by_reason = group_stats(rounds, lambda r: r.entry_reason)
        by_risk = group_stats(rounds, lambda r: risk_bucket(r.risk))
        props = proposals(by_reason, by_risk, by_wallet, trades, cfg)
        report = render(today, cfg, equity, cash, dd, trades, by_wallet, by_reason, by_risk,
                        skip_reasons(conn), props)

        metrics = {"cash": cash, "max_drawdown": dd, "trades": trades, "by_wallet": by_wallet,
                   "by_reason": by_reason, "by_risk": by_risk}
        conn.execute(
            """insert into audit_reports (day, equity, metrics, proposals, report_md) values (%s, %s, %s, %s, %s)
               on conflict (day) do update set equity = excluded.equity, metrics = excluded.metrics,
                   proposals = excluded.proposals, report_md = excluded.report_md""",
            (today, equity, json.dumps(metrics), json.dumps(props), report))

    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"audit-{today.isoformat()}.md"
    path.write_text(report, encoding="utf-8")
    log.info("auditoría: cartera %.2f %s, %d rondas, informe en %s", equity, cfg.portfolio.currency, len(rounds), path)

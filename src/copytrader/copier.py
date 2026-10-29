"""Módulo 2: detecta swaps nuevos del top N y los copia en simulación. Cada 15 min.

Compras: el allocator decide el tamaño y se simula con una cotización real de Jupiter en el
momento de la detección (precio que obtendríamos nosotros, no el del whale). Ventas: se vende la
misma fracción que vendió la wallet, de la posición que abrimos copiándola. Si Jupiter no
encuentra ruta (rug, sin liquidez) la operación queda como `failed` y la posición sigue abierta.

Las wallets que salen del top siguen vigiladas mientras tengamos posiciones suyas (solo ventas).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from copytrader import market
from copytrader.allocator import BuySignal, PortfolioState, size_buy, size_sell
from copytrader.config import Config
from copytrader.db import connect
from copytrader.solana import LAMPORTS, WSOL, CreditBudgetExceeded, SolanaRPC, Swap, parse_swap
from copytrader.tracker import save_token_risk

log = logging.getLogger(__name__)
BASE_TX_FEE_SOL = 0.000005


@dataclass
class Context:
    cfg: Config
    sol_usd: float
    sol_eur: float          # precio de 1 SOL en la moneda base
    ranks: dict[str, int]   # wallets del top actual -> rango

    @property
    def fee(self) -> float:
        return (self.cfg.simulation.priority_fee_sol + BASE_TX_FEE_SOL) * self.sol_eur

    def haircut(self, amount: float) -> float:
        return amount * (1 - self.cfg.simulation.slippage_bps / 20_000)


def run(cfg: Config) -> None:
    rpc = SolanaRPC(cfg.helius_api_key, credit_budget=cfg.copier.helius_credit_budget)
    px = market.prices([WSOL, market.EURC])
    sol_usd = px[WSOL]["usdPrice"]
    eur_usd = px[market.EURC]["usdPrice"] if cfg.portfolio.currency == "EUR" else 1.0

    with connect(cfg.database_url) as conn:
        ctx = Context(cfg, sol_usd, sol_usd / eur_usd, top_ranks(conn, cfg.portfolio.top_n))
        watched = set(ctx.ranks) | {r["source_wallet"] for r in conn.execute("select distinct source_wallet from positions")}
        if not watched:
            log.info("copier: no hay wallets en el ranking todavía")
            return
        try:
            for wallet in watched:
                process_wallet(conn, rpc, wallet, ctx)
                conn.commit()
        except CreditBudgetExceeded as e:
            log.warning("presupuesto de Helius agotado: %s", e)
            conn.rollback()
        cash, total = portfolio_totals(conn, ctx)
    log.info("cartera: %.2f %s (cash %.2f) | créditos Helius: %d", total, cfg.portfolio.currency, cash, rpc.credits_used)


def top_ranks(conn, top_n: int) -> dict[str, int]:
    rows = conn.execute(
        """select address, rank from wallet_scores
           where day = (select max(day) from wallet_scores) and rank <= %s""", (top_n,)).fetchall()
    return {r["address"]: r["rank"] for r in rows}


def process_wallet(conn, rpc: SolanaRPC, wallet: str, ctx: Context) -> None:
    last = conn.execute("select last_signature from wallets where address = %s", (wallet,)).fetchone()
    sigs = rpc.signatures(wallet, limit=ctx.cfg.copier.signatures_per_wallet, until=last["last_signature"])
    if not sigs:
        return
    ok = [s["signature"] for s in sigs if not s["err"]]
    swaps = [sw for tx in rpc.transactions(ok) if tx and (sw := parse_swap(tx, wallet, ctx.sol_usd))]
    min_time = datetime.now(timezone.utc) - timedelta(minutes=ctx.cfg.copier.max_signal_age_min)

    for sw in sorted(swaps, key=lambda s: s.block_time):
        inserted = conn.execute(
            """insert into wallet_trades (signature, address, block_time, side, token_mint, token_amount, sol_amount)
               values (%s, %s, %s, %s, %s, %s, %s) on conflict do nothing returning signature""",
            (sw.signature, sw.wallet, sw.block_time, sw.side, sw.token_mint, sw.token_amount, sw.sol_amount),
        ).fetchone()
        if not inserted or sw.block_time < min_time:
            continue
        if sw.side == "buy" and wallet in ctx.ranks:
            copy_buy(conn, sw, ctx)
        elif sw.side == "sell":
            copy_sell(conn, sw, ctx)
    conn.execute("update wallets set last_signature = %s where address = %s", (sigs[0]["signature"], wallet))


# --- Cartera ------------------------------------------------------------------------------------

def portfolio_totals(conn, ctx: Context) -> tuple[float, float]:
    """(cash, valor total) en moneda base. Posiciones a precio de Jupiter (o a coste si no hay precio)."""
    row = conn.execute(
        """select coalesce(sum(case when side = 'buy' then -(value + fee) else value - fee end), 0) as flow
           from sim_trades where status = 'filled'""").fetchone()
    cash = ctx.cfg.portfolio.initial_capital + float(row["flow"])
    positions = conn.execute("select token_mint, token_amount, cost_basis from positions").fetchall()
    px = market.prices([p["token_mint"] for p in positions]) if positions else {}
    value = 0.0
    for p in positions:
        price = px.get(p["token_mint"])
        value += (float(p["token_amount"]) * price["usdPrice"] * ctx.sol_eur / ctx.sol_usd if price
                  else float(p["cost_basis"]))
    return cash, cash + value


def other_top_holders(conn, token: str, wallet: str, ranks: dict[str, int]) -> int:
    others = [w for w in ranks if w != wallet]
    if not others:
        return 0
    row = conn.execute(
        """select count(*) as n from (
               select address from wallet_trades
               where token_mint = %s and address = any(%s)
               group by address
               having sum(case when side = 'buy' then token_amount else -token_amount end) > 0) h""",
        (token, others)).fetchone()
    return row["n"]


# --- Copia --------------------------------------------------------------------------------------

def record(conn, sw: Swap, ctx: Context, *, side: str, status: str, token_amount: float = 0.0,
           value: float = 0.0, fee: float = 0.0, price_impact: float | None = None, reason: str | None = None):
    delay = (datetime.now(timezone.utc) - sw.block_time).total_seconds() + ctx.cfg.simulation.execution_delay_s
    conn.execute(
        """insert into sim_trades (source_signature, source_wallet, side, token_mint, token_amount, value, price,
                                   price_impact_pct, fee, delay_s, allocator_reason, status)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (sw.signature, sw.wallet, side, sw.token_mint, token_amount, value,
         value / token_amount if token_amount else 0, price_impact, fee, delay, reason, status))
    log.info("%s %s %s %.2f -> %s (%s)", status, side, sw.token_mint[:6], value, sw.wallet[:6], reason)


def copy_buy(conn, sw: Swap, ctx: Context) -> None:
    held = conn.execute("select 1 from positions where token_mint = %s and source_wallet = %s",
                        (sw.token_mint, sw.wallet)).fetchone()
    if held and not ctx.cfg.copier.copy_same_wallet_adds:
        return record(conn, sw, ctx, side="buy", status="skipped", reason="same_wallet_add")

    cash, total = portfolio_totals(conn, ctx)
    exposure = conn.execute("select coalesce(sum(cost_basis), 0) as v from positions where token_mint = %s",
                            (sw.token_mint,)).fetchone()["v"]
    alloc = size_buy(
        BuySignal(ctx.ranks[sw.wallet], sw.token_mint,
                  other_top_holders(conn, sw.token_mint, sw.wallet, ctx.ranks), ctx.fee),
        PortfolioState(total_value=total, cash=cash, token_exposure=float(exposure)),
        ctx.cfg.allocator,
    )
    if alloc.amount <= 0:
        return record(conn, sw, ctx, side="buy", status="skipped", reason=alloc.reason)

    ensure_token_risk(conn, sw.token_mint)
    lamports = int(alloc.amount / ctx.sol_eur * LAMPORTS)
    q = market.quote(WSOL, sw.token_mint, lamports, ctx.cfg.simulation.slippage_bps)
    decimals = (market.prices([sw.token_mint]).get(sw.token_mint) or {}).get("decimals")
    if not q or decimals is None:
        return record(conn, sw, ctx, side="buy", status="failed", value=alloc.amount, reason=f"{alloc.reason}:no_route")

    qty = ctx.haircut(int(q["outAmount"]) / 10 ** decimals)
    record(conn, sw, ctx, side="buy", status="filled", token_amount=qty, value=alloc.amount, fee=ctx.fee,
           price_impact=float(q.get("priceImpactPct") or 0), reason=alloc.reason)
    conn.execute(
        """insert into positions (token_mint, source_wallet, token_amount, cost_basis) values (%s, %s, %s, %s)
           on conflict (token_mint, source_wallet) do update set
               token_amount = positions.token_amount + excluded.token_amount,
               cost_basis = positions.cost_basis + excluded.cost_basis""",
        (sw.token_mint, sw.wallet, qty, alloc.amount + ctx.fee))


def sell_fraction(sw: Swap) -> float:
    """Fracción de su posición que vendió la wallet (1 si no conocemos su saldo posterior)."""
    if sw.post_balance is None:
        return 1.0
    return sw.token_amount / (sw.token_amount + sw.post_balance)


def copy_sell(conn, sw: Swap, ctx: Context) -> None:
    pos = conn.execute("select token_amount, cost_basis from positions where token_mint = %s and source_wallet = %s",
                       (sw.token_mint, sw.wallet)).fetchone()
    if not pos:
        return
    held, cost = float(pos["token_amount"]), float(pos["cost_basis"])
    qty = size_sell(held, sell_fraction(sw))
    # Si queda un resto mínimo, se vende todo
    if held - qty < held * 0.01:
        qty = held

    decimals = (market.prices([sw.token_mint]).get(sw.token_mint) or {}).get("decimals")
    q = market.quote(sw.token_mint, WSOL, int(qty * 10 ** decimals), ctx.cfg.simulation.slippage_bps) \
        if decimals is not None else None
    if not q:
        return record(conn, sw, ctx, side="sell", status="failed", token_amount=qty, reason="no_route")

    value = ctx.haircut(int(q["outAmount"]) / LAMPORTS) * ctx.sol_eur
    record(conn, sw, ctx, side="sell", status="filled", token_amount=qty, value=value, fee=ctx.fee,
           price_impact=float(q.get("priceImpactPct") or 0), reason=f"mirror_{sell_fraction(sw):.0%}")
    if qty >= held:
        conn.execute("delete from positions where token_mint = %s and source_wallet = %s", (sw.token_mint, sw.wallet))
    else:
        conn.execute(
            """update positions set token_amount = token_amount - %s, cost_basis = cost_basis - %s
               where token_mint = %s and source_wallet = %s""",
            (qty, cost * qty / held, sw.token_mint, sw.wallet))


def ensure_token_risk(conn, mint: str) -> None:
    if conn.execute("select 1 from token_risk where token_mint = %s", (mint,)).fetchone():
        return
    info = market.token_info(mint)
    if info:
        save_token_risk(conn, info)

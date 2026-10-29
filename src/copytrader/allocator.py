"""Decide el tamaño de cada operación copiada según la convicción de la señal.

Lógica pura (sin red ni BD) para poder testearla y que el auditor la reajuste vía config.yaml.
"""
from dataclasses import dataclass

from copytrader.config import AllocatorConfig


@dataclass(frozen=True)
class BuySignal:
    wallet_rank: int            # 1 = mejor wallet del ranking
    token: str                  # mint address
    top_wallets_holding: int    # cuántas OTRAS wallets del top ya tienen el token
    est_fee: float              # fees estimadas de la operación (moneda base)


@dataclass(frozen=True)
class PortfolioState:
    total_value: float          # cash + valor de posiciones
    cash: float
    token_exposure: float       # valor que ya tenemos en este token


@dataclass(frozen=True)
class Allocation:
    amount: float
    reason: str


def size_buy(signal: BuySignal, state: PortfolioState, cfg: AllocatorConfig) -> Allocation:
    already_holding = state.token_exposure > 0

    if already_holding:
        target_pct, reason = cfg.consensus_add_pct, "consensus_add"
    elif signal.top_wallets_holding == 0 and cfg.first_mover_pct is not None:
        target_pct, reason = cfg.first_mover_pct, "first_mover"
    else:
        target_pct = cfg.base_pct_by_rank.get(signal.wallet_rank, 0.0)
        reason = f"rank_{signal.wallet_rank}"

    amount = min(
        target_pct * state.total_value,
        cfg.max_pct_per_trade * state.total_value,
        cfg.max_pct_per_token * state.total_value - state.token_exposure,
        state.cash - cfg.min_cash_reserve_pct * state.total_value,
    )

    if amount < cfg.min_trade_value:
        return Allocation(0.0, f"{reason}:below_min")
    if signal.est_fee / amount > cfg.max_fee_pct:
        return Allocation(0.0, f"{reason}:fee_too_high")
    return Allocation(round(amount, 2), reason)


def size_sell(position_amount: float, wallet_sold_fraction: float) -> float:
    """Vendemos la misma fracción que vendió la wallet de su posición."""
    return position_amount * min(max(wallet_sold_fraction, 0.0), 1.0)

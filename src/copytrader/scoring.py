"""Métricas y score de una wallet a partir de sus swaps. Lógica pura, sin red ni BD.

PnL realizado en SOL con coste medio por token. Las ventas de tokens comprados antes de la
ventana no tienen coste conocido y se ignoran. Lo no vendido no cuenta (criterio conservador).
"""
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from copytrader.config import TrackerConfig
from copytrader.solana import Swap

CLOSED_FRACTION = 0.9  # un token se da por cerrado al vender >= 90 % de lo comprado


@dataclass
class WalletMetrics:
    n_swaps: int = 0
    max_swaps_per_day: int = 0
    active_days: int = 0
    history_days: float = 0.0
    n_closed: int = 0
    win_rate: float = 0.0
    realized_pnl_sol: float = 0.0
    roi: float = 0.0
    profitable_days_ratio: float = 0.0
    max_drawdown_sol: float = 0.0
    avg_buy_sol: float = 0.0
    days_since_last_swap: float = 0.0
    launch_buy_ratio: float | None = None  # % de compras en los primeros segundos del token (sniper)

    def to_dict(self) -> dict:
        return asdict(self)


def compute_metrics(swaps: list[Swap], now: datetime | None = None) -> WalletMetrics:
    m = WalletMetrics(n_swaps=len(swaps))
    if not swaps:
        return m
    swaps = sorted(swaps, key=lambda s: s.block_time)
    m.days_since_last_swap = ((now or datetime.now(timezone.utc)) - swaps[-1].block_time).total_seconds() / 86400

    per_day: dict = defaultdict(int)
    for s in swaps:
        per_day[s.block_time.date()] += 1
    m.max_swaps_per_day = max(per_day.values())
    m.active_days = len(per_day)
    m.history_days = (swaps[-1].block_time - swaps[0].block_time).total_seconds() / 86400

    held = defaultdict(float)       # tokens en cartera
    cost = defaultdict(float)       # coste en SOL de lo que hay en cartera
    bought = defaultdict(float)     # total comprado por token
    sold = defaultdict(float)
    token_pnl = defaultdict(float)
    token_cost = defaultdict(float)
    day_pnl: dict = defaultdict(float)
    cum = peak = 0.0
    buys = []

    for s in swaps:
        t = s.token_mint
        if s.side == "buy":
            held[t] += s.token_amount
            cost[t] += s.sol_amount
            bought[t] += s.token_amount
            token_cost[t] += s.sol_amount
            buys.append(s.sol_amount)
            continue
        if held[t] <= 0:
            continue
        qty = min(s.token_amount, held[t])
        basis = cost[t] * qty / held[t]
        proceeds = s.sol_amount * qty / s.token_amount
        pnl = proceeds - basis
        held[t] -= qty
        cost[t] -= basis
        sold[t] += qty
        token_pnl[t] += pnl
        day_pnl[s.block_time.date()] += pnl
        cum += pnl
        peak = max(peak, cum)
        m.max_drawdown_sol = max(m.max_drawdown_sol, peak - cum)

    closed = [t for t in bought if sold[t] >= CLOSED_FRACTION * bought[t]]
    m.n_closed = len(closed)
    if closed:
        m.win_rate = sum(token_pnl[t] > 0 for t in closed) / len(closed)
        closed_cost = sum(token_cost[t] for t in closed)
        m.roi = sum(token_pnl[t] for t in closed) / closed_cost if closed_cost else 0.0
    m.realized_pnl_sol = cum
    if day_pnl:
        m.profitable_days_ratio = sum(v > 0 for v in day_pnl.values()) / len(day_pnl)
    m.avg_buy_sol = sum(buys) / len(buys) if buys else 0.0
    return m


def exclusion_reason(m: WalletMetrics, cfg: TrackerConfig) -> str | None:
    if m.max_swaps_per_day > cfg.max_trades_per_day:
        return "bot"
    if m.n_closed >= 10 and m.win_rate == 0 and abs(m.roi) < 0.05:
        return "wash"  # bot de volumen: compra y vende sin ganar nunca, PnL ~ 0
    if m.n_swaps < cfg.min_trades_30d:
        return "pocos_trades"
    if m.history_days < cfg.min_wallet_age_days:
        return "historial_corto"
    if m.n_closed < cfg.min_closed_positions:
        return "pocas_posiciones_cerradas"
    if m.realized_pnl_sol <= 0:
        return "pnl_negativo"
    if m.days_since_last_swap > cfg.max_days_inactive:
        return "inactiva"  # no permanente: vuelve al ranking cuando opere otra vez
    return None


PERMANENT_EXCLUSIONS = {"bot", "wash", "sniper"}


def launch_buy_ratio(swaps: list[Swap], created: dict[str, datetime], window_s: float,
                     last_n: int = 20) -> float | None:
    """Fracción de las últimas compras hechas a <= window_s de la creación del token.

    Un sniper compra en el bloque de lanzamiento: su ventaja no es copiable (llegaríamos siempre después).
    None si no conocemos la fecha de creación de ninguno de sus tokens.
    """
    buys = sorted((s for s in swaps if s.side == "buy" and s.token_mint in created),
                  key=lambda s: s.block_time)[-last_n:]
    if not buys:
        return None
    return sum((b.block_time - created[b.token_mint]).total_seconds() <= window_s for b in buys) / len(buys)


def onchain_score(m: WalletMetrics, cfg: TrackerConfig) -> float:
    w = cfg.score_weights
    dd_ratio = m.max_drawdown_sol / m.avg_buy_sol if m.avg_buy_sol else 0.0
    score = (
        w["roi"] * min(max(m.roi, 0.0), 2.0) / 2.0
        + w["win_rate"] * m.win_rate
        + w["profitable_days"] * m.profitable_days_ratio
        + w["sample_size"] * min(m.n_closed / 30, 1.0)
        - w["drawdown"] * min(dd_ratio / 5, 1.0)
    )
    return round(score, 4)


def copy_score(copy_roi: float | None) -> float | None:
    """Rendimiento real de copiar la wallet, mapeado a 0..1 (ROI -100 %..+100 %)."""
    if copy_roi is None:
        return None
    return round(min(max(copy_roi, -1.0), 1.0) / 2 + 0.5, 4)


def final_score(onchain: float, copy: float | None, cfg: TrackerConfig) -> float:
    if copy is None:
        return onchain
    return round((1 - cfg.feedback_weight) * onchain + cfg.feedback_weight * copy, 4)

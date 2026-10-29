from datetime import datetime, timedelta, timezone

from copytrader.config import TrackerConfig
from copytrader.scoring import compute_metrics, copy_score, exclusion_reason, final_score, onchain_score
from copytrader.solana import Swap

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def swap(i, day, side, mint, amount, sol):
    return Swap(f"sig{i}", "W", T0 + timedelta(days=day, minutes=i), side, mint, amount, sol)


def test_realized_pnl_with_partial_sells():
    m = compute_metrics([
        swap(1, 0, "buy", "A", 100, 1.0),
        swap(2, 1, "sell", "A", 50, 1.0),    # +0.5
        swap(3, 2, "sell", "A", 50, 0.25),   # -0.25
    ])
    assert round(m.realized_pnl_sol, 6) == 0.25
    assert m.n_closed == 1 and m.win_rate == 1.0
    assert round(m.roi, 6) == 0.25
    assert round(m.max_drawdown_sol, 6) == 0.25
    assert m.profitable_days_ratio == 0.5


def test_ignores_sells_without_cost_basis_and_open_positions():
    m = compute_metrics([
        swap(1, 0, "sell", "OLD", 10, 5.0),
        swap(2, 0, "buy", "B", 10, 1.0),
    ])
    assert m.realized_pnl_sol == 0 and m.n_closed == 0


def test_exclusion_and_scores():
    cfg = TrackerConfig(min_trades_30d=2, min_wallet_age_days=1, min_closed_positions=1)
    good = compute_metrics([
        swap(1, 0, "buy", "A", 10, 1.0), swap(2, 3, "sell", "A", 10, 2.0),
    ], now=T0 + timedelta(days=4))
    assert exclusion_reason(good, cfg) is None
    assert 0 < onchain_score(good, cfg) <= 1

    bot = compute_metrics([swap(i, 0, "buy", "A", 1, 0.1) for i in range(300)])
    assert exclusion_reason(bot, cfg) == "bot"

    wash = compute_metrics([s for i in range(12) for s in (
        swap(2 * i, 0, "buy", f"T{i}", 10, 0.1), swap(2 * i + 1, 0, "sell", f"T{i}", 10, 0.099))])
    assert exclusion_reason(wash, cfg) == "wash"


def test_inactive_wallet_leaves_ranking():
    cfg = TrackerConfig(min_trades_30d=2, min_wallet_age_days=1, min_closed_positions=1, max_days_inactive=2)
    swaps = [swap(1, 0, "buy", "A", 10, 1.0), swap(2, 3, "sell", "A", 10, 2.0)]
    assert exclusion_reason(compute_metrics(swaps, now=T0 + timedelta(days=4)), cfg) is None
    m = compute_metrics(swaps, now=T0 + timedelta(days=6))
    assert round(m.days_since_last_swap) == 3 and exclusion_reason(m, cfg) == "inactiva"


def test_feedback_blend():
    cfg = TrackerConfig(feedback_weight=0.5)
    assert copy_score(None) is None
    assert copy_score(-2) == 0.0 and copy_score(0) == 0.5
    assert final_score(0.6, None, cfg) == 0.6
    assert final_score(0.6, 0.2, cfg) == 0.4

from datetime import date

from copytrader.auditor import Round, group_stats, max_drawdown, proposals, render, risk_bucket
from copytrader.config import Config

CFG = Config()
NO_TRADES = {"buys": 0, "filled": 0, "failed": 0, "skipped": 0, "fees": 0, "avg_delay_s": None, "avg_impact": None}


def test_round_pnl_includes_open_value():
    r = Round("W", "T", "first_mover", cost=100, proceeds=60, open_value=50)
    assert r.pnl == 10 and not r.closed


def test_risk_buckets():
    assert risk_bucket({}) == "desconocido"
    assert risk_bucket({"liquidity_usd": 1e6, "mint_authority": True}) == "alto"
    assert risk_bucket({"liquidity_usd": 100_000, "top10_holders_pct": 10}) == "medio"
    assert risk_bucket({"liquidity_usd": 1e6, "top10_holders_pct": 10}) == "bajo"


def test_group_stats():
    rounds = [Round("A", "T1", "first_mover", cost=10, proceeds=15),
              Round("A", "T2", "first_mover", cost=10, proceeds=5),
              Round("B", "T3", "rank_1", cost=10, open_value=12)]
    s = group_stats(rounds, lambda r: r.wallet)
    assert s["A"] == {"n": 2, "pnl": 0.0, "roi": 0.0, "win_rate": 0.5}
    assert s["B"]["win_rate"] is None  # ninguna cerrada


def test_max_drawdown():
    assert max_drawdown([200, 220, 165, 210]) == 0.25
    assert max_drawdown([200]) == 0.0


def test_proposals_need_sample_and_bad_results():
    losing = {"first_mover": {"n": 6, "pnl": -30, "roi": -0.3, "win_rate": 0.2}}
    props = proposals(losing, {}, {}, NO_TRADES, CFG)
    assert "first_mover_pct" in props[0]
    small = {"first_mover": {"n": 2, "pnl": -30, "roi": -0.3, "win_rate": 0.0}}
    assert proposals(small, {}, {}, NO_TRADES, CFG)[0].startswith("Sin cambios")


def test_render_smoke():
    stats = {"A": {"n": 1, "pnl": 5.0, "roi": 0.5, "win_rate": None}}
    md = render(date(2026, 10, 29), CFG, 205, 100, 0.0, NO_TRADES, stats, stats, {}, {"same_wallet_add": 2}, ["x"])
    assert "205.00 EUR" in md and "| `A` | 1 | +5.00 | +50.0% | - |" in md

from datetime import datetime, timezone

from copytrader.copier import sell_fraction
from copytrader.solana import Swap

T = datetime(2026, 10, 1, tzinfo=timezone.utc)


def sell(amount, post):
    return Swap("SIG", "W", T, "sell", "TOK", amount, 1.0, post)


def test_sell_fraction_mirrors_wallet():
    assert sell_fraction(sell(250, 750)) == 0.25
    assert sell_fraction(sell(1000, 0)) == 1.0
    assert sell_fraction(sell(10, None)) == 1.0

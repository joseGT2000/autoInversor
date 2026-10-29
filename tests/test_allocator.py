from copytrader.allocator import BuySignal, PortfolioState, size_buy, size_sell
from copytrader.config import AllocatorConfig

CFG = AllocatorConfig()
FRESH = PortfolioState(total_value=200, cash=200, token_exposure=0)


def signal(rank=1, holding=1, fee=0.01):
    return BuySignal(wallet_rank=rank, token="MINT", top_wallets_holding=holding, est_fee=fee)


def test_first_mover_uses_conviction_pct():
    # El #3 entra primero y ni el #1 ni el #2 tienen el token -> 60 %, limitado por la reserva de cash
    alloc = size_buy(signal(rank=3, holding=0), FRESH, CFG)
    assert alloc.reason == "first_mover"
    assert alloc.amount == 120.0


def test_rank_pct_when_others_already_in():
    alloc = size_buy(signal(rank=2, holding=1), FRESH, CFG)
    assert alloc == size_buy(signal(rank=2, holding=2), FRESH, CFG)
    assert alloc.amount == 50.0


def test_consensus_add_respects_token_cap():
    state = PortfolioState(total_value=200, cash=80, token_exposure=130)
    alloc = size_buy(signal(holding=1), state, CFG)
    assert alloc.reason == "consensus_add"
    assert alloc.amount == 10.0  # cap por token 140 - 130


def test_keeps_cash_reserve():
    state = PortfolioState(total_value=200, cash=21, token_exposure=0)
    assert size_buy(signal(holding=1), state, CFG).amount == 0.0


def test_skips_when_fees_too_high():
    state = PortfolioState(total_value=200, cash=200, token_exposure=0)
    alloc = size_buy(signal(fee=5), state, CFG)
    assert alloc.amount == 0.0 and alloc.reason.endswith("fee_too_high")


def test_sell_mirrors_fraction():
    assert size_sell(100, 0.25) == 25
    assert size_sell(100, 1.5) == 100

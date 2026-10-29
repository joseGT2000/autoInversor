from copytrader.solana import USDC, WSOL, parse_swap

W = "Wallet111"


def tx(pre_sol, post_sol, pre_tok, post_tok, err=None):
    def bal(entries):
        return [{"owner": W, "mint": m, "uiTokenAmount": {"uiAmountString": str(a)}} for m, a in entries]
    return {
        "blockTime": 1_790_000_000,
        "transaction": {"signatures": ["SIG"], "message": {"accountKeys": [{"pubkey": W}, {"pubkey": "Pool"}]}},
        "meta": {"err": err, "preBalances": [pre_sol, 0], "postBalances": [post_sol, 0],
                 "preTokenBalances": bal(pre_tok), "postTokenBalances": bal(post_tok)},
    }


def test_buy_with_native_sol():
    s = parse_swap(tx(2_000_000_000, 1_500_000_000, [("TOK", 0)], [("TOK", 1000)]), W, 100)
    assert (s.side, s.token_mint, s.token_amount, s.sol_amount) == ("buy", "TOK", 1000, 0.5)


def test_sell_for_usdc_converted_to_sol():
    s = parse_swap(tx(1e9, 1e9, [("TOK", 1000), (USDC, 0)], [("TOK", 0), (USDC, 50)]), W, 100)
    assert (s.side, s.sol_amount, s.post_balance) == ("sell", 0.5, 0)


def test_partial_sell_keeps_post_balance():
    s = parse_swap(tx(1e9, 2e9, [("TOK", 1000)], [("TOK", 750)]), W, 100)
    assert (s.side, s.token_amount, s.post_balance) == ("sell", 250, 750)


def test_wsol_counts_as_quote():
    s = parse_swap(tx(1e9, 1e9, [("TOK", 0), (WSOL, 1)], [("TOK", 5), (WSOL, 0.2)]), W, 100)
    assert s.side == "buy" and round(s.sol_amount, 6) == 0.8


def test_ignores_transfers_failed_and_token_to_token():
    assert parse_swap(tx(1e9, 1e9 - 5000, [("TOK", 0)], [("TOK", 10)]), W, 100) is None  # airdrop
    assert parse_swap(tx(2e9, 1e9, [("TOK", 0)], [("TOK", 10)], err={"x": 1}), W, 100) is None
    assert parse_swap(tx(1e9, 1e9, [("A", 10), ("B", 0)], [("A", 0), ("B", 5)]), W, 100) is None

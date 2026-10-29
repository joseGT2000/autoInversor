// Mismos casos que tests/test_solana_parse.py, tests/test_allocator.py y tests/test_copier.py.
import { assertEquals } from "jsr:@std/assert@1";
import { type AllocatorConfig, parseSwap, sellFraction, signers, sizeBuy, sizeSell, USDC, WSOL } from "./logic.ts";

const W = "Wallet111";

function tx(preSol: number, postSol: number, preTok: [string, number][], postTok: [string, number][], err: unknown = null) {
  const bal = (entries: [string, number][]) =>
    entries.map(([mint, a]) => ({ owner: W, mint, uiTokenAmount: { uiAmountString: String(a) } }));
  return {
    blockTime: 1_790_000_000,
    transaction: { signatures: ["SIG"], message: { accountKeys: [W, "Pool"], header: { numRequiredSignatures: 1 } } },
    meta: { err, preBalances: [preSol, 0], postBalances: [postSol, 0], preTokenBalances: bal(preTok), postTokenBalances: bal(postTok) },
  };
}

Deno.test("compra con SOL nativo", () => {
  const s = parseSwap(tx(2e9, 1.5e9, [["TOK", 0]], [["TOK", 1000]]), W, 100)!;
  assertEquals([s.side, s.tokenMint, s.tokenAmount, s.solAmount], ["buy", "TOK", 1000, 0.5]);
});

Deno.test("venta a USDC convertida a SOL", () => {
  const s = parseSwap(tx(1e9, 1e9, [["TOK", 1000], [USDC, 0]], [["TOK", 0], [USDC, 50]]), W, 100)!;
  assertEquals([s.side, s.solAmount, s.postBalance], ["sell", 0.5, 0]);
});

Deno.test("venta parcial guarda el saldo posterior", () => {
  const s = parseSwap(tx(1e9, 2e9, [["TOK", 1000]], [["TOK", 750]]), W, 100)!;
  assertEquals([s.side, s.tokenAmount, s.postBalance, sellFraction(s)], ["sell", 250, 750, 0.25]);
});

Deno.test("WSOL cuenta como quote", () => {
  const s = parseSwap(tx(1e9, 1e9, [["TOK", 0], [WSOL, 1]], [["TOK", 5], [WSOL, 0.2]]), W, 100)!;
  assertEquals([s.side, Math.round(s.solAmount * 1e6) / 1e6], ["buy", 0.8]);
});

Deno.test("ignora airdrops, fallidas y token->token", () => {
  assertEquals(parseSwap(tx(1e9, 1e9 - 5000, [["TOK", 0]], [["TOK", 10]]), W, 100), null);
  assertEquals(parseSwap(tx(2e9, 1e9, [["TOK", 0]], [["TOK", 10]], { x: 1 }), W, 100), null);
  assertEquals(parseSwap(tx(1e9, 1e9, [["A", 10], ["B", 0]], [["A", 0], ["B", 5]]), W, 100), null);
});

Deno.test("firmantes en formato raw y jsonParsed", () => {
  assertEquals(signers(tx(0, 0, [], [])), [W]);
  const parsed = { transaction: { message: { accountKeys: [{ pubkey: "A", signer: true }, { pubkey: "B", signer: false }] } } };
  assertEquals(signers(parsed), ["A"]);
});

// Valores por defecto de AllocatorConfig en config.py
const CFG: AllocatorConfig = {
  base_pct_by_rank: { "1": 0.30, "2": 0.25, "3": 0.20 },
  first_mover_pct: 0.60, consensus_add_pct: 0.10, max_pct_per_trade: 0.60, max_pct_per_token: 0.70,
  min_cash_reserve_pct: 0.10, min_trade_value: 2.0, max_fee_pct: 0.02,
};
const FRESH = { totalValue: 200, cash: 200, tokenExposure: 0 };

Deno.test("allocator: igual que la versión Python", () => {
  assertEquals(sizeBuy({ walletRank: 3, topWalletsHolding: 0, estFee: 0.01 }, FRESH, CFG), { amount: 120, reason: "first_mover" });
  assertEquals(sizeBuy({ walletRank: 2, topWalletsHolding: 1, estFee: 0.01 }, FRESH, CFG).amount, 50);
  assertEquals(
    sizeBuy({ walletRank: 1, topWalletsHolding: 1, estFee: 0.01 }, { totalValue: 200, cash: 80, tokenExposure: 130 }, CFG),
    { amount: 10, reason: "consensus_add" },
  );
  assertEquals(sizeBuy({ walletRank: 1, topWalletsHolding: 1, estFee: 0.01 }, { totalValue: 200, cash: 21, tokenExposure: 0 }, CFG).amount, 0);
  assertEquals(sizeBuy({ walletRank: 1, topWalletsHolding: 1, estFee: 5 }, FRESH, CFG).reason, "rank_1:fee_too_high");
  assertEquals([sizeSell(100, 0.25), sizeSell(100, 1.5)], [25, 100]);
});

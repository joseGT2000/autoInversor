// Lógica pura del copier, port de src/copytrader/{solana,allocator,copier}.py.
// Si cambias algo aquí, cambia también la versión Python (la usa el copier de respaldo).

export const WSOL = "So11111111111111111111111111111111111111112";
export const USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
export const USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB";
export const EURC = "HzwqbKZw8HxMN6bF2yFZNrht3c2iXXzpKcFu7uBEDKtr";
const STABLES = new Set([USDC, USDT]);
export const LAMPORTS = 1_000_000_000;
const MIN_QUOTE_SOL = 0.001;

// deno-lint-ignore no-explicit-any
export type Tx = any;

export interface Swap {
  signature: string;
  wallet: string;
  blockTime: Date;
  side: "buy" | "sell";
  tokenMint: string;
  tokenAmount: number;
  solAmount: number;
  postBalance: number;
}

function accountKeys(tx: Tx): string[] {
  return tx.transaction.message.accountKeys.map((k: string | { pubkey: string }) =>
    typeof k === "string" ? k : k.pubkey
  );
}

/** Firmantes de la transacción (formato raw de webhook o jsonParsed de RPC). */
export function signers(tx: Tx): string[] {
  const keys = tx.transaction.message.accountKeys;
  if (typeof keys[0] !== "string") {
    return keys.filter((k: { signer: boolean }) => k.signer).map((k: { pubkey: string }) => k.pubkey);
  }
  return keys.slice(0, tx.transaction.message.header?.numRequiredSignatures ?? 1);
}

function tokenDeltas(meta: Tx, owner: string): Map<string, number> {
  const deltas = new Map<string, number>();
  for (const [sign, key] of [[-1, "preTokenBalances"], [1, "postTokenBalances"]] as const) {
    for (const b of meta[key] ?? []) {
      if (b.owner !== owner) continue;
      const amount = Number(b.uiTokenAmount.uiAmountString || 0);
      deltas.set(b.mint, (deltas.get(b.mint) ?? 0) + sign * amount);
    }
  }
  return deltas;
}

/** Swap token<->SOL/stable hecho por `wallet` en `tx`, o null. Igual que solana.parse_swap. */
export function parseSwap(tx: Tx, wallet: string, solUsd: number): Swap | null {
  const meta = tx.meta ?? {};
  if (meta.err !== null && meta.err !== undefined) return null;
  const keys = accountKeys(tx);
  const idx = keys.indexOf(wallet);
  if (idx < 0) return null;

  const deltas = tokenDeltas(meta, wallet);
  let quoteSol = (meta.postBalances[idx] - meta.preBalances[idx]) / LAMPORTS;
  quoteSol += deltas.get(WSOL) ?? 0;
  deltas.delete(WSOL);
  for (const mint of STABLES) {
    quoteSol += (deltas.get(mint) ?? 0) / solUsd;
    deltas.delete(mint);
  }

  const tokens = [...deltas].filter(([, d]) => Math.abs(d) > 0);
  if (tokens.length !== 1 || Math.abs(quoteSol) < MIN_QUOTE_SOL) return null;
  const [mint, amount] = tokens[0];
  let side: "buy" | "sell";
  if (amount > 0 && quoteSol < 0) side = "buy";
  else if (amount < 0 && quoteSol > 0) side = "sell";
  else return null;

  const postBalance = (meta.postTokenBalances ?? [])
    .filter((b: Tx) => b.owner === wallet && b.mint === mint)
    .reduce((s: number, b: Tx) => s + Number(b.uiTokenAmount.uiAmountString || 0), 0);

  return {
    signature: tx.transaction.signatures[0],
    wallet,
    blockTime: new Date(tx.blockTime * 1000),
    side,
    tokenMint: mint,
    tokenAmount: Math.abs(amount),
    solAmount: Math.abs(quoteSol),
    postBalance,
  };
}

// --- Allocator (allocator.py) ---------------------------------------------------------------

export interface AllocatorConfig {
  base_pct_by_rank: Record<string, number>;
  first_mover_pct: number | null;
  consensus_add_pct: number;
  max_pct_per_trade: number;
  max_pct_per_token: number;
  min_cash_reserve_pct: number;
  min_trade_value: number;
  max_fee_pct: number;
}

export interface BuySignal {
  walletRank: number;
  topWalletsHolding: number;
  estFee: number;
}

export interface PortfolioState {
  totalValue: number;
  cash: number;
  tokenExposure: number;
}

export function sizeBuy(signal: BuySignal, state: PortfolioState, cfg: AllocatorConfig) {
  let targetPct: number, reason: string;
  if (state.tokenExposure > 0) {
    [targetPct, reason] = [cfg.consensus_add_pct, "consensus_add"];
  } else if (signal.topWalletsHolding === 0 && cfg.first_mover_pct !== null) {
    [targetPct, reason] = [cfg.first_mover_pct, "first_mover"];
  } else {
    targetPct = cfg.base_pct_by_rank[String(signal.walletRank)] ?? 0;
    reason = `rank_${signal.walletRank}`;
  }
  const amount = Math.min(
    targetPct * state.totalValue,
    cfg.max_pct_per_trade * state.totalValue,
    cfg.max_pct_per_token * state.totalValue - state.tokenExposure,
    state.cash - cfg.min_cash_reserve_pct * state.totalValue,
  );
  if (amount < cfg.min_trade_value) return { amount: 0, reason: `${reason}:below_min` };
  if (signal.estFee / amount > cfg.max_fee_pct) return { amount: 0, reason: `${reason}:fee_too_high` };
  return { amount: Math.round(amount * 100) / 100, reason };
}

export function sizeSell(positionAmount: number, walletSoldFraction: number): number {
  return positionAmount * Math.min(Math.max(walletSoldFraction, 0), 1);
}

export function sellFraction(sw: Swap): number {
  return sw.tokenAmount / (sw.tokenAmount + sw.postBalance);
}

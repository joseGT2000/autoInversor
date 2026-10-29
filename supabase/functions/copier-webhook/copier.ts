// Copia en tiempo real los swaps que llegan por el webhook de Helius. Port de src/copytrader/copier.py.
// La deduplicación con el copier de respaldo (Python, cron) es el PK de wallet_trades: solo copia
// quien consigue insertar el swap.
import type postgres from "npm:postgres@3.4.5";
import {
  EURC,
  LAMPORTS,
  parseSwap,
  sellFraction,
  signers,
  sizeBuy,
  sizeSell,
  type AllocatorConfig,
  type Swap,
  type Tx,
  WSOL,
} from "./logic.ts";

type Sql = postgres.Sql | postgres.TransactionSql;
const JUP = "https://lite-api.jup.ag";
const BASE_TX_FEE_SOL = 0.000005;

interface Settings {
  portfolio: { initial_capital: number; currency: string; top_n: number };
  allocator: AllocatorConfig;
  simulation: { slippage_bps: number; max_price_impact_pct: number; priority_fee_sol: number; execution_delay_s: number };
  copier: { max_signal_age_min: number; copy_same_wallet_adds: boolean };
}

interface Ctx {
  cfg: Settings;
  solUsd: number;
  solEur: number;
  ranks: Map<string, number>;
  fee: number;
}

// --- Jupiter ---------------------------------------------------------------------------------

async function getJson(url: string): Promise<{ status: number; body: Tx }> {
  for (let attempt = 0; ; attempt++) {
    const resp = await fetch(url);
    if (resp.status !== 429 || attempt >= 4) return { status: resp.status, body: await resp.json() };
    await new Promise((r) => setTimeout(r, 2 ** attempt * 1000));
  }
}

async function prices(mints: string[]): Promise<Record<string, { usdPrice: number; decimals: number }>> {
  const out: Record<string, { usdPrice: number; decimals: number }> = {};
  const unique = [...new Set(mints)];
  for (let i = 0; i < unique.length; i += 50) {
    const { body } = await getJson(`${JUP}/price/v3?ids=${unique.slice(i, i + 50).join(",")}`);
    for (const [m, v] of Object.entries(body ?? {})) if (v) out[m] = v as { usdPrice: number; decimals: number };
  }
  return out;
}

async function quote(input: string, output: string, amountRaw: bigint, slippageBps: number) {
  const { status, body } = await getJson(
    `${JUP}/swap/v1/quote?inputMint=${input}&outputMint=${output}&amount=${amountRaw}&slippageBps=${slippageBps}`,
  );
  if (status === 400) return null;
  if (status !== 200) throw new Error(`Jupiter quote HTTP ${status}`);
  return body as { outAmount: string; priceImpactPct: string };
}

// --- Entrada ---------------------------------------------------------------------------------

export async function handleTransactions(sql: Sql, txs: Tx[]) {
  const [{ config: cfg }] = await sql<{ config: Settings }[]>`select config from settings where id = 1`;
  const rankRows = await sql<{ address: string; rank: number }[]>`
    select address, rank from wallet_scores
    where day = (select max(day) from wallet_scores) and rank <= ${cfg.portfolio.top_n}`;
  const ranks = new Map(rankRows.map((r) => [r.address, r.rank]));
  const withPositions = await sql<{ source_wallet: string }[]>`select distinct source_wallet from positions`;
  const watched = new Set([...ranks.keys(), ...withPositions.map((r) => r.source_wallet)]);

  const px = await prices([WSOL, EURC]);
  const solUsd = px[WSOL].usdPrice;
  const solEur = cfg.portfolio.currency === "EUR" ? solUsd / px[EURC].usdPrice : solUsd;
  const ctx: Ctx = {
    cfg, solUsd, solEur, ranks,
    fee: (cfg.simulation.priority_fee_sol + BASE_TX_FEE_SOL) * solEur,
  };

  const results: string[] = [];
  for (const tx of txs) {
    const wallet = signers(tx).find((s) => watched.has(s));
    if (!wallet) continue;
    const sw = parseSwap(tx, wallet, solUsd);
    if (!sw) continue;
    // Dentro de una transacción (pruebas) se usa savepoint; si no, una transacción por swap
    const atomic = "savepoint" in sql
      ? sql.savepoint((t) => processSwap(t, sw, ctx))
      : sql.begin((t) => processSwap(t, sw, ctx));
    results.push(await atomic as string);
  }
  return results;
}

async function processSwap(sql: Sql, sw: Swap, ctx: Ctx): Promise<string> {
  const inserted = await sql`
    insert into wallet_trades (signature, address, block_time, side, token_mint, token_amount, sol_amount)
    values (${sw.signature}, ${sw.wallet}, ${sw.blockTime}, ${sw.side}, ${sw.tokenMint}, ${sw.tokenAmount}, ${sw.solAmount})
    on conflict do nothing returning signature`;
  if (inserted.length === 0) return `${sw.signature}: ya procesado`;
  if (Date.now() - sw.blockTime.getTime() > ctx.cfg.copier.max_signal_age_min * 60_000) {
    return `${sw.signature}: demasiado antiguo`;
  }
  if (sw.side === "buy" && ctx.ranks.has(sw.wallet)) return await copyBuy(sql, sw, ctx);
  if (sw.side === "sell") return await copySell(sql, sw, ctx);
  return `${sw.signature}: compra de wallet fuera del top`;
}

// --- Cartera ---------------------------------------------------------------------------------

async function portfolioTotals(sql: Sql, ctx: Ctx): Promise<[number, number]> {
  const [{ flow }] = await sql`
    select coalesce(sum(case when side = 'buy' then -(value + fee) else value - fee end), 0) as flow
    from sim_trades where status = 'filled'`;
  const cash = ctx.cfg.portfolio.initial_capital + Number(flow);
  const positions = await sql`select token_mint, token_amount, cost_basis from positions`;
  const px = positions.length ? await prices(positions.map((p) => p.token_mint)) : {};
  let value = 0;
  for (const p of positions) {
    const price = px[p.token_mint];
    value += price ? Number(p.token_amount) * price.usdPrice * ctx.solEur / ctx.solUsd : Number(p.cost_basis);
  }
  return [cash, cash + value];
}

async function otherTopHolders(sql: Sql, token: string, wallet: string, ranks: Map<string, number>) {
  const others = [...ranks.keys()].filter((w) => w !== wallet);
  if (!others.length) return 0;
  const [{ n }] = await sql`
    select count(*)::int as n from (
      select address from wallet_trades
      where token_mint = ${token} and address = any(${others})
      group by address
      having sum(case when side = 'buy' then token_amount else -token_amount end) > 0) h`;
  return n;
}

// --- Copia -----------------------------------------------------------------------------------

interface Rec {
  side: "buy" | "sell";
  status: "filled" | "skipped" | "failed";
  tokenAmount?: number;
  value?: number;
  fee?: number;
  priceImpact?: number | null;
  reason?: string | null;
}

async function record(sql: Sql, sw: Swap, ctx: Ctx, r: Rec): Promise<string> {
  const tokenAmount = r.tokenAmount ?? 0;
  const value = r.value ?? 0;
  const delay = (Date.now() - sw.blockTime.getTime()) / 1000 + ctx.cfg.simulation.execution_delay_s;
  await sql`
    insert into sim_trades (source_signature, source_wallet, side, token_mint, token_amount, value, price,
                            price_impact_pct, fee, delay_s, allocator_reason, status)
    values (${sw.signature}, ${sw.wallet}, ${r.side}, ${sw.tokenMint}, ${tokenAmount}, ${value},
            ${tokenAmount ? value / tokenAmount : 0}, ${r.priceImpact ?? null}, ${r.fee ?? 0}, ${delay},
            ${r.reason ?? null}, ${r.status})`;
  const impact = r.priceImpact != null ? ` impact ${(r.priceImpact * 100).toFixed(1)}%` : "";
  return `${r.status} ${r.side} ${sw.tokenMint.slice(0, 6)} ${value.toFixed(2)} <- ${sw.wallet.slice(0, 6)} (${r.reason})${impact}`;
}

function haircut(amount: number, ctx: Ctx) {
  return amount * (1 - ctx.cfg.simulation.slippage_bps / 20_000);
}

async function copyBuy(sql: Sql, sw: Swap, ctx: Ctx): Promise<string> {
  const held = await sql`select 1 from positions where token_mint = ${sw.tokenMint} and source_wallet = ${sw.wallet}`;
  if (held.length && !ctx.cfg.copier.copy_same_wallet_adds) {
    return record(sql, sw, ctx, { side: "buy", status: "skipped", reason: "same_wallet_add" });
  }

  const [cash, total] = await portfolioTotals(sql, ctx);
  const [{ v: exposure }] = await sql`
    select coalesce(sum(cost_basis), 0) as v from positions where token_mint = ${sw.tokenMint}`;
  const alloc = sizeBuy(
    {
      walletRank: ctx.ranks.get(sw.wallet)!,
      topWalletsHolding: await otherTopHolders(sql, sw.tokenMint, sw.wallet, ctx.ranks),
      estFee: ctx.fee,
    },
    { totalValue: total, cash, tokenExposure: Number(exposure) },
    ctx.cfg.allocator,
  );
  if (alloc.amount <= 0) return record(sql, sw, ctx, { side: "buy", status: "skipped", reason: alloc.reason });

  await ensureTokenRisk(sql, sw.tokenMint);
  const lamports = BigInt(Math.floor(alloc.amount / ctx.solEur * LAMPORTS));
  const q = await quote(WSOL, sw.tokenMint, lamports, ctx.cfg.simulation.slippage_bps);
  const decimals = (await prices([sw.tokenMint]))[sw.tokenMint]?.decimals;
  if (!q || decimals === undefined) {
    return record(sql, sw, ctx, { side: "buy", status: "failed", value: alloc.amount, reason: `${alloc.reason}:no_route` });
  }
  const impact = Number(q.priceImpactPct || 0);
  if (impact > ctx.cfg.simulation.max_price_impact_pct) {
    return record(sql, sw, ctx, {
      side: "buy", status: "failed", value: alloc.amount, priceImpact: impact, reason: `${alloc.reason}:price_impact`,
    });
  }

  const qty = haircut(Number(q.outAmount) / 10 ** decimals, ctx);
  const msg = await record(sql, sw, ctx, {
    side: "buy", status: "filled", tokenAmount: qty, value: alloc.amount, fee: ctx.fee,
    priceImpact: impact, reason: alloc.reason,
  });
  await sql`
    insert into positions (token_mint, source_wallet, token_amount, cost_basis)
    values (${sw.tokenMint}, ${sw.wallet}, ${qty}, ${alloc.amount + ctx.fee})
    on conflict (token_mint, source_wallet) do update set
      token_amount = positions.token_amount + excluded.token_amount,
      cost_basis = positions.cost_basis + excluded.cost_basis`;
  return msg;
}

async function copySell(sql: Sql, sw: Swap, ctx: Ctx): Promise<string> {
  const [pos] = await sql`
    select token_amount, cost_basis from positions where token_mint = ${sw.tokenMint} and source_wallet = ${sw.wallet}`;
  if (!pos) return `${sw.signature}: venta de un token que no copiamos`;
  const held = Number(pos.token_amount), cost = Number(pos.cost_basis);
  let qty = sizeSell(held, sellFraction(sw));
  if (held - qty < held * 0.01) qty = held;

  const decimals = (await prices([sw.tokenMint]))[sw.tokenMint]?.decimals;
  const q = decimals === undefined
    ? null
    : await quote(sw.tokenMint, WSOL, BigInt(Math.floor(qty * 10 ** decimals)), ctx.cfg.simulation.slippage_bps);
  if (!q) return record(sql, sw, ctx, { side: "sell", status: "failed", tokenAmount: qty, reason: "no_route" });

  const value = haircut(Number(q.outAmount) / LAMPORTS, ctx) * ctx.solEur;
  const msg = await record(sql, sw, ctx, {
    side: "sell", status: "filled", tokenAmount: qty, value, fee: ctx.fee,
    priceImpact: Number(q.priceImpactPct || 0), reason: `mirror_${Math.round(sellFraction(sw) * 100)}%`,
  });
  if (qty >= held) {
    await sql`delete from positions where token_mint = ${sw.tokenMint} and source_wallet = ${sw.wallet}`;
  } else {
    await sql`
      update positions set token_amount = token_amount - ${qty}, cost_basis = cost_basis - ${cost * qty / held}
      where token_mint = ${sw.tokenMint} and source_wallet = ${sw.wallet}`;
  }
  return msg;
}

async function ensureTokenRisk(sql: Sql, mint: string) {
  const exists = await sql`select 1 from token_risk where token_mint = ${mint}`;
  if (exists.length) return;
  const { body } = await getJson(`${JUP}/tokens/v2/search?query=${mint}`);
  const t = (body ?? []).find((x: Tx) => x.id === mint);
  if (!t) return;
  const audit = t.audit ?? {};
  await sql`
    insert into token_risk (token_mint, liquidity_usd, mint_authority, freeze_authority, top10_holders_pct, raw)
    values (${mint}, ${t.liquidity ?? null}, ${t.mintAuthority != null}, ${!audit.freezeAuthorityDisabled},
            ${audit.topHoldersPercentage ?? null},
            ${sql.json({ symbol: t.symbol, organicScore: t.organicScore, holderCount: t.holderCount, mcap: t.mcap, audit: t.audit })})
    on conflict (token_mint) do nothing`;
}

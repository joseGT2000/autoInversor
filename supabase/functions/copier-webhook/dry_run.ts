// Prueba local de la función contra la BD real, sin dejar rastro (todo se revierte al final).
// Toma compras recientes del top en tokens con liquidez, las pide a Helius en formato raw (como
// el webhook) y las pasa por handleTransactions como si fueran nuevas.
//   deno run -A --env-file=../../../.env dry_run.ts
import postgres from "npm:postgres@3.4.5";
import { handleTransactions } from "./copier.ts";

const sql = postgres(Deno.env.get("DATABASE_URL")!, { prepare: false, max: 1 });
const rawKey = Deno.env.get("HELIUS_API_KEY")!.trim();
const heliusKey = rawKey.startsWith("http") ? new URL(rawKey).searchParams.get("api-key")! : rawKey;

async function rawTx(signature: string) {
  const resp = await fetch(`https://mainnet.helius-rpc.com/?api-key=${heliusKey}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0", id: 1, method: "getTransaction",
      params: [signature, { encoding: "json", maxSupportedTransactionVersion: 1 }],
    }),
  });
  return (await resp.json()).result;
}

const buys = await sql`
  select t.signature, t.token_mint from wallet_trades t
  join wallet_scores s on s.address = t.address and s.day = (select max(day) from wallet_scores)
  where s.rank <= 3 and t.side = 'buy' order by t.block_time desc limit 40`;
const prices = await (await fetch(`https://lite-api.jup.ag/price/v3?ids=${[...new Set(buys.map((b) => b.token_mint))].join(",")}`)).json();
const liq = (b: { token_mint: string }) => prices[b.token_mint]?.liquidity ?? 0;
const byLiquidity = [...new Map(buys.map((b) => [b.token_mint, b])).values()].sort((a, b) => liq(b) - liq(a));
const picked = [...byLiquidity.slice(0, 2), ...byLiquidity.slice(-1)];
for (const b of picked) console.log(`compra ${b.token_mint.slice(0, 6)}: liquidez ${Math.round(liq(b))} $`);

try {
  await sql.begin(async (t) => {
    await t`update settings set config = jsonb_set(config, '{copier,max_signal_age_min}', '100000000')`;
    for (const b of picked) await t`delete from wallet_trades where signature = ${b.signature}`;
    const txs = await Promise.all(picked.map((b) => rawTx(b.signature)));
    for (const r of await handleTransactions(t, txs)) console.log(" ", r);
    // Repetir el mismo lote debe ser idempotente
    for (const r of await handleTransactions(t, txs)) console.log("  2ª vez:", r);
    console.log("posiciones:", await t`select left(token_mint, 6) as token, token_amount, cost_basis from positions`);
    throw new Error("ROLLBACK");
  });
} catch (e) {
  if (!(e instanceof Error && e.message === "ROLLBACK")) throw e;
  console.log("revertido: la BD queda como estaba");
} finally {
  await sql.end();
}

// Endpoint que recibe el webhook "raw" de Helius y copia los swaps en simulación al momento.
// Autenticación: Helius envía el `authHeader` configurado en el webhook como cabecera Authorization.
import postgres from "npm:postgres@3.4.5";
import { handleTransactions } from "./copier.ts";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { prepare: false, max: 1 });
const SECRET = Deno.env.get("WEBHOOK_SECRET");

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("method not allowed", { status: 405 });
  if (!SECRET || req.headers.get("authorization") !== SECRET) {
    return new Response("unauthorized", { status: 401 });
  }
  const body = await req.json();
  try {
    const results = await handleTransactions(sql, Array.isArray(body) ? body : [body]);
    for (const r of results) console.log(r);
    return Response.json({ ok: true, results });
  } catch (e) {
    console.error(e instanceof Error ? e.message : e);
    return new Response("error", { status: 500 });
  }
});

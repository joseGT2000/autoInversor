# Copy-Trader cripto on-chain (simulación)

Rastrea wallets "smart money" on-chain, copia sus swaps **en simulación (paper trading)**, audita resultados y retroalimenta el ranking. Dinero real solo tras la fase de auditoría; la decisión y ejecución real la toma el usuario.

## Estado actual
- Fase: **1 – Setup completado** (2026-10-29). Repo **público** github.com/joseGT2000/autoInversor (rama `main`). Supabase con las 7 tablas creadas, Helius verificado (key o URL del RPC valen), allocator 6 tests OK. Birdeye sin key (opcional).
- **Fase 2 – Tracker implementado** (2026-10-29): descubrimiento + escaneo incremental + scoring + feedback. 14 tests OK. 2 ejecuciones reales (~3k créditos c/u).
- Pools trending = casi solo bots (17 `wash`, 12 `bot`). **Birdeye `/trader/gainers-losers` ordenado por `realized_pnl`** funciona (ordenar por `PnL` da basura no realizada).
- Ranking 2026-10-29: **1 elegible** (`9R3m89…`, 960 SOL realizados/30d, win-rate 81 %, score 0,51). 5 wallets Birdeye muy buenas (win-rate ~100 %) caen por `historial_corto` con historial idéntico de 12,2 días → probable mismo operador; entrarán en ~2 días. Pendiente: detectar wallets vinculadas para no concentrar.
- **Fase 3 – Copier implementado** (2026-10-29): probado con cotizaciones reales (compra+venta parcial en transacción revertida).
- **Fase 4 – Auditor implementado** (2026-10-29): 22 tests OK. Informe diario en `reports/audit-YYYY-MM-DD.md` (Actions lo commitea → hacer `git pull` antes de trabajar en local) + resumen de la ejecución + tabla `audit_reports`.
- **MODO PRUEBA**: filtros relajados en `config.yaml` (valores estrictos en comentarios) → 6 wallets elegibles. Top 3: `3me2rp…`, `Cz126Z…`, `E1qzvE…` (5 de las 6 con historial idéntico de 12,3 días pero solapan ≤4 tokens → no se filtran).
- **Copier en tiempo real** (2026-10-29): Edge Function `supabase/functions/copier-webhook` (TypeScript) recibe el webhook raw de Helius. Código y tests OK, probado en local contra BD real (`dry_run.ts`, revertido). **Pendiente**: secrets de GitHub (`SUPABASE_ACCESS_TOKEN`, `SUPABASE_PROJECT_REF`=pjtgqfzpjruegnyzmsdx, `WEBHOOK_URL`, `WEBHOOK_SECRET`) → desplegar → crear webhook (`webhook.sync`, lo hacen tracker/copier si hay `WEBHOOK_URL`).
- Cartera: 200 € sin operaciones aún. Las compras recientes del top son memecoins casi muertas (price impact 3–11 % incluso con importes pequeños).

## Tiempo real: cómo funciona
- Helius webhook `raw` (1 crédito/evento) → `copier-webhook` (sin JWT; auth = cabecera `Authorization` == `WEBHOOK_SECRET`) → misma lógica que `copier.py`. Se despliega con `.github/workflows/deploy-function.yml` al cambiar `supabase/functions/**`.
- **Lógica duplicada Python/TS**: `logic.ts`+`copier.ts` ↔ `solana.py`/`allocator.py`/`copier.py`. Cambiar ambas; `logic_test.ts` replica los tests Python.
- Config compartida: Python publica `config.yaml` en la tabla `settings` en cada ejecución. Direcciones vigiladas (top N + wallets con posiciones) en `webhook_state`; solo se llama a la API de Helius si cambian (editar = 100 créditos).
- Dedup: PK de `wallet_trades` (quien inserta el swap, copia). `copier.py` queda de respaldo cada 30 min.
- Copia con price impact > `max_price_impact_pct` (fracción, 0.15) → `failed`.
- Prueba local: `cd supabase/functions/copier-webhook; deno run -A --env-file=../../../.env dry_run.ts`.

## Auditor: cómo funciona
- Unidad = ronda (wallet × token): coste, ventas y lo abierto a precio de Jupiter (sin precio = 0). Desglose por wallet, regla de entrada del allocator y riesgo del token (alto/medio/bajo según liquidez, authorities, top10).
- Equity diario en `audit_reports` → drawdown. Propuestas por reglas con muestra mínima de 5 rondas (first_mover perdedor, riesgo alto, `no_route` > 20 %, wallet con ROI < -30 %, retraso > 15 min).

## Copier: cómo funciona
- Vigila top N (último `wallet_scores`) + wallets con posiciones abiertas (de estas solo copia ventas).
- Comparte `wallets.last_signature` con el tracker y guarda los swaps en `wallet_trades`. No copia swaps de más de `max_signal_age_min`.
- Compra: allocator → cotización real Jupiter `/swap/v1/quote` (incluye price impact) − mitad del slippage. Venta: misma fracción que la wallet (`post_balance` del swap). Sin ruta → `failed` (rug/sin liquidez), la posición sigue abierta.
- Recompras de la misma wallet: `skipped` (`copy_same_wallet_adds: false`). Todo se registra en `sim_trades` con `allocator_reason` y `delay_s`.
- Cash = capital − Σ compras(valor+fee) + Σ ventas(valor−fee). Moneda base EUR vía precio de EURC en Jupiter.
- Jupiter lite-api devuelve 429 rápido → `market._get` reintenta con backoff.

## Tracker: cómo funciona
- Descubrimiento: tokens trending de Jupiter (`lite-api.jup.ag/tokens/v2/toptrending`) → pool principal (DexScreener) → últimos swaps del pool → candidata si opera en ≥2 tokens trending. Más `tracker.manual_wallets`.
- Swaps = cambios de balance de la wallet (SOL+WSOL+USDC/USDT vs 1 token); DEX-agnóstico. token→token se ignora.
- Score: PnL realizado (coste medio) en SOL, win-rate de tokens cerrados (≥90 % vendido), días rentables, tamaño de muestra, drawdown. Pesos en `config.yaml`. Feedback = ROI de nuestras copias (≥3 cerradas).
- Exclusión permanente por `bot` (> max_trades_per_day) y `wash` (≥10 cerradas, win-rate 0, |ROI|<5 %); el resto (pocos trades, PnL negativo…) solo las saca del ranking del día.
- Descubrimiento en pools: muestra repartida en las últimas 1.000 firmas y solo swaps ≥ 0,5 SOL.

## Helius (plan gratis) – restricciones
- Solo RPC estándar (1 crédito); Enhanced API cuesta 100. 1M créditos/mes; ~8 créditos/s (cada tx de un lote cuenta).
- `getTransaction` necesita `maxSupportedTransactionVersion: 1`.
- **Nunca loguear URLs de Helius** (llevan la key y los logs de Actions son públicos): logger `httpx` en WARNING y errores sin URL.

## Estructura y comandos
- `src/copytrader/`: `config.py` (pydantic, lee `config.yaml` + `.env`), `allocator.py` y `scoring.py` (lógica pura), `solana.py` (RPC + `parse_swap`), `market.py` (Jupiter/DexScreener), `db.py`, `tracker.py`, `copier.py` (respaldo), `auditor.py`, `webhook.py`. Informes en `reports/`. Edge Function en `supabase/functions/copier-webhook/`.
- Deno 2.9 instalado (winget) para la Edge Function: `deno test`, `deno check index.ts`.
- `db/schema.sql` (Postgres idempotente), `tests/`, `.github/workflows/copytrader.yml` (un workflow, 3 crons + `workflow_dispatch`).
- `.\.venv\Scripts\python -m pytest -q` · `.\.venv\Scripts\python -m copytrader {tracker|copier|auditor|init-db}`
- En PowerShell nuevo, si `python`/`git` no se encuentran: reiniciar la terminal (PATH recién instalado).
- Usuario: nivel avanzado → decide arquitectura y revisa código; explicaciones breves.

## Decisiones tomadas
- Mercado: **cripto on-chain en Solana** (swaps en DEX vía Jupiter/Raydium/Pump.fun; fees ~0,001 $ + priority fee).
- Ejecución: **nube**. Copier en tiempo real = webhook Helius → Supabase Edge Function; tracker/auditor y copier de respaldo (30 min) en GitHub Actions (repo público). Estado en Supabase.
- Capital virtual: **200 €**, top 3 wallets.
- Tokens: **cualquiera, memecoins (Pump.fun) incluidas**; los filtros anti-rug solo registran el riesgo (no bloquean) para que el auditor mida si conviene filtrar.
- Fuentes: **solo gratuitas** de momento.
- Alertas Telegram: **no** de momento.
- Auditor: **diario**.
- Reparto **dinámico por convicción**, no igual: p.ej. si el #3 entra en un token y #1/#2 aún no, puede asignarse hasta 60 %. Los parámetros del asignador son configurables y el auditor propone ajustarlos.

## Arquitectura (4 módulos)
| # | Módulo | Qué hace | Frecuencia |
|---|--------|----------|------------|
| 1 | `tracker` | Descubre y puntúa wallets (PnL realizado, win-rate, drawdown, nº trades, antigüedad). Ranking top N. | Diario |
| 2 | `copier` | Detecta swaps nuevos del top N, `allocator` decide tamaño, abre/cierra posición simulada con precio real + slippage + gas + retraso. Copia también las ventas. | Cada 5–15 min |
| 3 | `auditor` | PnL, Sharpe, drawdown, coste del retraso/fees, rendimiento por wallet, por regla del allocator y por nivel de riesgo del token. Informe + propuestas. | Diario |
| 4 | `feedback` | Score final = score on-chain × rendimiento real de copiarla. Reordena ranking. | Diario (en 1) |

Flujo: `tracker → ranking → copier(+allocator) → trades simulados → auditor → métricas → tracker`.

### Allocator (borrador)
`peso = f(score_wallet, rango, consenso entre wallets, liquidez del token, cash libre)` con límites: máx % por operación (inicial 60 %), máx % por token, reserva mínima de cash, tamaño mínimo (si fees > X % no se copia). Todo en `config.yaml`.

## Riesgos conocidos
- **Fees vs 200 €**: en Solana son mínimas, pero simular priority fees, slippage y price impact reales.
- Retraso de copia: el whale mueve el precio; el copier registra precio al detectar, no el del whale.
- Wallets que hacen wash trading, bots MEV o airdrops: filtrarlas en el tracker.
- Memecoins / rug pulls: guardar por token mint/freeze authority, LP quemada, concentración de holders, liquidez (RugCheck API gratis) y simular que la venta puede fallar o tener price impact enorme.

## Stack propuesto
- Python 3.12, `httpx`, `pydantic`, `pandas`; un paquete `src/` con los 4 módulos.
- BD: **Supabase (Postgres gratis)** o Turso; GitHub Actions no conserva ficheros entre ejecuciones.
- Datos on-chain: **Helius** (RPC + API de transacciones parseadas, plan gratis). Precios/quotes: **Jupiter Quote API** (simula el swap real), DexScreener / Birdeye. Descubrimiento de wallets: Birdeye top traders, GMGN, Dune (o Nansen/Arkham si de pago).
- Auditoría con Claude: tarea diaria que lee la BD y escribe `reports/audit-YYYY-MM-DD.md`.
- Claves en `.env` local y en GitHub Secrets; nunca en código ni aquí.

## Reglas para Claude
- Nunca ejecutar operaciones reales ni usar claves privadas de wallets.
- Mantener este archivo compacto: actualizar "Estado actual" y "Decisiones" en cada cambio relevante.

## Decisiones abiertas
- Ninguna. Mejora futura: Helius webhooks → worker para copia en tiempo real en vez de cron de 15 min.

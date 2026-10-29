# Copy-Trader cripto on-chain (simulación)

Rastrea wallets "smart money" on-chain, copia sus swaps **en simulación (paper trading)**, audita resultados y retroalimenta el ranking. Dinero real solo tras la fase de auditoría; la decisión y ejecución real la toma el usuario.

## Estado actual
- Fase: **1 – Setup** (2026-10-29). Hecho: Python 3.12 + git instalados, `.venv`, esqueleto, esquema BD, allocator con tests (6 OK), workflow de Actions, `git init` (sin commit).
- Pendiente del usuario: cuentas GitHub, Helius y Supabase → `.env` y GitHub Secrets; luego `python -m copytrader init-db`.
- Siguiente: Fase 2 = tracker (Helius + Birdeye/GMGN). Fase 3 copier, Fase 4 auditor.

## Estructura y comandos
- `src/copytrader/`: `config.py` (pydantic, lee `config.yaml` + `.env`), `allocator.py` (lógica pura), `db.py`, `tracker.py`, `copier.py`, `auditor.py` (stubs con TODO).
- `db/schema.sql` (Postgres idempotente), `tests/`, `.github/workflows/copytrader.yml` (un workflow, 3 crons + `workflow_dispatch`).
- `.\.venv\Scripts\python -m pytest -q` · `.\.venv\Scripts\python -m copytrader {tracker|copier|auditor|init-db}`
- En PowerShell nuevo, si `python`/`git` no se encuentran: reiniciar la terminal (PATH recién instalado).
- Usuario: nivel avanzado → decide arquitectura y revisa código; explicaciones breves.

## Decisiones tomadas
- Mercado: **cripto on-chain en Solana** (swaps en DEX vía Jupiter/Raydium/Pump.fun; fees ~0,001 $ + priority fee).
- Ejecución: **nube, GitHub Actions** (cron). Estado persistente fuera del runner (ver Stack).
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
- **Repo público o privado**: el copier cada 15 min ≈ 2.900 min/mes de Actions; privado solo da 2.000 gratis. Opciones: repo público (ilimitado), copier cada 30 min, o Helius webhooks → worker (tiempo real, sin polling).

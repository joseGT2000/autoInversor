-- Esquema Postgres (Supabase). Idempotente: se puede ejecutar varias veces.

create table if not exists wallets (
    address        text primary key,
    source         text not null,              -- birdeye | gmgn | dune | manual
    first_seen     timestamptz not null default now(),
    excluded       boolean not null default false,
    exclude_reason text
);
alter table wallets add column if not exists last_signature text;     -- última firma escaneada
alter table wallets add column if not exists last_scanned_at timestamptz;
alter table wallets add column if not exists first_activity timestamptz;  -- firma más antigua vista

-- Un score por wallet y día (histórico para ver cómo evoluciona el ranking)
create table if not exists wallet_scores (
    address        text references wallets(address),
    day            date not null,
    onchain_score  numeric not null,
    copy_score     numeric,                    -- rendimiento real de copiarla (feedback)
    final_score    numeric not null,
    rank           int,
    metrics        jsonb not null default '{}', -- pnl_30d, win_rate, drawdown, n_trades...
    primary key (address, day)
);

-- Swaps detectados de las wallets seguidas
create table if not exists wallet_trades (
    signature      text primary key,
    address        text references wallets(address),
    block_time     timestamptz not null,
    side           text not null check (side in ('buy', 'sell')),
    token_mint     text not null,
    token_amount   numeric not null,
    sol_amount     numeric not null,
    detected_at    timestamptz not null default now()
);
create index if not exists wallet_trades_addr_time on wallet_trades (address, block_time desc);

-- Riesgo del token en el momento de la primera señal (no bloquea, se audita)
create table if not exists token_risk (
    token_mint     text primary key,
    checked_at     timestamptz not null default now(),
    liquidity_usd  numeric,
    mint_authority boolean,
    freeze_authority boolean,
    lp_burned_pct  numeric,
    top10_holders_pct numeric,
    rugcheck_score numeric,
    raw            jsonb
);
alter table token_risk add column if not exists created_at timestamptz;  -- creación del token (detectar snipers)

-- Nuestras operaciones simuladas
create table if not exists sim_trades (
    id             bigserial primary key,
    source_signature text references wallet_trades(signature),
    source_wallet  text references wallets(address),
    executed_at    timestamptz not null default now(),
    side           text not null check (side in ('buy', 'sell')),
    token_mint     text not null,
    token_amount   numeric not null,
    value          numeric not null,           -- en moneda base
    price          numeric not null,
    price_impact_pct numeric,
    fee            numeric not null default 0,
    delay_s        numeric,                    -- desde block_time del whale hasta nuestra ejecución
    allocator_reason text,
    status         text not null default 'filled' check (status in ('filled', 'skipped', 'failed'))
);

-- Posición abierta por token y wallet de origen (para replicar ventas parciales)
create table if not exists positions (
    token_mint     text not null,
    source_wallet  text not null references wallets(address),
    token_amount   numeric not null,
    cost_basis     numeric not null,
    opened_at      timestamptz not null default now(),
    primary key (token_mint, source_wallet)
);

-- Copia de config.yaml para la Edge Function (la sincroniza Python en cada ejecución)
create table if not exists settings (
    id             int primary key default 1 check (id = 1),
    config         jsonb not null,
    updated_at     timestamptz not null default now()
);

-- Última lista de direcciones enviada al webhook de Helius (editar cuesta 100 créditos)
create table if not exists webhook_state (
    id             int primary key default 1 check (id = 1),
    webhook_id     text not null,
    addresses      text[] not null,
    updated_at     timestamptz not null default now()
);

create table if not exists audit_reports (
    day            date primary key,
    equity         numeric not null,
    metrics        jsonb not null,
    proposals      jsonb not null default '[]',
    report_md      text
);

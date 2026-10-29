"""Carga y valida config.yaml. Los secretos vienen del entorno (.env / GitHub Secrets)."""
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class PortfolioConfig(BaseModel):
    initial_capital: float = 200.0
    currency: str = "EUR"
    top_n: int = 3


class AllocatorConfig(BaseModel):
    # % del capital total que abre cada rango del ranking en su primera entrada a un token
    base_pct_by_rank: dict[int, float] = Field(default_factory=lambda: {1: 0.30, 2: 0.25, 3: 0.20})
    # Si la wallet entra en un token que ninguna otra del top tiene, se usa este % (convicción de pionero)
    first_mover_pct: float | None = 0.60
    # % extra que se añade cuando otra wallet del top entra en un token que ya tenemos
    consensus_add_pct: float = 0.10
    max_pct_per_trade: float = 0.60
    max_pct_per_token: float = 0.70
    min_cash_reserve_pct: float = 0.10
    min_trade_value: float = 2.0
    # No copiar si las fees estimadas superan este % del tamaño de la operación
    max_fee_pct: float = 0.02


class SimulationConfig(BaseModel):
    # La cotización de Jupiter ya incluye el price impact; además se descuenta la mitad del slippage
    slippage_bps: int = 100
    # Si la cotización mueve el precio más que esto, la copia no sería ejecutable de verdad -> failed
    max_price_impact_pct: float = 0.15
    priority_fee_sol: float = 0.0001
    # Retraso extra simulado entre la detección y nuestra ejecución
    execution_delay_s: int = 5


class CopierConfig(BaseModel):
    max_signal_age_min: int = 60     # swaps más antiguos no se copian (llegaríamos tarde)
    copy_same_wallet_adds: bool = False  # recompras de la misma wallet en un token que ya copiamos
    signatures_per_wallet: int = 100
    helius_credit_budget: int = 3_000


class TrackerConfig(BaseModel):
    lookback_days: int = 30
    min_trades_30d: int = 20
    max_trades_per_day: int = 200  # por encima probablemente sea un bot
    min_wallet_age_days: int = 14
    min_closed_positions: int = 5
    max_days_inactive: float = 2  # sin swaps en más días -> fuera del ranking del día
    # Peso del rendimiento real de copiar la wallet frente a su score on-chain
    feedback_weight: float = 0.5
    min_copy_closed: int = 3  # copias cerradas mínimas para que cuente el feedback
    score_weights: dict[str, float] = Field(default_factory=lambda: {
        "roi": 0.35, "win_rate": 0.30, "profitable_days": 0.20, "sample_size": 0.15, "drawdown": 0.20,
    })
    # Descubrimiento de candidatas
    manual_wallets: list[str] = Field(default_factory=list)
    birdeye_top_traders: int = 50  # solo si hay BIRDEYE_API_KEY
    trending_interval: str = "24h"
    trending_tokens: int = 30
    min_token_liquidity_usd: float = 20_000
    pool_tx_sample: int = 100
    min_token_appearances: int = 2  # candidata si opera en >= N tokens trending
    min_discovery_swap_sol: float = 0.5  # los bots de volumen usan importes pequeños
    # Escaneo de historial
    max_wallets_scanned_per_run: int = 25
    max_tx_per_wallet: int = 200
    helius_credit_budget: int = 25_000  # por ejecución; plan gratis = 1M/mes


class Config(BaseModel):
    portfolio: PortfolioConfig = PortfolioConfig()
    allocator: AllocatorConfig = AllocatorConfig()
    simulation: SimulationConfig = SimulationConfig()
    copier: CopierConfig = CopierConfig()
    tracker: TrackerConfig = TrackerConfig()

    @property
    def helius_api_key(self) -> str:
        # Acepta la key sola o la URL del RPC de Helius (…?api-key=XXXX)
        value = os.environ["HELIUS_API_KEY"].strip()
        if value.startswith("http"):
            value = parse_qs(urlparse(value).query)["api-key"][0]
        return value

    @property
    def birdeye_api_key(self) -> str | None:
        return os.environ.get("BIRDEYE_API_KEY", "").strip() or None

    @property
    def webhook_url(self) -> str | None:
        return os.environ.get("WEBHOOK_URL", "").strip() or None

    @property
    def webhook_secret(self) -> str | None:
        return os.environ.get("WEBHOOK_SECRET", "").strip() or None

    @property
    def database_url(self) -> str:
        return os.environ["DATABASE_URL"]


def load_config(path: str | Path = "config.yaml") -> Config:
    load_dotenv()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)

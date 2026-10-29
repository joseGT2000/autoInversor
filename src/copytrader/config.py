"""Carga y valida config.yaml. Los secretos vienen del entorno (.env / GitHub Secrets)."""
import os
from pathlib import Path

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
    slippage_bps: int = 100
    priority_fee_sol: float = 0.0001
    # Retraso extra simulado entre la detección y nuestra ejecución
    execution_delay_s: int = 5


class TrackerConfig(BaseModel):
    min_trades_30d: int = 20
    max_trades_per_day: int = 200  # por encima probablemente sea un bot
    min_wallet_age_days: int = 14
    # Peso del rendimiento real de copiar la wallet frente a su score on-chain
    feedback_weight: float = 0.5


class Config(BaseModel):
    portfolio: PortfolioConfig = PortfolioConfig()
    allocator: AllocatorConfig = AllocatorConfig()
    simulation: SimulationConfig = SimulationConfig()
    tracker: TrackerConfig = TrackerConfig()

    @property
    def helius_api_key(self) -> str:
        return os.environ["HELIUS_API_KEY"]

    @property
    def database_url(self) -> str:
        return os.environ["DATABASE_URL"]


def load_config(path: str | Path = "config.yaml") -> Config:
    load_dotenv()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)

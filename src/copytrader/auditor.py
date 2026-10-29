"""Módulo 3: audita todas las operaciones simuladas y propone mejoras. Diario."""
import logging

from copytrader.config import Config

log = logging.getLogger(__name__)


def run(cfg: Config) -> None:
    # TODO Fase 4:
    # 1. Mark-to-market de posiciones abiertas (Jupiter price API)
    # 2. Métricas: PnL, Sharpe, max drawdown, win-rate, coste de retraso y fees
    # 3. Desglose por wallet, por regla del allocator (reason) y por nivel de riesgo del token
    # 4. Escribir reports/audit-YYYY-MM-DD.md y guardar fila en audit_reports
    log.info("auditor: pendiente de implementar")

"""Módulo 1 + 4: descubre y puntúa wallets, incorpora el feedback de la simulación. Diario."""
import logging

from copytrader.config import Config

log = logging.getLogger(__name__)


def run(cfg: Config) -> None:
    # TODO Fase 2:
    # 1. Descubrir candidatas (Birdeye top traders / GMGN / Dune) -> tabla wallets
    # 2. Descargar swaps 30d vía Helius (parsed transactions) -> wallet_trades
    # 3. Filtrar bots (max_trades_per_day), wallets nuevas, wash trading
    # 4. Score on-chain: PnL realizado, win-rate, drawdown, consistencia
    # 5. Feedback: mezclar con el PnL real de copiarla (feedback_weight)
    # 6. Guardar wallet_scores del día y marcar el top_n activo
    log.info("tracker: pendiente de implementar")

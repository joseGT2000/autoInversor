"""Módulo 2: detecta swaps nuevos del top N y los copia en simulación. Cada 5-15 min."""
import logging

from copytrader.config import Config

log = logging.getLogger(__name__)


def run(cfg: Config) -> None:
    # TODO Fase 3:
    # 1. Leer top_n activo y el último signature procesado por wallet
    # 2. Pedir a Helius las transacciones SWAP nuevas de cada wallet
    # 3. Compras: allocator.size_buy -> quote de Jupiter (precio real + price impact) -> sim_trades
    # 4. Ventas: allocator.size_sell sobre la posición atribuida a esa wallet
    # 5. Guardar token_risk (RugCheck) de cada token nuevo, sin bloquear
    log.info("copier: pendiente de implementar")

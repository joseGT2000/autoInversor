"""Punto de entrada: python -m copytrader {tracker|copier|auditor|init-db}."""
import argparse
import logging

from copytrader import auditor, copier, db, tracker
from copytrader.config import load_config

COMMANDS = {
    "tracker": tracker.run,
    "copier": copier.run,
    "auditor": auditor.run,
    "init-db": lambda cfg: db.init_schema(cfg.database_url),
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="copytrader")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx registra las URLs completas y la de Helius lleva la API key (los logs de Actions son públicos)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    COMMANDS[args.command](load_config(args.config))


if __name__ == "__main__":
    main()

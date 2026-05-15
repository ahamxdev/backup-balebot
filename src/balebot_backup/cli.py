from __future__ import annotations

import argparse
import logging
import sys

from .config import load_settings
from .service import BackupSenderService


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


def run_worker(dotenv_path: str) -> int:
    settings = load_settings(dotenv_path)
    setup_logging(settings.log_level)

    service = BackupSenderService(settings)
    service.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bale backup sender bot")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to env file (default: .env)",
    )
    args = parser.parse_args(argv)

    try:
        return run_worker(args.env_file)
    except Exception as exc:
        logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")
        logging.exception("fatal error: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

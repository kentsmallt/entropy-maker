"""Logging that cannot print a private key."""

from __future__ import annotations

import logging

from entropy_maker.redact import redact


class RedactFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str) -> None:
    logger = logging.getLogger("entropy_maker")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(RedactFormatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False

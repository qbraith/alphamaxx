"""Logging setup. Call configure_logging() once from an entry point;
modules use `log = logging.getLogger(__name__)`."""

from __future__ import annotations

import logging

from alphamaxx.config import settings

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"


def configure_logging(level: str | None = None) -> None:
    logging.basicConfig(level=level or settings.LOG_LEVEL, format=_FORMAT)
    # yfinance and urllib3 are chatty at INFO
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)

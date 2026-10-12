"""Structured JSON logging shared by the CLI and the API."""

from __future__ import annotations

import logging
import os

import structlog

logging.basicConfig(format="%(message)s", level=os.environ.get("FOOTNOTE_LOG_LEVEL", "INFO"))
for noisy in ("httpx", "huggingface_hub", "sentence_transformers", "urllib3", "filelock", "numexpr"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(os.environ.get("FOOTNOTE_LOG_LEVEL", "INFO"))),
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger("footnote")

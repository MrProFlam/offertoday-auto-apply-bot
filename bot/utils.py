from __future__ import annotations

import logging
import random
import re
import time
from logging.handlers import QueueHandler

import yaml
from rich.logging import RichHandler

from .runtime import CONFIG_PATH, DATA_DIR, ROOT


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def setup_logging(level: str = "INFO", log_queue=None) -> logging.Logger:
    logger = logging.getLogger("offertoday")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if logger.handlers:
        if log_queue is not None and not any(isinstance(h, QueueHandler) for h in logger.handlers):
            logger.addHandler(QueueHandler(log_queue))
        return logger
    handlers = [RichHandler(rich_tracebacks=True, show_path=False)]
    if log_queue is not None:
        handlers.append(QueueHandler(log_queue))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=handlers,
    )
    return logger


def human_delay(cfg: dict, action: str = "apply_action") -> float:
    """Randomized pause between actions.

    IMPORTANT: only call this during the applying/chatting phase. Scraping
    intentionally has no artificial delay.
    """
    delays = cfg.get("delays", {})
    lo, hi = delays.get(action, [3.0, 5.0])
    multiplier = float(delays.get("speed_multiplier", 1.0))
    seconds = random.uniform(float(lo), float(hi)) * multiplier
    logging.getLogger("offertoday").debug("pause %.2fs (%s)", seconds, action)
    time.sleep(seconds)
    return seconds


def parse_salary_min(text: str | None) -> float | None:
    """Best-effort conversion of OfferToday salary text to a monthly HKD number."""
    if not text:
        return None
    t = str(text).upper().replace(",", "").replace("HK$", "$")
    # Hourly: $50-60/H -> approximate monthly (2080 hours / 12 months)
    m = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*K?\s*-\s*(\d+(?:\.\d+)?)\s*K?\s*/\s*H", t)
    if m:
        return float(m.group(1)) * 173
    # Monthly, K notation: $25K-40K/M
    m = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*K\s*-\s*(\d+(?:\.\d+)?)\s*K\s*/\s*M", t)
    if m:
        return float(m.group(1)) * 1000
    # Monthly, plain: $25000-40000/M
    m = re.search(r"\$?\s*(\d{4,6})\s*-\s*(\d{4,6})\s*/\s*M", t)
    if m:
        return float(m.group(1))
    # Single K value
    m = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*K\s*/\s*M", t)
    if m:
        return float(m.group(1)) * 1000
    return None

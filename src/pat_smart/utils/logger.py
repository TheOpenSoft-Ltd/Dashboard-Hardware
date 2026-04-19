import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.logging import RichHandler


class TopicFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt_utc = datetime.fromtimestamp(record.created, tz=timezone.utc)
        dt_bkk = dt_utc.astimezone(ZoneInfo("Asia/Bangkok"))

        utc_str = dt_utc.strftime("%d/%b/%Y:%H:%M:%S +0000")
        bkk_str = dt_bkk.strftime("%d/%b/%Y:%H:%M:%S %z")

        return f"{utc_str} | {bkk_str}"

    def format(self, record):
        timestamp = self.formatTime(record)
        message = record.getMessage()
        return f"[{timestamp}] {message}"


def setup_logger(name: str = "sandbox") -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # 🔥 Force UTF-8 console
    console = Console(file=sys.stdout, force_terminal=True, color_system="auto")

    handler = RichHandler(
        console=console,
        show_time=False,
        show_level=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )

    handler.setFormatter(TopicFormatter())
    logger.addHandler(handler)

    return logger


def log_topic(logger, topic: str, payload: dict):
    payload_json = json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=False,  # 🔥 important for UTF-8
    )

    logger.info(
        f"[cyan]TOPIC[/cyan] | "
        f"[yellow]{topic}[/yellow] | "
        f"[magenta]PAYLOAD[/magenta] | "
        f"[green]{payload_json}[/green]"
    )

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


def save_log_to_file(log_dir: Path, topic: str, payload: dict):
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{topic.replace('/', '_')}.json"
    filepath = log_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def log_topic(logger, topic: str, payload: dict, log_dir: Path | None = None):
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

    if log_dir:
        save_log_to_file(log_dir, topic, payload)

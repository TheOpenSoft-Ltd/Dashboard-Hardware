import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pat_smart.config import Settings

settings = Settings()  # type: ignore


class FileService:
    THAILAND_TZ = ZoneInfo("Asia/Bangkok")

    def __init__(self):
        log_dir = settings.LOG_DIR
        self.log_dir = log_dir
        self.filename_prefix = settings.LOG_FILE_PREFIX
        self._lock = threading.Lock()

    def _get_log_path(self) -> Path:
        now = datetime.now(tz=self.THAILAND_TZ)
        filename = f"{self.filename_prefix}_{now.strftime('%Y%m%d')}.log"
        return Path(self.log_dir) / filename

    def _ensure_log_dir(self) -> None:
        log_path = Path(self.log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

    def _write(self, message: str) -> None:
        self._ensure_log_dir()
        log_path = self._get_log_path()
        timestamp = datetime.now(tz=ZoneInfo("UTC")).isoformat()
        log_line = f"{timestamp} {message}\n"
        with self._lock:
            with open(log_path, "a") as f:
                f.write(log_line)

    def save_log(self, data: dict, topic: str = "") -> None:
        payload_json = json.dumps(data)
        formatted = f"[cyan]TOPIC[/cyan] | [yellow]{topic}[/yellow] | [magenta]PAYLOAD[/magenta] | [green]{payload_json}[/green]"
        self._write(formatted)

    def save_raw(self, message: str) -> None:
        self._write(message)

    def read_logs(self, days: int = 7) -> list[dict]:
        logs = []
        log_path = Path(self.log_dir)
        if not log_path.exists():
            return logs

        now = datetime.now(tz=self.THAILAND_TZ)
        for i in range(days):
            date = now - timedelta(days=i)
            filename = f"{self.filename_prefix}_{date.strftime('%Y%m%d')}.log"
            file_path = log_path / filename
            if file_path.exists():
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        lines = content.strip().splitlines()
                        for line in lines:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                if "[cyan]TOPIC[/cyan]" in line:
                                    parts = line.split(" | ", 3)
                                    if len(parts) >= 4:
                                        log_entry = {
                                            "_timestamp": (
                                                parts[0].split(" ", 1)[0]
                                                if " " in parts[0]
                                                else parts[0]
                                            ),
                                            "topic": parts[1]
                                            .replace("[yellow]", "")
                                            .replace("[/yellow]", ""),
                                            "payload": json.loads(
                                                parts[3]
                                                .replace("[green]", "")
                                                .replace("[/green]", "")
                                            ),
                                        }
                                        logs.append(log_entry)
                                    else:
                                        parts = line.split(" ", 1)
                                        if len(parts) == 2:
                                            data = json.loads(parts[1])
                                            data["_timestamp"] = parts[0]
                                            logs.append(data)
                                else:
                                    parts = line.split(" ", 1)
                                    if len(parts) == 2:
                                        data = json.loads(parts[1])
                                        data["_timestamp"] = parts[0]
                                        logs.append(data)
                            except (json.JSONDecodeError, IndexError):
                                continue
                except OSError:
                    continue
        logs.reverse()
        return logs

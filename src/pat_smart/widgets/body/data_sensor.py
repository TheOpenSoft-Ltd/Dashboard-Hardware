import json
import subprocess
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Static

from pat_smart.config import Settings
from pat_smart.services.redis.client import RedisClient

settings = Settings()  # type: ignore


def _check_service(name: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


class StatusGroup(Vertical):
    def compose(self) -> ComposeResult:
        services = [
            ("Radar  ", "pat-smart-radar.service"),
            ("Dropler", "pat-smart-dropler.service"),
            ("CCTV   ", "pat-smart-stream.service"),
        ]
        for name, service in services:
            active = _check_service(service)
            status = "[green]Active[/green] 🟢" if active else "[red]Inactive[/red] 🔴"
            yield Label(f"[yellow]{name}:[/yellow]  {status}")

    def on_mount(self) -> None:
        self.border_title = "Status"


_redis_client_radar: RedisClient | None = None


class RadarGroup(Vertical):
    def __init__(self) -> None:
        super().__init__()
        self._level_label: Label | None = None

    def compose(self) -> ComposeResult:
        self._level_label = Label("[yellow]Level:[/yellow] -- m")
        yield self._level_label

    def on_mount(self) -> None:
        self.border_title = "Radar"
        self._setup_redis()

    def _setup_redis(self) -> None:
        redis_client_radar = None
        if redis_client_radar is None:
            redis_client_radar = RedisClient()
            if redis_client_radar.connect():
                redis_client_radar.subscribe(
                    settings.REDIS_RADAR_CHANNEL, self._on_redis_message
                )
                redis_client_radar.start_listening()
            else:
                redis_client_radar = None

    def _on_redis_message(self, data) -> None:
        if isinstance(data, str):
            json_data = json.loads(data)
        elif isinstance(data, dict):
            json_data = data
        else:
            return
        if self._level_label:
            value = json_data.get("level", "--")
            self._level_label.update(f"[yellow]Level:[/yellow] {value} m")


class DroplerGroup(Vertical):
    def __init__(self) -> None:
        super().__init__()
        self._velocity: Static | None = None
        self._flowrate: Static | None = None
        self._cumulative: Static | None = None
        self._temp: Static | None = None

    def compose(self) -> ComposeResult:
        self._velocity = Label(
            "[yellow]Velocity[/yellow]\n -- m/s", id="dropler_velocity"
        )
        self._flowrate = Label(
            "[yellow]Flowrate[/yellow]\n -- m3/h", id="dropler_flowrate"
        )
        self._cumulative = Label(
            "[yellow]Cumulative Flow[/yellow]\n -- m3",
            id="dropler_cumulative",
        )
        self._temp = Label("[yellow]Temperature[/yellow]\n -- °C", id="dropler_temp")
        yield self._velocity
        yield self._flowrate
        yield self._cumulative
        yield self._temp

    def on_mount(self) -> None:
        self.border_title = "Dropler"
        self._setup_redis()

    def _setup_redis(self) -> None:
        redis_client_dropler = None
        if redis_client_dropler is None:
            redis_client_dropler = RedisClient()
            if redis_client_dropler.connect():
                redis_client_dropler.subscribe(
                    settings.REDIS_DROPLER_CHANNEL, self._on_redis_message
                )
                redis_client_dropler.start_listening()
            else:
                redis_client_dropler = None

    def _on_redis_message(self, data) -> None:
        print(data)
        if isinstance(data, str):
            json_data = json.loads(data)
        elif isinstance(data, dict):
            json_data = data
        else:
            return

        if self._velocity:
            self._velocity.update(
                f"[yellow]Velocity[/yellow]\n {json_data.get('velocity', '--sss ')} m/s"
            )
        if self._flowrate:
            self._flowrate.update(
                f"[yellow]Flowrate[/yellow]\n {json_data.get('flowrate', '--')} m3/h"
            )
        if self._cumulative:
            self._cumulative.update(
                f"[yellow]Cumulative Flow[/yellow]\n {json_data.get('cumulative_flow', '--')} m3"
            )
        if self._temp:
            self._temp.update(
                f"[yellow]Temperature[/yellow]\n {json_data.get('temperature', '--')} °C"
            )


class DataSensor(Vertical):
    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        yield StatusGroup()
        yield RadarGroup()
        yield DroplerGroup()

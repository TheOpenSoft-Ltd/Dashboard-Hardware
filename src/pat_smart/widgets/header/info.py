import socket
from importlib.metadata import version

import psutil
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label

from pat_smart.config import Settings

setting = Settings()  # type: ignore
version = version("pat-smart")


def _check_cloud(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return True
    except Exception:
        return False


class SensorInfo(Vertical):
    def __init__(self) -> None:
        super().__init__()
        self._cpu_label = Label()
        self._mem_label = Label()
        self._disk_label = Label()
        self._cloud_label = Label()

    def compose(self) -> ComposeResult:
        yield Label(f"[yellow]Pat Smart Version:[/yellow] {version}")
        yield Label(f"[yellow]Station:[/yellow]           {setting.STATION_NAME}")
        yield Label(f"[yellow]Device ID:[/yellow]         {setting.DEVICE_ID}")
        yield Label(f"[yellow]Mode:[/yellow]              {setting.MODE}")
        yield self._cloud_label
        yield self._cpu_label
        yield self._mem_label
        yield self._disk_label

    def on_mount(self) -> None:
        self._update_stats()
        self._update_cloud()
        self.set_interval(1.0, self._update_stats)
        self.set_interval(5.0, self._update_cloud)

    def _update_stats(self) -> None:
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent

        self._cpu_label.update(f"[yellow]CPU:[/yellow]               {cpu} %")
        self._mem_label.update(f"[yellow]MEM:[/yellow]               {mem} %")
        self._disk_label.update(f"[yellow]DISK:[/yellow]              {disk} %")

    def _update_cloud(self) -> None:
        online = _check_cloud(setting.MQTT_HOST, setting.MQTT_PORT)
        status = "[green]Online 🟢[/green]" if online else "[red]Offline 🔴[/red]"
        self._cloud_label.update(f"[yellow]Cloud:[/yellow]             {status}")

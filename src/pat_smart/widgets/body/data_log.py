from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Label, ListItem, ListView

from pat_smart.widgets.body.data_log_detail import DataLogDetail


class DataLog(Vertical):
    def __init__(self) -> None:
        super().__init__()
        self._logs: list[tuple[str, str, str]] = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search... (Ctrl+f)", classes="search-input")
        with Horizontal():
            yield ListView(classes="log-list")
            yield DataLogDetail()

    def on_mount(self) -> None:
        self._add_sample_logs()

    def _add_sample_logs(self) -> None:
        self._logs = [
            ("10:00:00", "INFO", "System started"),
            ("10:01:00", "WARNING", "High CPU usage at 85%"),
            ("10:02:00", "ERROR", "Connection timeout to MQTT broker"),
            ("10:03:00", "INFO", "Reconnected successfully"),
            ("10:04:00", "WARNING", "Memory usage high: 78%"),
            ("10:05:00", "ERROR", "Failed to read Modbus sensor"),
            ("10:06:00", "INFO", "Sensor data received: temp=25.5°C"),
        ]
        self._render_logs(self._logs)

    def _render_logs(self, logs: list[tuple[str, str, str]]) -> None:
        list_view = self.query_one(ListView)
        list_view.clear()
        for time, level, message in logs:
            color = self._get_level_color(level)
            list_view.append(
                ListItem(Label(f"[b]{time}[/b] [{color}]{level}[/] {message}"))
            )

    def _get_level_color(self, level: str) -> str:
        colors = {"INFO": "green", "WARNING": "yellow", "ERROR": "red"}
        return colors.get(level, "white")

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.lower()
        if not query:
            self._render_logs(self._logs)
        else:
            filtered = [
                (t, l, m)
                for t, l, m in self._logs
                if query in m.lower() or query in l.lower()
            ]
            self._render_logs(filtered)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        list_view = self.query_one(ListView)
        index = list_view.index
        detail = self.query_one(DataLogDetail)
        if index is not None and 0 <= index < len(self._logs):
            time, level, message = self._logs[index]
            detail.set_content(time, level, message)
            detail.display = True
        else:
            detail.display = False

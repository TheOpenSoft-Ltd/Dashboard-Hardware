from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label


class DataSensor(Vertical):
    def compose(self) -> ComposeResult:
        yield Label("[b]Temperature:[/b] 25.10°C")
        yield Label("[b]Humidity:[/b] 60%")
        yield Label("[b]Pressure:[/b] 1013 hPa")

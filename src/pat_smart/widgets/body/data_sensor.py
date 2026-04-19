from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Static


class StatusGroup(Vertical):
    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Label("[yellow]Radar:[/yellow]   [green]🟢 Active[/green]")
        yield Label("[yellow]Dropler:[/yellow] [green]🟢 Active[/green]")
        yield Label("[yellow]CCTV:[/yellow]    [green]🟢 Active[/green]")

    def on_mount(self) -> None:
        self.border_title = "Status"


class RadarGroup(Vertical):
    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Label("[yellow]Level:[/yellow] 1.2 m")

    def on_mount(self) -> None:
        self.border_title = "Radar"


class DroplerGroup(Vertical):
    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("[yellow]Velocity[/yellow]\n  2.5 m/s", id="dropler_velocity")
        yield Static("[yellow]Flowrate[/yellow]\n  100 m3/h", id="dropler_flowrate")
        yield Static(
            "[yellow]Cumulative Flow[/yellow]\n  500000000000000 m3",
            id="dropler_cumulative",
        )
        yield Static("[yellow]Temperature[/yellow]\n  25.0°C", id="dropler_temp")

    def on_mount(self) -> None:
        self.border_title = "Dropler"


class DataSensor(Vertical):
    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        yield StatusGroup()
        yield RadarGroup()
        yield DroplerGroup()

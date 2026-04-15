from datetime import datetime

from textual.app import ComposeResult, RenderResult
from textual.widgets import Static


class LogoApp(Static):
    def __init__(self) -> None:
        super().__init__()
        self._time_label = Static()

    def compose(self) -> ComposeResult:
        yield Static(
            "[cyan]"
            "██████╗  █████╗ ████████╗    ███████╗███╗   ███╗ █████╗ ██████╗ ████████╗\n"
            "██╔══██╗██╔══██╗╚══██╔══╝    ██╔════╝████╗ ████║██╔══██╗██╔══██╗╚══██╔══╝\n"
            "██████╔╝███████║   ██║       ███████╗██╔████╔██║███████║██████╔╝   ██║   \n"
            "██╔═══╝ ██╔══██║   ██║       ╚════██║██║╚██╔╝██║██╔══██║██╔══██╗   ██║   \n"
            "██║     ██║  ██║   ██║       ███████║██║ ╚═╝ ██║██║  ██║██║  ██║   ██║   \n"
            "╚═╝     ╚═╝  ╚═╝   ╚═╝       ╚══════╝╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   \n"
            "[/cyan]"
        )
        yield self._time_label

    def on_mount(self) -> None:
        self._update_time()
        self.set_interval(1.0, self._update_time)

    def _update_time(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._time_label.update(f"[yellow]{now}[/yellow]")

    def render(self) -> RenderResult:
        return ""

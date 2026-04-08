from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Digits, Header


class AppHeader(Horizontal):

    def compose(self) -> ComposeResult:
        return super().compose()


class AppBody(Horizontal):
    def compose(self) -> ComposeResult:
        return super().compose()


class MainScreen(App):
    CSS = """
    Screen {align: center middle; }
    Digits {width: auto}
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Digits("")

    def on_ready(self) -> None:
        self.update_clock()
        self.set_interval(1, self.update_clock)

    def update_clock(self) -> None:
        clock = datetime.now().time()
        self.query_one(Digits).update(f"{clock:%T}")

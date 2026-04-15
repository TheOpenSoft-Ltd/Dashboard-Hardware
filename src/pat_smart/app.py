from textual.app import App, ComposeResult
from textual.containers import HorizontalGroup

from pat_smart.widgets.body.data_log import DataLog
from pat_smart.widgets.body.data_sensor import DataSensor
from pat_smart.widgets.header.info import SensorInfo
from pat_smart.widgets.header.logo import LogoApp
from pat_smart.widgets.header.shortcut_key import ShortCutKey


class AppHeader(HorizontalGroup):
    def compose(self) -> ComposeResult:
        yield SensorInfo()
        yield ShortCutKey()
        yield LogoApp()


class AppBody(HorizontalGroup):
    def compose(self) -> ComposeResult:
        yield DataSensor()
        yield DataLog()


class MainScreen(App):
    CSS_PATH = "style.css"
    BINDINGS = [("q", "exit_app", "Exit"), ("s", "save", "Save")]

    def compose(self) -> ComposeResult:
        yield AppHeader()
        yield AppBody()

    def action_exit_app(self) -> None:
        self.exit()

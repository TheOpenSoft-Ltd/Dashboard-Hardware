import json

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Input

from pat_smart.services.file.file import FileService


class LogListSelected(Message):
    def __init__(self, index: int) -> None:
        super().__init__()
        self.index = index


class LogList(ScrollView):
    BINDINGS = [
        Binding("up,w,k", "select_previous", "Previous", show=False),
        Binding("down,s,j", "select_next", "Next", show=False),
        Binding("enter", "confirm_select", "Select", show=False),
        Binding("left,h", "scroll_left", "Scroll Left", show=False),
        Binding("right,l", "scroll_right", "Scroll Right", show=False),
        Binding("g", "move_top", "Top", show=False),
        Binding("G", "move_bottom", "Bottom", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._logs: list[tuple[str, str, str]] = []
        self._max_width = 0
        self._selected_index: int | None = 0

    def set_logs(self, logs: list[tuple[str, str, str]]) -> None:
        self._logs = logs
        self._max_width = max(
            (
                len(time) + len(level) + len(message) + 4
                for time, level, message in logs
            ),
            default=0,
        )
        self.virtual_size = Size(self._max_width, len(logs))
        self.refresh()

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        index = y + scroll_y
        width, height = self.size
        if index >= len(self._logs):
            return Strip.blank(width, self.rich_style)

        time, level, message = self._logs[index]
        color = self._get_level_color(level)
        text = f"[b]{time}[/b] [{color}]{level}[/] {message}"
        from rich.style import Style
        from rich.text import Text

        text_obj = Text.from_markup(text)
        if index == self._selected_index:
            text_obj.style = Style(bgcolor="#606060", bold=True)
        else:
            text_obj.style = Style(bgcolor="#262626")
        strip = Strip(text_obj.render(self.app.console), text_obj.cell_len)
        strip = strip.crop_extend(scroll_x, scroll_x + width, None)
        return strip

    def action_select_previous(self) -> None:
        if self._selected_index is not None and self._selected_index > 0:
            self._selected_index -= 1
            self.scroll_to(y=self._selected_index, animate=True)
            self.refresh()

    def action_select_next(self) -> None:
        if (
            self._selected_index is not None
            and self._selected_index < len(self._logs) - 1
        ):
            self._selected_index += 1
            self.scroll_to(y=self._selected_index, animate=True)
            self.refresh()

    def action_confirm_select(self) -> None:
        if self._selected_index is not None:
            self.post_message(LogListSelected(self._selected_index))

    def action_move_top(self) -> None:
        if self._logs:
            self._selected_index = 0
            self.scroll_to(y=0, animate=False)
            self.refresh()

    def action_move_bottom(self) -> None:
        if self._logs:
            self._selected_index = len(self._logs) - 1
            self.scroll_to(y=self._selected_index, animate=False)
            self.refresh()

    def _get_level_color(self, level: str) -> str:
        colors = {"INFO": "green", "WARNING": "yellow", "ERROR": "red"}
        return colors.get(level, "white")

    def on_click(self, event: events.Click) -> None:
        index = event.y + self.scroll_offset.y
        if 0 <= index < len(self._logs):
            self._selected_index = index
            self.action_confirm_select()

    def on_mount(self) -> None:
        self.virtual_size = Size(0, 0)


class DataLog(Vertical):
    BINDINGS = [
        Binding("r", "reload", "Reload", show=True),
    ]

    REFRESH_INTERVAL = 1

    def __init__(self, group: str = "Logs") -> None:
        super().__init__()
        self.group = group
        self._logs: list[tuple[str, str, str]] = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search...")
        with Horizontal():
            yield LogList()

    def on_mount(self) -> None:
        log_list = self.query_one(LogList)
        log_list.border_title = self.group
        self._load_logs()
        self.set_timer(self.REFRESH_INTERVAL, self._auto_reload)

    def _auto_reload(self) -> None:
        self._load_logs()
        self.set_timer(self.REFRESH_INTERVAL, self._auto_reload)

    def action_reload(self) -> None:
        self._load_logs()

    def _load_logs(self) -> None:
        file_service = FileService()
        logs = file_service.read_logs(days=7)
        self._logs = []
        for log in logs:
            timestamp = log.get("_timestamp") or log.get("date_time", "")
            time_str = ""
            if timestamp:
                try:
                    from datetime import datetime, timedelta, timezone

                    ts = timestamp.replace("Z", "+00:00")
                    if "T" in timestamp:
                        dt = datetime.fromisoformat(ts.replace(" ", "T"))
                        utc_dt = dt.replace(tzinfo=timezone.utc)
                        ict_offset = timedelta(hours=7)
                        ict_dt = utc_dt.astimezone(timezone(ict_offset))
                        time_str = ict_dt.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        time_str = timestamp[:19] if len(timestamp) >= 19 else timestamp
                except Exception:
                    time_str = timestamp[:19] if len(timestamp) >= 19 else timestamp
            level = "INFO"
            raw_log = json.dumps(log)
            self._logs.append((time_str, level, raw_log))
        if not self._logs:
            self._logs = [
                ("--:--:--", "INFO", "No logs found"),
            ]
        self._render_logs(self._logs)

    def _render_logs(self, logs: list[tuple[str, str, str]]) -> None:
        log_list = self.query_one(LogList)
        log_list.set_logs(logs)

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

from textual.widgets import Pretty


class DataLogDetail(Pretty):
    DEFAULT_CSS = """
    DataLogDetail {
        display: none;
    }
    """

    def __init__(self) -> None:
        super().__init__(object="", classes="log-detail")

    def set_content(self, time: str, level: str, message: str) -> None:
        data = {
            "time": time,
            "level": level,
            "message": message,
        }
        self.update(data)

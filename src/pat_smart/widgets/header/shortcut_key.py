from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Label


class ShortCutKey(Vertical):
    def compose(self) -> ComposeResult:
        app = self.app
        if app and hasattr(app, "BINDINGS"):
            for binding in app.BINDINGS:
                if isinstance(binding, Binding):
                    yield Label(f"[cyan]<{binding.key}>[/cyan]  {binding.description}")
                elif len(binding) >= 3:
                    key, _, description = binding
                    yield Label(f"[cyan]<{key}>[/cyan]  {description}")

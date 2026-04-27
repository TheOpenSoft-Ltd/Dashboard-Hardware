from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Label


class ShortCutKey(Vertical):
    def compose(self) -> ComposeResult:
        app = self.app
        bindings: list[tuple[str, str]] = []
        if app and hasattr(app, "BINDINGS"):
            for binding in app.BINDINGS:
                if isinstance(binding, Binding):
                    bindings.append((binding.key, binding.description))
                elif len(binding) >= 3:
                    key, _, description = binding
                    bindings.append((key, description))
        if app and hasattr(app, "screen"):
            self._collect_from_widget(app.screen, bindings)
        unique = []
        seen = set()
        for k, d in bindings:
            if k not in seen:
                seen.add(k)
                unique.append((k, d))
        for key, description in unique:
            yield Label(f"[cyan]<{key}>[/cyan]  {description}")

    def _collect_from_widget(self, widget, bindings: list[tuple[str, str]]) -> None:
        if hasattr(widget, "BINDINGS") and widget.BINDINGS:
            for binding in widget.BINDINGS:
                if isinstance(binding, Binding):
                    bindings.append((binding.key, binding.description))
                elif len(binding) >= 3:
                    key, _, description = binding
                    bindings.append((key, description))
        if hasattr(widget, "children"):
            for child in widget.children:
                try:
                    self._collect_from_widget(child, bindings)
                except Exception:
                    pass

# Agent Guidelines for pat-smart (Dashboard-Hardware)

## Project Overview

pat-smart is a Python TUI application for monitoring MQTT and MODBUS devices. It uses the Textual framework for the UI, pydantic for configuration, and supports MQTT/Modbus communication.

- **Python**: 3.11+
- **Package Manager**: uv
- **Entry Point**: `pat-smart` command (defined in pyproject.toml)

---

## Build & Run Commands

### Install Dependencies
```bash
uv sync
```

### Run Application
```bash
pat-smart
```

### Run in Development Mode
```bash
# Using uv run with textual CLI
uv run textual run --dev pat_smart.__main__:run
```

### Run Dev Console (for debugging)
```bash
# Terminal 1: Start dev console
uv run textual console

# Terminal 2: Run app in dev mode
uv run textual run --dev pat_smart.__main__:run
```

### Serve App in Browser
```bash
uv run textual serve pat_smart.__main__:run
```

### Run Sandbox (Test Data Generator)
```bash
pat-smart sandbox
```

### Run CLI Check Connection
```bash
pat-smart check-connection
```

### Run Single Test
```bash
# pytest (if tests exist)
pytest tests/test_specific.py::test_function_name -v

# or with uv
uv run pytest tests/test_specific.py::test_function_name -v
```

---

## Code Style Guidelines

### Formatting Tools
- **black**: Code formatter (line-length: 88, configured in pyproject.toml)
- **isort**: Import sorter (profile: black)

```bash
# Format all code
black src/ tests/

# Sort imports
isort src/ tests/
```

### Type Hints
- Use Python 3.11+ type hints (`|` union types, not `Union`)
- Add return type annotations to all functions/methods
- Use `# type: ignore` sparingly

### Naming Conventions
- **Classes**: PascalCase (`MQTTClient`, `MainScreen`)
- **Functions/Methods**: snake_case (`connect()`, `_heartbeat_loop`)
- **Constants**: UPPER_SNAKE_CASE (`STATUS_INTERVAL`)
- **Files**: snake_case (`mqtt_client.py`, `data_sensor.py`)

### Import Organization
Order (isort handles this automatically):
1. Standard library
2. Third-party packages
3. Local application imports

Example:
```python
import json
import socket
import threading

import paho.mqtt.client as mqtt
from pydantic import ValidationError
from textual.app import App

from pat_smart.common.enum import SensorStatusType
from pat_smart.config import Settings
```

### Error Handling
- Use specific exception types (e.g., `ConnectionRefusedError`, `socket.timeout`)
- Avoid bare `except:` clauses
- Use try/except blocks for expected error conditions
- Log errors appropriately using the project's logging utility

### Project Structure
```
src/pat_smart/
├── __main__.py          # Entry point
├── app.py               # Main Textual app
├── config.py            # Pydantic settings
├── cli/
│   └── main.py          # CLI commands
├── common/
│   ├── enum.py          # Enumerations
│   └── constants.py     # Constants
├── models/
│   └── message.py       # Data models
├── modules/
│   └── sandbox/         # Sandbox runner/worker
├── services/
│   ├── mqtt/            # MQTT client/publisher
│   └── modbus/          # Modbus client
├── ui/
│   └── screens/         # UI screens
├── utils/
│   ├── generator.py     # Utilities
│   └── logger.py        # Logging
└── widgets/             # Textual widgets
```

### Configuration
- Use Pydantic `BaseSettings` with `SettingsConfigDict` for environment-based config
- Store sensitive config in `.env` file
- Use type annotations for all settings fields

### Concurrency
- Use `threading` for background tasks (e.g., heartbeat)
- Use `threading.Event` for thread synchronization
- Set daemon=True for background threads

---

## Testing

Currently there are no tests in the `tests/` directory. When adding tests:
- Use pytest as the test framework
- Place tests in `tests/` directory
- Follow pytest naming conventions (`test_*.py`)

---

## Dependencies

Key dependencies (from pyproject.toml):
- `paho-mqtt` - MQTT client
- `pymodbus` - Modbus client
- `pydantic` / `pydantic-settings` - Configuration
- `textual` - TUI framework
- `rich-click` - CLI framework
- `typer` - CLI framework

---

## Notes

- The application uses TLS for MQTT connections (configured in mqtt/client.py)
- LWT (Last Will and Testament) is configured for sensor status
- The project uses a virtual environment (`.venv/`) managed by uv
- Configuration is loaded from `.env` file
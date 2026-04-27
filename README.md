# Dashboard-Hardware

## Install Dependencies

```bash
uv sync
```

## Run Application

```bash
pat-smart
```

## Development

### Dev Mode (with live CSS editing + console)
```bash
uv run textual run --dev pat_smart.__main__:run
```

### Dev Console (run in separate terminal)
```bash
uv run textual console
```

### Serve in Browser
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

---

## MQTT Broker

#### Install VerneMQ

```bash
docker run -e "DOCKER_VERNEMQ_ALLOW_ANONYMOUS=on" -p 1883:1883 -e "DOCKER_VERNEMQ_ACCEPT_EULA=yes" --name vernemq -d vernemq/vernemq
```
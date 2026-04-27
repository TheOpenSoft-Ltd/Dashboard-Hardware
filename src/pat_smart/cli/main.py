import os
import subprocess
from importlib.metadata import version
from pathlib import Path

import rich_click as click
from pydantic import ValidationError

from pat_smart.app import MainScreen
from pat_smart.config import Settings
from pat_smart.modules.sandbox.runner import SandboxRunner

# Cofiguration of Textual Framwork
click.rich_click.USE_RICH_MARKUP = True
click.rich_click.COLOR_SYSTEM = "truecolor"

click.rich_click.STYLE_OPTIONS_TABLE_LEADING = 1
click.rich_click.STYLE_OPTIONS_TABLE_BOX = "SIMPLE"

click.rich_click.SHOW_METAVARS_COLUMN = True
click.rich_click.ERRORS_SUGGESTION = "Try 'pat-smart --help' to view available options."


def _version_option() -> str:
    pat_smart_version = version("pat-smart")
    return f"Pattaya Smart, version {pat_smart_version}"


def _load_config():
    try:
        return Settings()  # type: ignore
    except ValidationError as e:
        return None


@click.group(
    invoke_without_command=True,
    help="PAT Smart – MQTT Monitor, Sandbox & Forwarder",
)
@click.version_option(
    package_name="pat-smart",
    message=_version_option(),
)
@click.pass_context
def cli(ctx: click.Context):
    """
    Run the CLI application.
    """
    if ctx.invoked_subcommand is None:
        config = _load_config()
        if config is None:
            click.echo(
                "Error: Configuration error. Run 'pat-smart init' to create a default .env file."
            )
            return
        app = MainScreen()
        app.run()


@cli.command()
def init():
    """Initialize configuration in ~/.config/pat-smart/.env"""
    config_dir = Path.home() / ".config" / "pat-smart"
    config_dir.mkdir(parents=True, exist_ok=True)
    env_file = config_dir / ".env"

    if env_file.exists():
        click.confirm(
            f"File already exists at {env_file}. Overwrite?", default=False, abort=True
        )

    click.echo("Enter configuration values (press Enter to accept defaults)")
    click.echo("-" * 40)

    mqtt_host = click.prompt("MQTT Host", default="localhost")
    mqtt_port = click.prompt("MQTT Port", default=1883, type=int)

    default_cert = str(Path.home() / ".config" / "pat-smart" / "certificate.pem")
    default_key = str(Path.home() / ".config" / "pat-smart" / "private.key")
    default_ca = str(Path.home() / ".config" / "pat-smart" / "ca.pem")

    cert_checks = [
        ("certificate.pem", default_cert),
        ("private.key", default_key),
        ("ca.pem", default_ca),
    ]

    errors = []
    for name, path in cert_checks:
        if Path(path).expanduser().exists():
            click.echo(f"✅ {name}: {path}")
        else:
            errors.append(f"  ❌ {name}: {path} - Not found")

    if errors:
        click.echo("\nCertificate check failed:")
        for e in errors:
            click.echo(e)
        raise SystemExit(1)

    click.echo("\nAll certificates found successfully!")

    mqtt_cert = default_cert
    mqtt_private_key = default_key
    mqtt_ca = default_ca

    click.echo("-" * 40)

    device_id = click.prompt("Device ID", default="PAT-Txxxxxx")
    station_id = click.prompt("Station ID", default="2e45-xxxxx-xxxx")
    station_name = click.prompt("Station Name", default="PS12")
    mode = click.prompt("Mode (DROPLER/RADAR/FULL)", default="FULL")

    click.echo("-" * 40)

    heartbeat_interval = click.prompt(
        "Heartbeat Interval (seconds)", default=10, type=int
    )
    log_dir = click.prompt(
        "Log Directory",
        default=str(Path.home() / ".local" / "state" / "pat-smart" / "logs"),
    )
    log_file_prefix = click.prompt("Log File Prefix", default="sensor")

    click.echo("-" * 40)

    modbus_host = click.prompt("Modbus Host", default="localhost")
    modbus_port = click.prompt("Modbus Port", default=502, type=int)
    modbus_usbport = click.prompt("Modbus USB Port", default="/dev/ttyUSB0")

    click.echo("-" * 40)

    rtmp_url = click.prompt(
        "RTMP URL",
        default="rtsp://username:@Password@192.168.x.x:554/stream0",
    )
    rtsp_url = click.prompt(
        "RTSP URL", default="rtmp://192.168.x.x/CCTVApp/CCTV-2C9F9E1Fxxxxxx"
    )

    default_config = f"""# MQTT Configuration
MQTT_HOST={mqtt_host}
MQTT_PORT={mqtt_port}
MQTT_CERT={mqtt_cert}
MQTT_PRIVATE_KEY={mqtt_private_key}
MQTT_CA={mqtt_ca}

# Device Configuration
DEVICE_ID={device_id}
STATION_ID={station_id}
STATION_NAME={station_name}
MODE={mode}

# Heartbeat
HEARTBEAT_INTERVAL={heartbeat_interval}

# Logging
LOG_DIR={log_dir}
LOG_FILE_PREFIX={log_file_prefix}

# Modbus
MODBUS_HOST={modbus_host}
MODBUS_PORT={modbus_port}
MODBUS_USBPORT={modbus_usbport}

# Streaming
RTMP_URL={rtmp_url}
RTSP_URL={rtsp_url}
"""
    env_file.write_text(default_config, encoding="utf-8")
    click.echo(f"Configuration file created at {env_file}")
    click.echo("Run 'pat-smart' to start.")


@cli.command()
def sandbox():
    """Run sandbox data sender"""
    config = _load_config()
    if config is None:
        click.echo("Error: Configuration error. Run 'pat-smart init' first.")
        return
    runner = SandboxRunner()
    runner.start()


@cli.command()
def check_connection():
    """Check Netowork/MQTT Connectivity"""
    click.echo("checking connection...")


def _install_service(
    name: str, description: str, exec_start: str, user: str, group: str
):
    service_path = f"/etc/systemd/system/{name}"
    content = f"""[Unit]
Description={description}
After=network.target

[Service]
EnvironmentFile={str(Path.home() / ".local" / "pat-smart" / ".env")}
WorkingDirectory={str(Path.home() / ".config"/ "pat-smart" / "workers")}
ExecStart={exec_start}
Restart=on-failure
RestartSec=10
User={user}
Group={group}

[Install]
WantedBy=multi-user.target
"""
    with open(service_path, "w", encoding="utf-8") as f:
        f.write(content)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", name], check=True)


@cli.command()
def install():
    """Install worker service(s)."""
    mode = os.getenv("MODE", "DROPLER").upper()
    if mode not in ("DROPLER", "RADAR", "FULL"):
        click.echo(f"Unsupported MODE: {mode}. Supported: DROPLER, RADAR, FULL.")
        return

    user = os.getenv("USER") or "root"
    group = os.getenv("LOGNAME") or user
    python_path = str(
        Path.home() / ".local" / "pipx" / "venvs" / "pat-smart" / "bin" / "python3"
    )

    services = []
    if mode in ("DROPLER", "FULL"):
        services.append(
            (
                "pat-smart-dropler.service",
                "Pat-Smart Dropler Service",
                f"{python_path} -m dropler.py",
            )
        )
    if mode in ("RADAR", "FULL"):
        services.append(
            (
                "pat-smart-radar.service",
                "Pat-Smart Radar Service",
                f"{python_path} -m radar.py",
            )
        )

    services.append(
        (
            "pat-smart-stream.service",
            "Pat-Smart Stream Service",
            f"bash stream.sh",
        )
    )

    failed = []
    for name, description, exec_start in services:
        try:
            _install_service(name, description, exec_start, user, group)
            click.echo(f"Service {name} installed and started.")
        except Exception as e:
            failed.append((name, str(e)))
            click.echo(f"Failed to install {name}: {e}")

    if failed:
        click.echo(f"\nFailed services: {[n for n, _ in failed]}")


@cli.command()
def status():
    """Check if worker service is running."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "pat-smart-worker.service"],
            capture_output=True,
            text=True,
            check=False,
        )
        state = result.stdout.strip()
        if state == "active":
            click.echo("Pat-smart worker service is running.")
        else:
            click.echo(f"Pat-smart worker service status: {state}")
    except Exception as e:
        click.echo(f"Could not determine service status: {e}")


def run() -> None:
    cli()

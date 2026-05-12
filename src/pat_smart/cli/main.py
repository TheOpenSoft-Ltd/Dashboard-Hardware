import os
import shutil
import socket
import subprocess
from pathlib import Path

import redis
import rich_click as click
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from pat_smart.app import MainScreen
from pat_smart.config import Settings

#
# Console
#
console = Console()

#
# Rich Click Configuration
#
click.rich_click.USE_RICH_MARKUP = False
click.rich_click.COLOR_SYSTEM = None

click.rich_click.STYLE_OPTIONS_TABLE_LEADING = 1
click.rich_click.STYLE_OPTIONS_TABLE_BOX = "SIMPLE"

click.rich_click.SHOW_METAVARS_COLUMN = True
click.rich_click.ERRORS_SUGGESTION = (
    "Try 'pat-smart --help' to view available options."
)

CONFIG_DIR = Path.home() / ".config" / "pat-smart"
ENV_FILE = CONFIG_DIR / ".env"

SERVICE_MAP = {
    "DROPLER": {
        "pat-smart-dropler.service",
        "pat-smart-stream.service",
    },
    "RADAR": {
        "pat-smart-radar.service",
        "pat-smart-stream.service",
    },
    "FULL": {
        "pat-smart-dropler.service",
        "pat-smart-radar.service",
        "pat-smart-stream.service",
    },
}


def _load_config():
    try:
        return Settings()  # type: ignore
    except ValidationError:
        return None


@click.group(
    invoke_without_command=True,
    help="PAT Smart – MQTT Monitor, Sandbox & Forwarder",
)
@click.pass_context
def cli(ctx: click.Context):
    """
    Run PAT Smart CLI.
    """

    if ctx.invoked_subcommand is None:
        config = _load_config()

        if config is None:
            console.print(
                "❌ Configuration error. Run 'pat-smart init' first."
            )
            raise SystemExit(1)

        app = MainScreen()
        app.run()


#
# INIT
#
@cli.command()
def init():
    """Initialize PAT Smart configuration."""

    CONFIG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if ENV_FILE.exists():

        overwrite = click.confirm(
            "⚠️ Configuration already exists. Overwrite?",
            default=False,
        )

        if not overwrite:
            console.print("ℹ️ Initialization cancelled.")
            return

    console.print("\n📦 PAT SMART INITIALIZATION\n")

    #
    # MQTT
    #
    console.print("📡 MQTT Configuration")

    mqtt_host = click.prompt(
        "MQTT Host",
        default="localhost",
    )

    mqtt_port = click.prompt(
        "MQTT Port",
        default=1883,
        type=int,
    )

    #
    # DEVICE
    #
    console.print("\n🖥️ Device Configuration")

    device_id = click.prompt(
        "Device ID",
        default="PAT-TXXXXXX",
    )

    station_id = click.prompt(
        "Station ID",
        default="station-001",
    )

    station_name = click.prompt(
        "Station Name",
        default="PS12",
    )

    mode = click.prompt(
        "Mode",
        type=click.Choice(
            ["DROPLER", "RADAR", "FULL"],
            case_sensitive=False,
        ),
        default="FULL",
    )

    #
    # LOGGING
    #
    console.print("\n📝 Logging Configuration")

    log_dir = click.prompt(
        "Log Directory",
        default=str(
            Path.home()
            / ".local"
            / "state"
            / "pat-smart"
            / "logs"
        ),
    )

    heartbeat_interval = click.prompt(
        "Heartbeat Interval",
        default=10,
        type=int,
    )

    #
    # MODBUS
    #
    console.print("\n🔌 Modbus Configuration")

    modbus_usbport = click.prompt(
        "Modbus USB Port",
        default="/dev/ttyUSB0",
    )

    modbus_host = click.prompt(
        "Modbus Host",
        default="localhost",
    )

    modbus_port = click.prompt(
        "Modbus Port",
        default=502,
        type=int,
    )

    #
    # CERTIFICATES
    #
    console.print("\n🔐 Certificate Configuration")

    mqtt_cert = click.prompt(
        "MQTT Certificate",
        default=str(
            CONFIG_DIR / "certificate.pem"
        ),
    )

    mqtt_private_key = click.prompt(
        "MQTT Private Key",
        default=str(
            CONFIG_DIR / "private.key"
        ),
    )

    mqtt_ca = click.prompt(
        "MQTT CA",
        default=str(
            CONFIG_DIR / "ca.pem"
        ),
    )

    #
    # REDIS
    #
    console.print("\n🧠 Redis Configuration")

    redis_host = click.prompt(
        "Redis Host",
        default="localhost",
    )

    redis_port = click.prompt(
        "Redis Port",
        default=6379,
        type=int,
    )

    #
    # SAVE
    #
    env_content = f"""# MQTT
MQTT_HOST={mqtt_host}
MQTT_PORT={mqtt_port}
MQTT_CERT={mqtt_cert}
MQTT_PRIVATE_KEY={mqtt_private_key}
MQTT_CA={mqtt_ca}

# DEVICE
DEVICE_ID={device_id}
STATION_ID={station_id}
STATION_NAME={station_name}
MODE={mode.upper()}

# HEARTBEAT
HEARTBEAT_INTERVAL={heartbeat_interval}

# LOGGING
LOG_DIR={log_dir}

# MODBUS
MODBUS_HOST={modbus_host}
MODBUS_PORT={modbus_port}
MODBUS_USBPORT={modbus_usbport}

# REDIS
REDIS_HOST={redis_host}
REDIS_PORT={redis_port}
"""

    ENV_FILE.write_text(
        env_content,
        encoding="utf-8",
    )

    console.print("")
    console.print(f"✅ Configuration saved to:")
    console.print(f"   {ENV_FILE}")

    console.print("")
    console.print("ℹ️ Next step:")
    console.print("   pat-smart install")


#
# INSTALL
#
@cli.command()
def install():
    """Install PAT Smart systemd services."""

    config = _load_config()

    if config is None:
        console.print(
            "❌ Invalid configuration."
        )

        console.print(
            "ℹ️ Run 'pat-smart init' first."
        )

        raise SystemExit(1)

    mode = config.MODE.upper()

    desired_services = SERVICE_MAP.get(mode)

    if desired_services is None:
        console.print(
            f"❌ Unsupported MODE: {mode}"
        )
        raise SystemExit(1)

    console.print(
        f"\n⚙️ Installing PAT Smart services for MODE={mode}\n"
    )

    #
    # Worker directory
    #
    workers_dir = (
        CONFIG_DIR / "workers"
    )

   
    python_path = (
        Path.home()
        / ".local"
        / "share"
        / "pipx"
        / "venvs"
        / "pat-smart"
        / "bin"
        / "python3"
    )

    if not python_path.exists():
        console.print(
            f"❌ Python executable not found: {python_path}"
        )

        console.print(
            "ℹ️ Ensure pat-smart is installed using pipx."
        )

        raise SystemExit(1)

    #
    # Service definitions
    #
    service_definitions = {
        "pat-smart-dropler.service": {
            "description": "PAT Smart Dropler Service",
            "exec": (
                f"{python_path} "
                f"{workers_dir / 'dropler.py'}"
            ),
        },
        "pat-smart-radar.service": {
            "description": "PAT Smart Radar Service",
            "exec": (
                f"{python_path} "
                f"{workers_dir / 'radar.py'}"
            ),
        },
        "pat-smart-stream.service": {
            "description": "PAT Smart Stream Service",
            "exec": (
                f"bash {workers_dir / 'stream.sh'}"
            ),
        },
    }

    #
    # Install selected services
    #
    for service in desired_services:

        service_data = service_definitions[service]

        service_content = f"""[Unit]
Description={service_data['description']}
After=network.target

[Service]
Type=simple
EnvironmentFile={ENV_FILE}
WorkingDirectory={workers_dir}
ExecStart={service_data['exec']}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

        temp_service = (
            Path("/tmp") / service
        )

        temp_service.write_text(
            service_content,
            encoding="utf-8",
        )

        #
        # Move service using sudo
        #
        subprocess.run(
            [
                "sudo",
                "mv",
                str(temp_service),
                f"/etc/systemd/system/{service}",
            ],
            check=True,
        )

        console.print(
            f"✅ Installed {service}"
        )

    #
    # Reload systemd
    #
    subprocess.run(
        [
            "sudo",
            "systemctl",
            "daemon-reload",
        ],
        check=True,
    )

    #
    # Enable services
    #
    for service in desired_services:

        subprocess.run(
            [
                "sudo",
                "systemctl",
                "enable",
                "--now",
                service,
            ],
            check=True,
        )

        console.print(
            f"▶️ Started {service}"
        )

    console.print("")
    console.print(
        "✅ PAT Smart installation complete."
    )

    console.print("")
    console.print("ℹ️ Useful commands:")
    console.print("   pat-smart status")
    console.print("   pat-smart logs")
    console.print("   pat-smart doctor")

#
# MODE
#
@cli.command()
@click.argument(
    "mode",
    type=click.Choice(
        ["DROPLER", "RADAR", "FULL"],
        case_sensitive=False,
    ),
)
def mode(mode: str):
    """Change PAT Smart operating mode."""

    mode = mode.upper()

    if not ENV_FILE.exists():
        console.print("❌ Configuration file not found.")
        console.print("ℹ️ Run 'pat-smart init' first.")
        raise SystemExit(1)

    content = ENV_FILE.read_text(encoding="utf-8")

    lines = []
    found = False

    for line in content.splitlines():

        if line.startswith("MODE="):
            lines.append(f"MODE={mode}")
            found = True
        else:
            lines.append(line)

    if not found:
        lines.append(f"MODE={mode}")

    ENV_FILE.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    console.print(f"✅ MODE changed to {mode}")
    console.print("ℹ️ Run 'pat-smart reload' to apply changes.")


#
# RELOAD
#
@cli.command()
def reload():
    """Reload PAT Smart services."""

    config = _load_config()

    if config is None:
        console.print("❌ Configuration validation failed.")
        raise SystemExit(1)

    mode = config.MODE.upper()

    desired_services = SERVICE_MAP.get(mode)

    if desired_services is None:
        console.print(f"❌ Unsupported MODE: {mode}")
        raise SystemExit(1)

    all_services = set().union(*SERVICE_MAP.values())

    console.print(f"🔄 Reloading MODE={mode}")

    subprocess.run(
        [
            "sudo",
            "systemctl",
            "daemon-reload",
        ],
        check=True,
    )

    #
    # Stop unwanted services
    #
    unused_services = all_services - desired_services

    if unused_services:
        console.print("\n⏹️ Stopping unused services")

    for service in unused_services:

        subprocess.run(
            [
                "sudo",
                "systemctl",
                "stop",
                service,
            ],
            check=False,
        )

        console.print(f"   • {service}")

    #
    # Restart required services
    #
    console.print("\n▶️ Restarting required services")

    for service in desired_services:

        result = subprocess.run(
            [
                "sudo",
                "systemctl",
                "restart",
                service,
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            console.print(f"   ✅ {service}")
        else:
            console.print(f"   ❌ {service}")

            if result.stderr:
                console.print(f"      {result.stderr.strip()}")

    console.print("\n✅ Reload complete.")


#
# STATUS
#
@cli.command()
def status():
    """Show PAT Smart service status."""

    config = _load_config()

    if config is None:
        console.print("❌ Invalid configuration.")
        raise SystemExit(1)

    mode = config.MODE.upper()

    services = SERVICE_MAP.get(mode, set())

    console.print("\n📊 PAT SMART STATUS\n")

    table = Table(
        show_header=True,
        expand=True,
    )

    table.add_column("Service")
    table.add_column("Active")
    table.add_column("Enabled")

    for service in services:

        active_result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
        )

        enabled_result = subprocess.run(
            ["systemctl", "is-enabled", service],
            capture_output=True,
            text=True,
        )

        active = active_result.stdout.strip()
        enabled = enabled_result.stdout.strip()

        if active == "active":
            active_text = "✅ active"
        else:
            active_text = f"❌ {active}"

        if enabled == "enabled":
            enabled_text = "✅ enabled"
        else:
            enabled_text = f"❌ {enabled}"

        table.add_row(
            service,
            active_text,
            enabled_text,
        )

    console.print(table)

    console.print(f"\n📁 Config: {ENV_FILE}")


#
# LOGS
#
@cli.command()
@click.argument(
    "service",
    required=False,
    type=click.Choice(
        ["dropler", "radar", "stream"],
        case_sensitive=False,
    ),
)
@click.option(
    "-f",
    "--follow",
    is_flag=True,
    help="Follow logs.",
)
@click.option(
    "-n",
    "--lines",
    default=50,
    help="Number of log lines.",
)
def logs(service: str | None, follow: bool, lines: int):
    """Show service logs."""

    service_map = {
        "dropler": "pat-smart-dropler.service",
        "radar": "pat-smart-radar.service",
        "stream": "pat-smart-stream.service",
    }

    cmd = ["journalctl"]

    if follow:
        cmd.append("-f")

    cmd.extend(
        [
            "--output",
            "short-iso",
            "-n",
            str(lines),
        ]
    )

    if service:
        cmd.extend(["-u", service_map[service]])
    else:
        for svc in service_map.values():
            cmd.extend(["-u", svc])

    subprocess.run(cmd)


#
# ENV
#
@cli.command(name="env")
def env_command():
    """Show current environment configuration."""

    if not ENV_FILE.exists():
        console.print("❌ Missing configuration file.")
        raise SystemExit(1)

    table = Table(
        show_header=True,
        expand=True,
    )

    table.add_column("Key")
    table.add_column("Value")

    content = ENV_FILE.read_text(encoding="utf-8")

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        table.add_row(key, value)
    console.print(table)

@cli.command()
def doctor():
    """Run PAT Smart diagnostics."""

    console.print("\n🩺 PAT SMART DOCTOR\n")

    table = Table(
        show_header=True,
        expand=True,
    )

    table.add_column("Component")
    table.add_column("Status")
    table.add_column("Message")

    error_count = 0

    #
    # CONFIG
    #
    if ENV_FILE.exists():
        table.add_row(
            "Config",
            "✅ OK",
            str(ENV_FILE),
        )
    else:
        table.add_row(
            "Config",
            "❌ FAIL",
            f"Missing {ENV_FILE}",
        )
        error_count += 1

    config = _load_config()

    if config is None:
        table.add_row(
            "Settings",
            "❌ FAIL",
            "Invalid configuration",
        )

        console.print(table)
        console.print("\n❌ Doctor failed with 1 error(s).")

        raise SystemExit(1)

    mode = config.MODE.upper()

    table.add_row(
        "Mode",
        "✅ OK",
        mode,
    )

    #
    # MQTT
    #
    try:
        sock = socket.create_connection(
            (
                config.MQTT_HOST,
                config.MQTT_PORT,
            ),
            timeout=3,
        )

        sock.close()

        table.add_row(
            "MQTT",
            "✅ OK",
            f"{config.MQTT_HOST}:{config.MQTT_PORT}",
        )

    except Exception as e:
        table.add_row(
            "MQTT",
            "❌ FAIL",
            str(e),
        )
        error_count += 1

    #
    # REDIS
    #
    try:
        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
        )

        r.ping()

        table.add_row(
            "Redis",
            "✅ OK",
            "Connection successful",
        )

    except Exception as e:
        table.add_row(
            "Redis",
            "❌ FAIL",
            str(e),
        )
        error_count += 1

    #
    # MODBUS
    #
    modbus_port = Path(config.MODBUS_USBPORT)

    if modbus_port.exists():

        if os.access(modbus_port, os.R_OK | os.W_OK):
            table.add_row(
                "Modbus",
                "✅ OK",
                str(modbus_port),
            )
        else:
            table.add_row(
                "Modbus",
                "❌ FAIL",
                "Permission denied",
            )
            error_count += 1

    else:
        table.add_row(
            "Modbus",
            "❌ FAIL",
            f"Missing {modbus_port}",
        )
        error_count += 1

    #
    # SERVICES
    #
    desired_services = SERVICE_MAP.get(mode, set())

    for service in desired_services:

        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
        )

        state = result.stdout.strip()

        if state == "active":
            table.add_row(
                "Service",
                "✅ OK",
                f"{service} active",
            )
        else:
            table.add_row(
                "Service",
                "❌ FAIL",
                f"{service} ({state})",
            )
            error_count += 1

    #
    # FILESYSTEM
    #
    log_dir = Path(config.LOG_DIR)

    try:
        log_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        test_file = log_dir / ".test"

        test_file.write_text("ok")
        test_file.unlink()

        table.add_row(
            "Filesystem",
            "✅ OK",
            str(log_dir),
        )

    except Exception as e:
        table.add_row(
            "Filesystem",
            "❌ FAIL",
            str(e),
        )
        error_count += 1

    #
    # CERTIFICATES
    #
    certs = [
        ("MQTT_CERT", config.MQTT_CERT),
        ("MQTT_PRIVATE_KEY", config.MQTT_PRIVATE_KEY),
        ("MQTT_CA", config.MQTT_CA),
    ]

    for cert_name, cert_path in certs:

        path = Path(cert_path)

        if path.exists():
            table.add_row(
                cert_name,
                "✅ OK",
                str(path),
            )
        else:
            table.add_row(
                cert_name,
                "❌ FAIL",
                f"Missing {path}",
            )
            error_count += 1

    #
    # RESULT
    #
    console.print(table)

    console.print("")

    if error_count > 0:
        console.print(
            f"❌ Doctor found {error_count} error(s)."
        )
        raise SystemExit(1)

    console.print("✅ Doctor completed successfully.")

#
# UNINSTALL
#
@cli.command()
@click.option(
    "--purge",
    is_flag=True,
    help="Remove config and logs.",
)
def uninstall(purge: bool):
    """Uninstall PAT Smart services."""

    all_services = set().union(*SERVICE_MAP.values())

    console.print("⏹️ Stopping services")

    for service in all_services:

        subprocess.run(
            ["sudo", "systemctl", "stop", service],
            check=False,
        )

    console.print("\n🗑️ Removing services")

    for service in all_services:

        subprocess.run(
            ["sudo", "systemctl", "disable", service],
            check=False,
        )

        subprocess.run(
            [
                "sudo",
                "rm",
                "-f",
                f"/etc/systemd/system/{service}",
            ],
            check=False,
        )

        console.print(f"   ✅ {service}")

    subprocess.run(
        ["sudo", "systemctl", "daemon-reload"],
        check=False,
    )

    if purge:

        console.print("\n🧹 Removing data")

        paths = [
            CONFIG_DIR,
            Path.home()
            / ".local"
            / "state"
            / "pat-smart",
        ]

        for path in paths:

            if path.exists():
                shutil.rmtree(path)

                console.print(f"   ✅ {path}")

    console.print("\n✅ PAT Smart uninstall complete.")
    console.print("ℹ️ To remove CLI package:")
    console.print("   pipx uninstall pat-smart")


def run():
    cli()
from importlib.metadata import version

import rich_click as click

from pat_smart.modules.sandbox.runner import SandboxRunner
from pat_smart.services.mqtt.client import MQTTClient
from pat_smart.utils.generator import generate_random_sha

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
        print("Start app")
        mqtt = MQTTClient("localhost", "test1")
        mqtt.connect()


@cli.command()
def sandbox():
    """Run sandbox data sender"""
    client_id = f"PAT-ST1-{generate_random_sha()}"
    station_id = "701673d6-b663-45a1-ab84-79d7743eb659"
    runner = SandboxRunner(
        "localhost", client_id, "PAT-E333EE", "พัทยา 6/1", station_id
    )
    runner.start()


@cli.command()
def check_connection():
    """Check Netowork/MQTT Connectivity"""
    click.echo("checking connection...")


def run() -> None:
    cli()

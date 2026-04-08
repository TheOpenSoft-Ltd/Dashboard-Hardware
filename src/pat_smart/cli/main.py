from importlib.metadata import version

import rich_click as click
from pydantic import ValidationError

from pat_smart.app import MainScreen
from pat_smart.config import Settings
from pat_smart.modules.sandbox.runner import SandboxRunner

# Load evironmen variable
try:
    config = Settings()  # type: ignore
except ValidationError as e:
    print("Evironment configuration error: ")
    print(e)
    exit(1)


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
        app = MainScreen()
        app.run()


@cli.command()
def sandbox():
    """Run sandbox data sender"""
    runner = SandboxRunner()
    runner.start()


@cli.command()
def check_connection():
    """Check Netowork/MQTT Connectivity"""
    click.echo("checking connection...")


def run() -> None:
    cli()

"""
Functions for starting KIMMDY either from python or the command line.
Other entry points such as `kimmdy-analysis` also live here.
"""

import argparse
import logging
import logging.config
import os
import shutil
import sys
import textwrap
from os import chmod
from pathlib import Path
from typing import Optional

from kimmdy.assets.templates import jobscript
from kimmdy.config import Config
from kimmdy.plugins import (
    broken_parameterization_plugins,
    broken_reaction_plugins,
    discover_plugins,
    parameterization_plugins,
    reaction_plugins,
)
from kimmdy.runmanager import RunManager
from kimmdy.utils import longFormatter

if sys.version_info > (3, 10):
    from importlib_metadata import version
else:
    from importlib.metadata import version

logger = logging.getLogger(__name__)


def configure_logger(config: Config):
    """Configure logging.

    Parameters
    ----------
    config
        configuration that contains
        log.level and log.file
    """
    log_conf = {
        "version": 1,
        "formatters": {
            "short": {
                "format": "%(name)-15s %(levelname)s: %(message)s",
                "datefmt": "%H:%M",
            },
            "full": {
                "format": "%(asctime)s %(name)-17s %(levelname)s: %(message)s",
                "datefmt": "%d-%m-%y %H:%M:%S",
            },
            "full_cut": {
                "()": longFormatter,
                "format": "%(asctime)s %(name)-12s %(levelname)s: %(message)s",
                "datefmt": "%d-%m-%y %H:%M:%S",
            },
        },
        "handlers": {
            "cmd": {
                "class": "logging.StreamHandler",
                "formatter": "short",
            },
            "file": {
                "class": "logging.FileHandler",
                "formatter": "full_cut",
                "filename": config.log.file,
            },
            "null": {
                "class": "logging.NullHandler",
            },
        },
        "loggers": {
            "kimmdy": {
                "level": config.log.level.upper(),
                "handlers": ["cmd", "file"],
            },
        },
        # Mute others, e.g. tensorflow, matplotlib
        "root": {
            "level": "CRITICAL",
            "handlers": ["null"],
        },
    }
    logging.config.dictConfig(log_conf)

    # symlink logfile of the latest run to kimmdy.log in cwd
    log: Path = config.cwd.joinpath("kimmdy.log")
    if log.is_symlink():
        log.unlink()
    if log.exists():
        os.remove(log)

    log.symlink_to(config.out / config.log.file)


def get_cmdline_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns
    -------
        Parsed command line arguments
    """
    parser = argparse.ArgumentParser(
        description="Welcome to KIMMDY. `kimmdy` runs KIMMDY, further tools "
        "are available as `kimmdy-...` commands. These are `-analysis`, "
        "`-modify-top` and `-build-examples`. Access their help with "
        "`kimmdy-... -h.`"
    )
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        help="Kimmdy input file. Default `kimmdy.yml`",
        default="kimmdy.yml",
    )
    parser.add_argument(
        "--loglevel",
        "-l",
        type=str,
        help="logging level (CRITICAL, ERROR, WARNING, INFO, DEBUG)",
        default=None,
    )
    parser.add_argument("--logfile", "-f", type=Path, help="logfile", default=None)

    # flag to show available plugins
    parser.add_argument(
        "--show-plugins", action="store_true", help=("List available plugins")
    )

    # flag to print an example jobscript for slurm hpc clusters
    parser.add_argument(
        "--generate-jobscript",
        action="store_true",
        help="Instead of running KIMMDY directly, generate at jobscript.sh for"
        " slurm HPC clusters."
        "You can then run this jobscript with sbatch jobscript.sh",
    )

    parser.add_argument(
        "--version", action="version", version=f'KIMMDY {version("kimmdy")}'
    )

    # on error, drop into debugger
    parser.add_argument(
        "--debug", action="store_true", help=("on error, drop into debugger")
    )

    # visualize call stack
    parser.add_argument(
        "--callgraph",
        action="store_true",
        help="Generate visualization of function calls. Mostly useful for "
        "debugging and documentation.",
    )

    return parser.parse_args()


def _run(args: argparse.Namespace):
    """Run kimmdy.

    Parameters
    ----------
    args
        Command line arguments. See [](`~kimmdy.cmd.get_cmdline_args`)
    """

    if args.show_plugins:
        discover_plugins()

        print("\nAvailable reaction plugins:")
        for plugin in reaction_plugins:
            print(plugin)

        print("\nFound but not loadable reaction plugins:")
        for plugin in broken_reaction_plugins:
            print(plugin)
            print(f"\tException: {broken_reaction_plugins[plugin]}")

        print("\nAvailable parameterization plugins:")
        for plugin in parameterization_plugins:
            print(plugin)

        print("\nFound but not loadable parameterization plugins:")
        for plugin in broken_parameterization_plugins:
            print(plugin)
            print(f"\tException: {broken_parameterization_plugins[plugin]}")

        exit()

    if not Path(args.input).exists():
        raise FileNotFoundError(
            f"Input file {args.input} does not exist. Specify its name with "
            "--input and make sure that you are in the right directory."
        )

    try:

        discover_plugins()
        config = Config(
            input_file=args.input, logfile=args.logfile, loglevel=args.loglevel
        )

        configure_logger(config)
        logger.info("Welcome to KIMMDY")

        # write out collected log messages
        # from initial config parsing
        # before the logger was configured
        # (because the logger config depends on the config)
        for info in config._logmessages["debug"]:
            logger.debug(info)
        for info in config._logmessages["infos"]:
            logger.info(info)
        for warning in config._logmessages["warnings"]:
            logger.warning(warning)

        runmgr = RunManager(config)

        if args.generate_jobscript:
            content = jobscript.format(config=config).strip("\n")
            path = "jobscript.sh"

            with open(path, "w") as f:
                f.write(textwrap.dedent(content))

            chmod(path, 0o755)
        else:
            if args.callgraph:
                try:
                    from pycallgraph2 import PyCallGraph
                    from pycallgraph2.config import Config as Vis_conf
                    from pycallgraph2.globbing_filter import GlobbingFilter
                    from pycallgraph2.output import GraphvizOutput
                except ImportError as e:
                    logger.error(
                        "pycallgraph2 needed for call visualization. Get it with `pip install pycallgraph2`"
                    )
                    exit()

                out = GraphvizOutput()
                out.output_type = "svg"
                trace_filter = GlobbingFilter(
                    exclude=["pycallgraph.*"],
                    include=["kimmdy.*"],
                )
                vis_conf = Vis_conf(trace_filter=trace_filter)
                with PyCallGraph(output=out, config=vis_conf):
                    runmgr.run()

            else:
                runmgr.run()

    except Exception as e:
        if args.debug:
            print(e)
            import pdb

            pdb.post_mortem()
        else:
            raise e
    finally:
        logging.shutdown()
        if args.generate_jobscript:
            shutil.rmtree(config.out)


def kimmdy_run(
    input: Path = Path("kimmdy.yml"),
    loglevel: Optional[str] = None,
    logfile: Optional[Path] = None,
    show_plugins: bool = False,
    generate_jobscript: bool = False,
    debug: bool = False,
    callgraph: bool = False,
):
    """Run KIMMDY from python.

    Also see See [](`~kimmdy.cmd.get_cmdline_args`) or `kimmdy --help` for the descriptions of the arguments.

    Parameters
    ----------
    input
        kimmdy input yml file.
    loglevel
        Loglevel. One of ["INFO", "WARNING", "MESSAGE", "DEBUG"]
    logfile
        File path of the logfile.
    show_plugins
        Show available plugins and exit.
    generate_jobscript
        Instead of running KIMMDY directly, generate at jobscript.sh for slurm HPC clusters
    """
    args = argparse.Namespace(
        input=input,
        loglevel=loglevel,
        logfile=logfile,
        show_plugins=show_plugins,
        generate_jobscript=generate_jobscript,
        debug=debug,
        callgraph=callgraph,
    )
    _run(args)


def entry_point_kimmdy():
    """Run KIMMDY from the command line.

    The configuration is gathered from the input file,
    which is `kimmdy.yml` by default.
    See [](`~kimmdy.cmd.get_cmdline_args`) or `kimmdy --help`
    for the descriptions of the arguments.
    """
    args = get_cmdline_args()
    _run(args)


if __name__ == "__main__":
    entry_point_kimmdy()

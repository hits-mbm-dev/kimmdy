"""
Read and validate kimmdy.yml configuration files
and package into a parsed format for internal use.
"""
from __future__ import annotations
from typing import Optional
import yaml
import logging
from pathlib import Path
from dataclasses import dataclass
from kimmdy import plugins
from kimmdy.reaction import ReactionPlugin


def check_file_exists(p: Path):
    if not p.exists():
        m = "File not found: " + str(p.resolve())
        logging.error(m)
        raise LookupError(m)


class Sequence(list):
    """A sequence of tasks."""

    def __init__(self, tasks: list):
        list.__init__(self)
        for task in tasks:
            if isinstance(task, dict):
                for _ in range(task["mult"]):
                    assert isinstance(
                        task["tasks"], list
                    ), "Grouped tasks must be a list!"
                    self.extend(task["tasks"])
            else:
                self.append(task)


class Mds:
    def __init__(self):
        pass


type_scheme = {
    "experiment": str,
    "run": int,
    "dryrun": bool,
    "iterations": int,
    "out": Path,
    "gromacs_alias": str,
    "ff": Path,
    "ffpatch": None,
    "top": Path,
    "gro": Path,
    "ndx": Path,
    "mds": {
        "*": {
            "mdp": Path,
            "plumed": {"dat": Path, "distances": Path},
            "prefix": str,
            "overwrite": str,
        }
    },
    "changer": {"coordinates": {"md": str, "md_parameter_growth": str}},
    "reactions": {},
    "sequence": Sequence,
}

# classes for static code analysis


class MDrefConfig:
    md: str
    md_parameter_growth: str


class ChangerConfig:
    coordinates: MDrefConfig


class HomolysisConfig:
    dat: Path
    itp: Path


class ReactionsConfig:
    homolysis: HomolysisConfig


class PullConfig:
    mdp: Path


class SequenceConfig(list):
    tasks: list


class Config:
    """Internal representation of the configuration generated
    from the input file, which enables validation before running
    and computationally expensive operations.
    All settings read from the input file are accessible through nested attributes.

    TODO: think about how much the type annotations on here can lie.

    Attributes
    run: int
    experiment: str
    name: str  # TODO: obsolete??
    dryrun: bool
    iterations: int
    out: Path
    gromacs_alias: str
    ff: Path
    ffpatch: Optional[Path]
    top: Path
    gro: Path
    ndx: Path
    mds: dict
    changer: ChangerConfig
    reactions: ReactionsConfig
    pull: PullConfig
    sequence: SequenceConfig

    Parameters
    ----------
    input_file : Path
        Path to the config yaml file.
    recursive_dict : dict
        For internal use only, used in reading settings in recursively.
    type_scheme : dict
        dict containing types for casting and validating settings.

    """

    run: int
    experiment: str
    name: str  # TODO: obsolete??
    dryrun: bool
    iterations: int
    out: Path
    gromacs_alias: str
    ff: Path
    ffpatch: Optional[Path]
    top: Path
    gro: Path
    ndx: Path
    mds: dict
    changer: ChangerConfig
    reactions: ReactionsConfig
    pull: PullConfig
    sequence: SequenceConfig

    def __init__(
        self,
        input_file: Path | None = None,
        recursive_dict: dict | None = None,
        type_scheme=type_scheme,
    ):
        if input_file is None and recursive_dict is None:
            m = "Error: No input file was provided!"
            logging.error(m)
            raise ValueError(m)

        if input_file is not None and not isinstance(input_file, Path):
            logging.debug(
                "Config input file was not type pathlib.Path, attempting conversion.."
            )
            input_file = Path(input_file)

        self.type_scheme = type_scheme
        if self.type_scheme is None:
            self.type_scheme = {}

        # top level before initialization here
        if input_file is not None:
            with open(input_file, "r") as f:
                raw = yaml.safe_load(f)
            self.raw = raw
            if self.raw is None:
                m = "Error: Could not read input file"
                logging.error(m)
                raise ValueError(m)
            recursive_dict = raw

            if len(plugins) > 0:
                logging.info("Loading Plugins")
                for plg_name, plugin in plugins.items():
                    logging.debug(f"Loading {plg_name}")
                    if type(plugin) is ModuleNotFoundError:
                        # TODO; should this be more than a warning?
                        # Should we crash here?
                        logging.warn(
                            f"Plugin {plg_name} could not be loaded!\n{plugin}\n"
                        )
                    else:
                        try:
                            if (
                                plugin.__bases__[0] is ReactionPlugin
                            ):  # isinstance didnt work for some reason
                                self.type_scheme["reactions"].update(plugin.type_scheme)
                                logging.debug(self.type_scheme["reactions"])
                        except:
                            logging.warn(
                                f"Plugin {plg_name} could not be loaded!\n{plugin}\n"
                            )
                            pass

        # building config recursively
        if recursive_dict is not None:
            for name, val in recursive_dict.items():
                if isinstance(val, dict):
                    recursiv_type_s = self.type_scheme.get(name)
                    if recursiv_type_s is None:
                        recursiv_type_s = self.type_scheme.get("*")
                    val = Config(recursive_dict=val, type_scheme=recursiv_type_s)
                logging.debug(f"Set attribute: {name}, {val}")
                self.__setattr__(name, val)

        # top level after initialization here
        if input_file is not None:
            if cwd := self.raw.get("cwd"):
                cwd = Path(cwd)
            else:
                cwd = input_file.parent.resolve()
            self.cwd = cwd
            if out := self.raw.get("out"):
                out = Path(out)
            else:
                out = self.cwd / self.name
            self.out = out
            # make sure self.out is empty
            while self.out.exists():
                logging.debug(f"Output dir {self.out} exists, incrementing name")
                out_end = self.out.name[-3:]
                if out_end.isdigit():
                    self.out = self.out.with_name(
                        f"{self.out.name[:-3]}{int(out_end)+1:03}"
                    )
                else:
                    self.out = self.out.with_name(self.out.name + "_001")
            self.out.mkdir()
            logging.info(f"Created output dir {self.out}")

            if not hasattr(self, "ff"):
                ffs = list(self.cwd.glob("*.ff"))
                if len(ffs) < 1:
                    # TODO: it can work with a ff in the gromacs data dir
                    # need to re-add the `ff` option but change a bit
                    # to unify with read_top
                    raise FileNotFoundError(
                        "No forcefield found in cwd, please provide one!"
                    )
                if len(ffs) > 1:
                    logging.warn(
                        f"Found {len(ffs)} forcefields in cwd, using first one: {ffs[0]}"
                    )
                assert ffs[0].is_dir(), "Forcefield should be a directory!"
                self.ff = ffs[0].resolve()

            # TODO: why is this commented out?
            # assert hasattr(self,'mds'), "MD section not defined in config file!"
            # for attribute in self.mds.get_attributes():
            #     self.mds.attr(attribute).mdp = Path(self.mds.attr(attribute).mdp)

            self._cast_types()
            self._validate()

    def get_attributes(self):
        """Get a list of all attributes"""
        repr = self.__dict__.copy()
        repr.pop("type_scheme")
        return list(repr.keys())

    def __repr__(self):
        repr = self.__dict__.get("type_scheme")
        return str(repr)

    def attr(self, attribute):
        """Get the value of a specific attribute.
        Alias for self.__getattribute__
        """
        return self.__getattribute__(attribute)

    def _cast_types(self, to_type_wildcard=None):
        """Cast raw attributes to types defined in `type_scheme`"""
        attr_names = filter(lambda s: s[0] != "_", self.__dir__())
        for attr_name in attr_names:
            to_type = self.type_scheme.get(attr_name)
            attr = self.__getattribute__(attr_name)

            if attr_name == "type_scheme" or callable(attr):
                continue

            # handle wildcard attr
            if to_type_wildcard is not None:
                to_type = to_type_wildcard
            if to_type is not None:
                if attr is None:
                    raise ValueError(
                        f"ERROR in inputfile: Missing settings for {attr_name}"
                    )

                # nested:
                if isinstance(to_type, dict):
                    if list(to_type.keys()) == ["*"]:
                        attr._cast_types(to_type["*"])
                    else:
                        attr._cast_types()
                    continue
                # wrap single element if it should be a list
                elif to_type is list:
                    self.__setattr__(attr_name, [attr])
                try:
                    self.__setattr__(attr_name, to_type(attr))
                except ValueError as e:
                    m = (
                        f"Error: Attribute {attr_name} with value {attr} "
                        + f"and type {type(attr)} cound not be converted to {to_type}!"
                    )
                    logging.error(m)
                    raise ValueError(e)

            else:
                logging.debug(
                    f"{to_type} conversion found for attribute {attr_name} and not executed."
                )

    def _validate(self):
        """Validates attributes read from config file."""
        try:
            attr_names = filter(lambda s: s[0] != "_", self.__dir__())
            for attr_name in attr_names:
                logging.debug(f"validating: {attr_name}")
                attr = self.attr(attr_name)
                if isinstance(attr, Config):
                    attr._validate()

                # Check files from scheme
                if isinstance(attr, Path):
                    self.__setattr__(attr_name, attr.resolve())
                    # distances.dat wouldn't exist prior to the run
                    if not str(attr) in ["distances.dat"]:
                        logging.debug(attr)
                        check_file_exists(attr)

                # Validate sequence
                if isinstance(attr, Sequence):
                    for task in attr:
                        if task == "breakpoint":
                            continue
                        elif not hasattr(self, task):
                            if hasattr(self, "mds"):
                                if hasattr(self.mds, task):
                                    continue
                            raise AssertionError(
                                f"Task {task} listed in sequence, but not defined!"
                            )

                # Validate changer reference
                if attr_name == "raw":
                    if hasattr(self, "changer"):
                        if hasattr(self.changer, "coordinates"):
                            if "md" in self.changer.coordinates.get_attributes():
                                assert (
                                    self.changer.coordinates.md
                                    in self.mds.get_attributes()
                                ), f"Relax MD {self.changer.coordinates.md} not in MD section!"

                # Validate reaction plugins
                if attr_name == "reactions":
                    for r in attr.get_attributes():
                        assert r in (ks := list(plugins.keys())), (
                            f"Error: Reaction plugin {r} not found!\n"
                            + f"Available plugins: {ks}"
                        )

        except AssertionError as e:
            logging.error(f"Validating input failed!\n{e}")
            raise AssertionError(e)

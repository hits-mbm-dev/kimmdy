import yaml
import logging
from pathlib import Path
from dataclasses import dataclass
from pprint import pprint


def check_file_exists(p: Path):
    if not p.exists():
        m = "File not found: " + str(p)
        logging.error(m)
        raise LookupError(m)


type_scheme = {
    "dryrun": bool,
    "name": str,
    "iterations": int,
    "out": Path,
    "ff": Path,
    "top": Path,
    "gro": Path,
    "idx": Path,
    "plumed": {"dat": Path},
    "minimization": {"mdp": Path, "tpr": Path},
    "equilibration": {
        "nvt": {"mdp": Path, "tpr": Path},
        "npt": {"mdp": Path, "tpr": Path},
    },
    "equilibrium": {"mdp": Path},
    "prod": {"mdp": Path},
    "changer": {"coordinates": {"md": {"mdp": Path}}},
    "reactions": {"homolysis": {"edis": Path, "bonds": Path}},
}


class Config:
    """
    Internal representation of the configuration generated
    from the input file, which enables validation before running
    and computationally expensive operations.
    All settings read from the input file are accessible through nested attributes.

    Parameters
    ----------
    input_file : Path
        Path to the config yaml file.
    recursive_dict : dict
        For internal use only, used in reading settings in recursively.
    type_scheme : dict
        dict containing types for casting and validating settings.

    """

    cwd: Path
    out: Path

    def __init__(
        self, input_file: Path = None, recursive_dict=None, type_scheme=type_scheme
    ):
        if input_file is None and recursive_dict is None:
            m = "Error: No input file was provided!"
            logging.error(m)
            raise ValueError(m)

        if input_file is not None and not isinstance(input_file, Path):
            logging.warn(
                "Warning: Config input file was not type pathlib.Path, attemptin conversion.."
            )
            Path(input_file)

        self.type_scheme = type_scheme
        if self.type_scheme is None:
            self.type_scheme = {}

        if input_file is not None:
            with open(input_file, "r") as f:
                raw = yaml.safe_load(f)
                self.raw = raw
                if self.raw is None:
                    m = "Error: Could not read input file"
                    logging.error(m)
                    raise ValueError(m)
                recursive_dict = raw

        if recursive_dict is not None:
            for name, val in recursive_dict.items():
                if isinstance(val, dict):
                    val = Config(
                        recursive_dict=val, type_scheme=self.type_scheme.get(name)
                    )
                logging.debug(f"Set attribute: {name}, {val}")
                self.__setattr__(name, val)

        if input_file is not None:
            Config.cwd = (
                Path(cwd) if (cwd := raw.get("cwd")) else input_file.parent.resolve()
            )
            Config.out = Path(out) if (out := raw.get("out")) else self.cwd / self.name
            # make sure Config.out is empty
            while Config.out.exists():
                logging.info(f"Output dir {Config.out} exists, incrementing name")
                out_end = Config.out.name[-3:]
                if out_end.isdigit():
                    Config.out = Config.out.with_name(
                        f"{Config.out.name[:-3]}{int(out_end)+1:03}"
                    )
                else:
                    Config.out = Config.out.with_name(Config.out.name + "_001")
            Config.out.mkdir()
            logging.info(f"Created output dir {Config.out}")

            if not hasattr(self, "ff"):
                ffs = list(self.cwd.glob("*.ff"))
                assert len(ffs) == 1, "Wrong count of forcefields"
                assert ffs[0].is_dir(), "Forcefield should be a directory!"
                self.ff = ffs[0].resolve()

            self._cast_types()
            self._validate()

    def __repr__(self):
        repr = self.__dict__.copy()
        repr.pop("type_scheme")
        return str(repr)

    def attr(self, attribute):
        """Alias for self.__getattribute__"""
        return self.__getattribute__(attribute)

    def _cast_types(self):
        """Casts types defined in `type_scheme` to raw attributes."""
        attr_names = filter(lambda s: s[0] != "_", self.__dir__())
        for attr_name in attr_names:
            to_type = self.type_scheme.get(attr_name)
            attr = self.__getattribute__(attr_name)

            if to_type is not None:
                if attr is None:
                    raise ValueError(
                        f"ERROR in inputfile: Missing settings for {attr_name}"
                    )

                # nested:
                if isinstance(to_type, dict):
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
        attr_names = filter(lambda s: s[0] != "_", self.__dir__())
        for attr_name in attr_names:
            logging.debug(f"validating: {attr_name}")
            attr = self.__getattribute__(attr_name)
            if isinstance(attr, Config):
                attr._validate()

            # Check files from scheme
            if isinstance(attr, Path):
                self.__setattr__(attr_name, attr.resolve())
                if not str(attr) in [
                    "distances.dat"
                ]:  # distances.dat wouldn't exist prior to the run
                    logging.debug(attr)
                    check_file_exists(attr)

            # Check config for consistency
            if attr_name in ["nvt", "npt"]:
                for necessary_f in ["mdp", "tpr"]:
                    assert (
                        necessary_f in attr.__dir__()
                    ), f"{necessary_f} for {attr_name} is missing in config!"

            # Checks
            if (
                attr_name == "reactions"
            ):  # changed reactions to be no longer a list but it yields a wrong value
                logging.info(
                    f"DUMMY VALIDATION: There are {len(attr.__dict__)-2} reactions!"
                )

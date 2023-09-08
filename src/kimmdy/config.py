"""
Read and validate kimmdy.yml configuration files
and package into a parsed format for internal use.
"""
from __future__ import annotations
import shutil
from pprint import pformat, pprint
from typing import Any, Optional
import yaml
from pathlib import Path
from kimmdy import reaction_plugins
from kimmdy.schema import Sequence, get_combined_scheme
from kimmdy.utils import get_gmx_dir


def check_file_exists(p: Path):
    if not p.exists():
        m = f"File not found: {p}"
        raise LookupError(m)


class Config:
    """Internal representation of the configuration generated
    from the input file, which enables validation before running
    and computationally expensive operations.

    Parameters
    ----------
    input_file
        Path to the config yaml file.
    recursive_dict
        For internal use only, used in reading settings in recursively.
    scheme
        dict containing types and defaults for casting and validating settings.
    section
        current section e.g. to determine the level of recursion in nested configs
        e.g. "config", "config.mds" or "config.reactions.homolysis"
    """

    # override get and set attributes to satisy type checker and
    # acknowledge that we don't actually statically type-check the attributes
    def __getattribute__(self, name) -> Any:
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value: Any):
        object.__setattr__(self, name, value)

    def __init__(
        self,
        input_file: Path | None = None,
        recursive_dict: dict | None = None,
        scheme: dict | None = None,
        section: str = "config",
        logfile: Optional[Path] = None,
        loglevel: Optional[str] = None,
    ):
        # failure case: no input file and no values from dictionary
        if input_file is None and recursive_dict is None:
            m = "No input file was provided for Config"
            raise ValueError(m)

        # initial scheme
        if scheme is None:
            scheme = get_combined_scheme()

        # read initial input file
        if input_file is not None:
            with open(input_file, "r") as f:
                raw = yaml.safe_load(f)
            if raw is None or not isinstance(raw, dict):
                m = "Could not read input file"
                raise ValueError(m)
            recursive_dict = raw

        assert recursive_dict is not None
        assert scheme is not None
        # go over parsed yaml file recursively
        # this is what is on the config
        for k, v in recursive_dict.items():
            if isinstance(v, dict):
                # recursive case

                # merge ".*" and specific schemes
                general_subscheme = scheme.get(".*")
                subscheme = scheme.get(k)
                if subscheme is None:
                    subscheme = general_subscheme
                if general_subscheme is not None:
                    general_subscheme.update(subscheme)
                    subscheme = general_subscheme
                assert subscheme is not None, (k, v, scheme)
                subsection = f"{section}.{k}"
                subconfig = Config(
                    recursive_dict=v, scheme=subscheme, section=subsection
                )
                self.__setattr__(k, subconfig)
            else:
                # base case for recursion
                global_opts = scheme.get(".*")
                opts = scheme.get(k)
                if opts is None and global_opts is None:
                    m = f"Unknown option {section}.{k} found in config file."
                    raise ValueError(m)
                if opts is None:
                    opts = {}
                if global_opts is not None:
                    opts.update(global_opts)
                pytype = opts.get("pytype")
                if pytype is None:
                    m = f"No type found for {section}.{k}"
                    raise ValueError(m)

                # cast to type
                v = pytype(v)
                self.__setattr__(k, v)

        # validate on initial construction
        if section == "config":
            self._logmessages = {"infos": [], "warnings": [], "errors": []}
            self._set_defaults(section, scheme)
            self._validate()

            # merge command line arguments
            # with config file
            if logfile is None:
                self.log.file = self.out / self.log.file
            else:
                self.log.file = logfile
            if loglevel is None:
                loglevel = self.log.level
            else:
                self.log.level = loglevel

            # write a copy of the config file to the output directory
            assert input_file, "No input file provided"
            shutil.copy(input_file, self.out)

    def _set_defaults(self, section: str = "config", scheme: dict = {}):
        """
        Set defaults for attributes not set in yaml file but
        specified in scheme (generated from the schema).
        """

        # implicit defaults not in the schema
        # but defined in terms of other attributes
        if section == "config":
            if not hasattr(self, "cwd"):
                self.cwd = Path.cwd()
            if not hasattr(self, "out"):
                self.out = self.cwd / self.name

        if section.startswith("config.mds"):
            print('hello')
            print(scheme)
            exit()

        for k, v in scheme.items():
            if type(v) is not dict:
                continue
            pytype = v.get("pytype")
            if pytype is not None:
                # this is a base case since
                # only leaves have a pytype
                if not hasattr(self, k):
                    # get default if not set in yaml
                    default = v.get("default")
                    if default is None:
                        # f"No default for required option {section}.{k} in schema and not set in yaml"
                        continue
                    default = pytype(default)
                    self.__setattr__(k, default)

            else:
                # this is a recursive case
                if not hasattr(self, k):
                    # the current section doen't exist
                    # but might have a default in a subsection

                    # don't set defaults for reactions
                    # as only those requested by the user
                    # should be defined
                    if section == "config.reactions":
                        continue

                    empty_config = Config.__new__(type(self))
                    empty_config._set_defaults(f"{section}.{k}", v)
                    self.__setattr__(k, empty_config)
                else:
                    # the current section exists
                    # and might have defaults in a subsection
                    self.__getattribute__(k)._set_defaults(f"{section}.{k}", v)


    def _validate(self, section: str = "config"):
        """Validates config."""

        # TODO: check for required attributes

        # globals / interconnected
        if section == "config":

            ffdir = self.ff
            if ffdir == Path("*.ff"):
                ffs = list(self.cwd.glob("*.ff"))
                if len(ffs) > 1:
                    self._logmessages["warnings"].append(
                        f"Found {len(ffs)} forcefields in cwd, using first one: {ffs[0]}"
                    )
                assert ffs[0].is_dir(), "Forcefield should be a directory!"
                ffdir = ffs[0].resolve()
            elif not ffdir.exists():
                gmxdir = get_gmx_dir(self.gromacs_alias)
                if gmxdir is None:
                    self._logmessages["warnings"].append(
                        f"Could not find gromacs data directory for {self.gromacs_alias}"
                    )
                    gmxdir = self.cwd
                gmx_builtin_ffs = gmxdir / "top"
                ffdir = gmx_builtin_ffs / ffdir
                if not ffdir.exists():
                    self._logmessages["warnings"].append(
                        f"Could not find forcefield {ffdir} in cwd or gromacs data directory"
                    )
            self.ff = ffdir

            # Validate changer reference
            if hasattr(self, "changer"):
                if hasattr(self.changer, "coordinates"):
                    if "md" in self.changer.coordinates.__dict__.keys():
                        assert (
                            self.changer.coordinates.md in self.mds.__dict__.keys()
                        ), f"Relax MD {self.changer.coordinates.md} not in MD section!"

            # Validate reaction plugins
            if hasattr(self, "reactions"):
                for reaction_name, reaction_config in self.reactions.__dict__.items():
                    assert reaction_name in (ks := list(reaction_plugins.keys())), (
                        f"Error: Reaction plugin {reaction_name} not found!\n"
                        + f"Available plugins: {ks}"
                    )
                    if isinstance(reaction_plugins[reaction_name], Exception):
                        raise reaction_plugins[reaction_name]

            # Validate sequence
            assert hasattr(self, "sequence"), "No sequence defined!"
            for task in self.sequence:
                if not hasattr(self, task):
                    if hasattr(self, "mds"):
                        if hasattr(self.mds, task):
                            continue
                    if hasattr(self.reactions, task):
                        continue
                    raise AssertionError(
                        f"Task {task} listed in sequence, but not defined!"
                    )

            # Validate plumed defined if requested in md run
            if hasattr(self, "mds"):
                needs_plumed = False
                for attr_name in self.mds.get_attributes():
                    if hasattr(getattr(self.mds, attr_name), "use_plumed"):
                        if getattr(getattr(self.mds, attr_name), "use_plumed"):
                            needs_plumed = True
                if needs_plumed:
                    if not hasattr(self, "plumed"):
                        raise AssertionError(
                            "Plumed requested in md section, but not defined at config root"
                        )

            # make sure self.out is empty
            while self.out.exists():
                self._logmessages["warnings"].append(
                    f"Output dir {self.out} exists, incrementing name"
                )
                name = self.out.name.split("_")
                out_end = name[-1]
                out_start = "_".join(name[:-1])
                if out_end.isdigit():
                    self.out = self.out.with_name(f"{out_start}_{int(out_end)+1:03}")
                else:
                    self.out = self.out.with_name(self.out.name + "_001")

            self.out.mkdir()
            self._logmessages["infos"].append(f"Created output dir {self.out}")

        # individual attributes, recursively
        for name, attr in self.__dict__.items():
            if type(attr) is Config:
                attr._validate(section=f"{section}.{name}")
                continue

            # Check files from scheme
            elif isinstance(attr, Path):
                path = attr
                path = path.resolve()
                self.__setattr__(name, path)
                if not path.is_dir():
                    check_file_exists(path)

        return

    def attr(self, attribute):
        """Get the value of a specific attribute.
        Alias for self.__getattribute__
        """
        return self.__getattribute__(attribute)

    def get_attributes(self):
        """Get a list of all attributes"""
        return list(self.__dict__.keys())

    def __repr__(self):
        return pformat(self.__dict__, indent=2)

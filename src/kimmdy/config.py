"""
Read and validate kimmdy.yml configuration files
and package into a parsed format for internal use.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from pprint import pformat
from typing import Any, Optional

import yaml

from kimmdy.plugins import (
    broken_parameterization_plugins,
    broken_reaction_plugins,
    parameterization_plugins,
    reaction_plugins,
)
from kimmdy.schema import get_combined_scheme
from kimmdy.utils import check_file_exists, check_gmx_version, get_gmx_dir


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
        # this is what is in the config
        for k, v in recursive_dict.items():
            if isinstance(v, dict):
                # recursive case

                # merge ".*" and specific schemes
                # before recursing
                general_subscheme = scheme.get(".*")
                subscheme = scheme.get(k)
                if subscheme is None:
                    subscheme = general_subscheme
                if general_subscheme is not None:
                    general_subscheme.update(subscheme)
                    subscheme = general_subscheme
                if subscheme is None:
                    raise RuntimeError(
                        "Config could not be generated. \nCheck installed "
                        "plugins with --show-plugins and your input .yml\n"
                        f"k: {k}\nv: {v}\nscheme: {scheme}"
                    )
                subsection = f"{section}.{k}"
                subconfig = Config(
                    recursive_dict=v, scheme=subscheme, section=subsection
                )
                self.__setattr__(k, subconfig)
            else:
                # base case for recursion
                opts = scheme.get(k)
                if opts is None:
                    if scheme.get("additionalProperties"):
                        # property "additionalProperties" marks objects that have additional properties
                        # so we can just set them
                        self.__setattr__(k, v)
                        continue
                    m = f"Unknown option {section}.{k} found in config file."
                    if "reactions." in section:
                        m += "\nCheck installed plugins with --show-plugins and your input .yml"
                    raise ValueError(m)
                pytype = opts.get("pytype")
                if pytype is None:
                    m = f"No type found for {section}.{k}"
                    raise ValueError(m)

                # cast to type
                v = pytype(v)
                self.__setattr__(k, v)

        # validate on initial construction
        if section == "config":
            self._logmessages = {"infos": [], "warnings": [], "errors": [], "debug": []}
            self._set_defaults(section, scheme)
            self._validate()

            # merge with command line arguments
            self.input_file = input_file
            if logfile is None:
                self.log.file = self.out / self.log.file
            else:
                self.log.file = logfile
            if loglevel is None:
                loglevel = self.log.level
            else:
                self.log.level = loglevel

            # write a copy of the config file to the output directory
            if input_file is not None:
                shutil.copy(input_file, self.out)
            else:
                self._logmessages["infos"].append(
                    "Config initialized without input file, can't copy to output directory."
                )

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

        # don't do anything for the description of a section
        scheme.pop("description", None)

        # if the section has a general subscheme
        # merge it into all specific subschemes
        # (yes, this has to happen twice, first in __init__ for setting
        # the types and here for setting the defaults)
        general_subscheme = scheme.pop(".*", None)
        if general_subscheme is not None:
            subsections = self.get_attributes()
            for subsection in subsections:
                subscheme = scheme.get(subsection, None)
                if subscheme is None:
                    scheme[subsection] = general_subscheme
                else:
                    general_subscheme.update(subscheme)
                    scheme[subsection] = general_subscheme

        for k, v in scheme.items():
            if type(v) is not dict:
                # raise ValueError(f"Scheme entry {section}.{k}: {v} is not a dict.")
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

                    # don't set sections for all known reactions,
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
        # globals / interconnected
        if section == "config":
            ffdir = self.ff
            if ffdir == Path("*.ff"):
                ffs = list(self.cwd.glob("*.ff"))
                if len(ffs) > 1:
                    self._logmessages["warnings"].append(
                        f"Found {len(ffs)} forcefields in cwd, using first one: {ffs[0]}"
                    )
                if len(ffs) == 0:
                    m = f"Found 0 forcefields in cwd. Please specify a forcefield to be used from the gromacs directory or add a .ff folder to the cwd or preceed at your own risk."
                    self._logmessages["warnings"].append(m)
                    ffdir = None
                else:
                    ffdir = ffs[0].resolve()
                    assert ffdir.is_dir(), "Forcefield should be a directory!"
            elif ffdir is not None and not ffdir.exists():
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

            self.name = self.name.replace(" ", "_")
            self.out = self.out.with_name(self.out.name.replace(" ", "_"))

            # Validate changer reference
            if hasattr(self, "changer"):
                if hasattr(self.changer, "coordinates"):
                    if "md" in self.changer.coordinates.__dict__.keys():
                        assert (
                            self.changer.coordinates.md in self.mds.__dict__.keys()
                        ), f"Relax MD {self.changer.coordinates.md} not in MD section!"

            # Validate reaction plugins
            if hasattr(self, "reactions"):
                for reaction_name in self.reactions.__dict__.keys():
                    if reaction_name not in (ks := list(reaction_plugins.keys())):
                        if reaction_name in (list(broken_reaction_plugins.keys())):
                            m = f"Reaction plugin {reaction_name} could not be loaded."
                            self._logmessages["errors"].append(m)
                            raise broken_reaction_plugins[reaction_name]
                        else:
                            m = f"Reaction plugin {reaction_name} not found!\nAvailable plugins: {ks}"
                            self._logmessages["errors"].append(m)
                            raise ModuleNotFoundError(m)

            # Validate parameterization plugins
            parameterizer_name = self.changer.topology.parameterization
            if parameterizer_name != "basic":
                if parameterizer_name not in (
                    ks := list(parameterization_plugins.keys())
                ):
                    if parameterizer_name in (
                        list(broken_parameterization_plugins.keys())
                    ):
                        m = f"Parameterization plugin {parameterizer_name} could not be loaded."
                        self._logmessages["errors"].append(m)
                        raise broken_parameterization_plugins[parameterizer_name]
                    else:
                        m = f"Parameterization plugin {parameterizer_name} not found!\nAvailable plugins: {ks}"
                        self._logmessages["errors"].append(m)
                        raise ModuleNotFoundError(m)

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
                    # also check whether section has mdp file
                    if not hasattr(getattr(self.mds, attr_name), "mdp"):
                        raise AssertionError(
                            "MD instance defined but contains no mdp file."
                        )
                    if hasattr(getattr(self.mds, attr_name), "use_plumed"):
                        if getattr(getattr(self.mds, attr_name), "use_plumed"):
                            needs_plumed = True
                if needs_plumed:
                    if not hasattr(self, "plumed"):
                        raise AssertionError(
                            "Plumed requested in md section, but not defined at config root"
                        )
                    check_gmx_version(self)

            # make sure self.out is empty
            while self.out.exists():
                self._logmessages["debug"].append(
                    f"Output dir {self.out} exists, incrementing name"
                )
                name = self.out.name.split("_")
                out_end = name[-1]
                if out_end.isdigit():
                    self.out = self.out.with_name(
                        f"{'_'.join(name[:-1])}_{int(out_end)+1:03}"
                    )
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
        """Get a list of all attributes without hidden ones (_<...>)."""
        return list(filter(lambda x: not x.startswith("_"), self.__dict__.keys()))

    def __repr__(self):
        return pformat(self.__dict__, indent=2)

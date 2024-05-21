"""
Handle the schema for the config file.
To  be used by the config module to validate the config file and set defaults
for the Config object.

Reserved keywords:
    - pytype
    - default
    - description
    - type
    - required
"""

import importlib.resources as pkg_resources
import json
import logging

# needed for eval of type_scheme from schema
# don't remove even if lsp says it's unused
import pathlib
from pathlib import Path

import kimmdy
from kimmdy.plugins import reaction_plugins

logger = logging.getLogger(__name__)


class Sequence(list):
    """A sequence of tasks.

    Tasks can be grouped together by using a dictionary with the following
    keys:
        - mult: number of times to repeat the tasks
        - tasks: list of tasks to repeat

    Attributes
    ----------
    tasks:
        list of tasks
    """

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

    def __repr__(self):
        return f"Sequence({list.__repr__(self)})"


def load_kimmdy_schema() -> dict:
    """Return the schema for the config file"""
    path = pkg_resources.files(kimmdy) / "kimmdy-yaml-schema.json"
    with path.open("r") as f:
        schema = json.load(f)
    return schema


def load_plugin_schemas() -> dict:
    """Return the schemas for the reaction plugins known to kimmdy"""

    schemas = {}
    for plg_name, plugin in reaction_plugins.items():
        logger.debug(f"Loading {plg_name}")
        # Catch loading exception
        if type(plugin) is ModuleNotFoundError:
            logger.warning(f"Plugin {plg_name} could not be loaded!\n{plugin}\n")
            continue
        # get main module from that plugin
        plg_module_name = plugin.__module__.split(".")[0]
        if plg_module_name == "kimmdy":
            continue
        schema_path = pkg_resources.files(plg_module_name) / "kimmdy-yaml-schema.json"
        with pkg_resources.as_file(schema_path) as p:
            if not p.exists():
                logger.warning(
                    f"{plg_name} did not provide a `kimmdy-yaml-schema.json`!\n"
                    "Schema will not be loaded!"
                )
                continue
            with open(p, "rt") as f:
                schemas[plg_name] = json.load(f)

    return schemas


def convert_schema_to_dict(dictionary: dict) -> dict:
    """Convert a dictionary from a raw json schema to a nested dictionary

    Parameters
    ----------
    dictionary:
        dictionary from a raw json schema

    Returns
    -------
        nested dictionary where each leaf entry is a dictionary with the
        "pytype", "default" and "description" keys.
    """
    result = {}
    properties = dictionary.get("properties")
    patternProperties = dictionary.get("patternProperties")
    if properties is None and patternProperties is not None:
        properties = patternProperties
    if properties is None:
        return result
    if patternProperties is not None:
        properties.update(patternProperties)
    for key, value in properties.items():
        if not isinstance(value, dict):
            continue
        result[key] = {}
        json_type = value.get("type")
        if json_type == "object":
            result[key] = convert_schema_to_dict(value)

        pytype = value.get("pytype")
        default = value.get("default")
        description = value.get("description")
        additionalProperties = value.get("additionalProperties")
        if pytype is not None:
            result[key]["pytype"] = eval(pytype)
        if default is not None:
            result[key]["default"] = default
        if description is not None:
            result[key]["description"] = description
        if additionalProperties is not None:
            result[key]["additionalProperties"] = additionalProperties

    return result


def get_combined_scheme() -> dict:
    """Return the schema for the config file.

    Nested scheme where each leaf entry is a dictionary with the "pytype",
    "default" and "description".
    Contains the schema for the main kimmdy config file and all the plugins
    known at runtime.
    """
    schema = load_kimmdy_schema()
    schemas = load_plugin_schemas()
    kimmdy_dict = convert_schema_to_dict(schema)
    plugin_dicts = {k: convert_schema_to_dict(schema) for k, schema in schemas.items()}
    for k, v in plugin_dicts.items():
        kimmdy_dict["reactions"].update({k: v})

    return kimmdy_dict


def prune(d: dict) -> dict:
    """Remove empty dicts from a nested dict"""
    if not isinstance(d, dict):
        return d
    return {
        k: v
        for k, v in ((k, prune(v)) for k, v in d.items())
        if v is not None and v != {}
    }


def flatten_scheme(scheme, section="") -> list:
    """Recursively get properties and their desicripions from the scheme"""
    ls = []
    for key, value in scheme.items():
        if not isinstance(value, dict):
            continue
        if section:
            key = f"{section}.{key}"

        description = value.get("description", "")
        pytype = value.get("pytype")
        if pytype is not None:
            pytype = pytype.__name__
        else:
            pytype = ""
        default = value.get("default", "")

        ls.append((key, description, pytype, default))

        for k in value.keys():
            if k not in ["pytype", "default", "description", "type"]:
                k_esc = k
                if k == ".*":
                    k_esc = "\\*"
                s = f"{key}.{k_esc}"
                ls.extend(flatten_scheme(value[k], section=s))

    return ls


def generate_markdown_table(scheme, append=False):
    """Generate markdown table from scheme

    Used in documentation generation.
    """
    table = []
    if not append:
        table.append("| Option | Description | Type | Default |")
        table.append("| --- | --- | --- | --- | --- |")

    for key, pytype, description, default in scheme:
        if pytype == "":
            key = f"**{key}**"
        row = f"| {key} | {pytype} | {description} | {default} |"
        table.append(row)

    return "\n".join(table)

from __future__ import annotations  # for 3.7 <= Python version < 3.10

import logging
import re
from itertools import permutations
from typing import TYPE_CHECKING, Any, Callable, Optional

from kimmdy.constants import ION_NAMES, REACTIVE_MOLECULEYPE, SOLVENT_NAMES
from kimmdy.parsing import empty_section
from kimmdy.topology.atomic import MoleculeTypeHeader

if TYPE_CHECKING:
    from kimmdy.config import Config
    from kimmdy.topology.atomic import Atom, AtomicType, AtomicTypes
    from kimmdy.topology.topology import Topology

logger = logging.getLogger(__name__)


def get_top_section(
    top: dict, name: str, moleculetype: Optional[str] = None
) -> Optional[list[list]]:
    """Get content of a section from a topology dict.

    Resolves any `#ifdef` statements by check in the top['define'] dict
    and chooses the 'content' or 'else_content' depending on the result.
    """
    if moleculetype is not None:
        parent_name = f"moleculetype_{moleculetype}"
        parent_section = top.get(parent_name)
        if parent_section is None:
            logger.warning(f"topology does not contain moleculetype {moleculetype}")
            return None
        section = parent_section["subsections"].get(name)
    else:
        section = top.get(name)

    if section is None:
        logger.debug(
            f"Topology does not have {name}. "
            "If you expet to find this sectioh, check that "
            "the forcefield is in the current working directory config.cwd."
        )
        return None
    condition = section.get("condition")
    if condition is not None:
        condition_type = condition.get("type")
        condition_value = condition.get("value")
        if condition_type == "ifdef":
            if condition_value in top["define"].keys():
                return section.get("content")
            else:
                return section.get("else_content")
        elif condition_type == "ifndef":
            if condition_value not in top["define"].keys():
                return section.get("content")
            else:
                return section.get("else_content")
        else:
            raise NotImplementedError(
                f"condition type {condition_type} is not supported"
            )
    return section.get("content")


def get_moleculetype_header(
    top: dict, moleculetype: str
) -> Optional[MoleculeTypeHeader]:
    """Get content of the header of a moleculetype from a topology dict.

    Resolves any `#ifdef` statements by check in the top['define'] dict
    and chooses the 'content' or 'else_content' depending on the result.
    """
    section = top.get(moleculetype)
    if section is None:
        logger.warning(f"topology does not contain moleculetype {moleculetype}")
        return None

    condition = section.get("condition")
    if condition is not None:
        condition_type = condition.get("type")
        condition_value = condition.get("value")
        if condition_type == "ifdef":
            if condition_value in top["define"].keys():
                return section.get("content")
            else:
                return section.get("else_content")
        elif condition_type == "ifndef":
            if condition_value not in top["define"].keys():
                return section.get("content")
            else:
                return section.get("else_content")
        else:
            raise NotImplementedError(
                f"condition type {condition_type} is not supported"
            )
    name, nrexcl = section.get("content")[0]
    if name is None:
        logger.info(
            f"name not found in moleculetype {moleculetype}. Defaulting to Unknown."
        )
        name = "Unknown"
    if nrexcl is None:
        logger.info(
            f"nrexcl not found in moleculetype {moleculetype}. Defaulting to 3."
        )
        nrexcl = "3"
    return MoleculeTypeHeader(name=name, nrexcl=nrexcl)


def get_moleculetype_atomics(top: dict, moleculetype: str) -> Optional[dict]:
    """Get content of subsections (atoms/bonds/angles etc.) of a moleculetype from a topology dict.

    Resolves any `#ifdef` statements by check in the top['define'] dict
    and chooses the 'content' or 'else_content' depending on the result.
    """
    section = top.get(moleculetype)
    if section is None:
        logger.warning(f"topology does not contain moleculetype {moleculetype}")
        return None

    subsections = section["subsections"]
    atomics = {}
    for k, v in subsections.items():
        condition = v.get("condition")
        if condition is not None:
            condition_type = condition.get("type")
            condition_value = condition.get("value")
            if condition_type == "ifdef":
                if condition_value in top["define"].keys():
                    atomics[k] = v.get("content")
                else:
                    atomics[k] = v.get("else_content")
            elif condition_type == "ifndef":
                if condition_value not in top["define"].keys():
                    atomics[k] = v.get("content")
                else:
                    atomics[k] = v.get("else_content")
            else:
                raise NotImplementedError(
                    f"condition type {condition_type} is not supported"
                )
        else:
            atomics[k] = v["content"]

    return atomics


def get_protein_section(top: dict, name: str) -> Optional[list[list]]:
    """Get content of a section in the first moleculetype (protein) from a topology dict."""
    return get_top_section(top, name, moleculetype="Protein")


def get_reactive_section(top: dict, name: str) -> Optional[list[list]]:
    """Get content of a section in the Reactive moleculetype from a topology dict."""
    return get_top_section(top, name, moleculetype=REACTIVE_MOLECULEYPE)


def set_top_section(
    top: dict, name: str, value: list, moleculetype: Optional[str] = None
) -> Optional[list[list]]:
    """Set content of a section from a topology dict.

    Resolves any `#ifdef` statements by check in the top['define'] dict
    and chooses the 'content' or 'else_content' depending on the result.
    """
    if moleculetype is not None:
        parent_name = f"moleculetype_{moleculetype}"
        parent_section = top.get(parent_name)
        if parent_section is None:
            raise ValueError(f"topology does not contain moleculetype {moleculetype}")
        section = parent_section["subsections"].get(name)
    else:
        section = top.get(name)

    if section is None:
        m = f"topology does not contain section {name}"
        logger.warning(m)
        return None
    condition = section.get("condition")
    if condition is not None:
        condition_type = condition.get("type")
        condition_value = condition.get("value")
        if condition_type == "ifdef":
            if condition_value in top["define"].keys():
                section["content"] = value
            else:
                section["else_content"] = value
        elif condition_type == "ifndef":
            if condition_value not in top["define"].keys():
                section["content"] = value
            else:
                section["else_content"] = value
        else:
            raise NotImplementedError(
                f"condition type {condition_type} is not supported"
            )
    section["content"] = value


def set_reactive_section(top: dict, name: str, value: list) -> Optional[list[list]]:
    """Set content of a section in the first moleculetype (protein) from a topology dict."""
    set_top_section(top, name, value, moleculetype=REACTIVE_MOLECULEYPE)


def attributes_to_list(obj) -> list[str]:
    attrs = []
    for k, v in obj.__dict__.items():
        if k in ["bound_to_nrs", "is_radical", "id", "id_sym"]:
            continue
        if v in [None, ""]:
            continue
        attrs.append(v)
    return attrs


def is_not_none(x) -> bool:
    return x is None


def get_by_permutations(d: dict, key) -> Optional[Any]:
    for k in permutations(key):
        value = d.get(k, None)
        if value is not None:
            return value
    return None


def match_atomic_item_to_atomic_type(
    id: list[str], types: AtomicTypes, periodicity: str = ""
) -> Optional[AtomicType]:
    def escape_re_atomtypes(s: str) -> str:
        """
        escape special regex characters
        that can appear in a forcecield
        use X as the wildcard
        """
        return s.replace("*", "STAR").replace("+", "PLUS")

    id = [escape_re_atomtypes(s) for s in id]
    id_sym = id[::-1]
    id_str = "---".join(id)
    id_sym_str = "---".join(id_sym)
    if periodicity:
        id[-1] += ":::" + periodicity
        id_sym[-1] += ":::" + periodicity
        id_str += ":::" + periodicity
        id_sym_str += ":::" + periodicity
    result = None
    longest_match = 0
    for _, atomic_type in types.items():
        for key in [atomic_type.id, atomic_type.id_sym]:
            key = escape_re_atomtypes(key).replace("X", ".*")
            keys = key.split("---")
            # early return exact match
            if key == id_str or key == id_sym_str:
                return atomic_type
            matches = [re.match(pattern, s) for pattern, s in zip(keys, id)]
            if all(matches):
                # favor longer (=more specific) and later matches
                if len(key) >= longest_match:
                    longest_match = len(key)
                    result = atomic_type

    return result


def increment_field(l: list[str], i: int, n: int):
    l[i] = str(int(l[i]) + n)
    return l


def is_not_solvent_or_ion(name: str) -> bool:
    """Returns whether a moleculetype name is not solvent or ion."""
    return name.lower() not in [x.lower() for x in SOLVENT_NAMES + ION_NAMES]


def get_is_reactive_predicate_from_config_f(cfg: Config) -> Callable[[str], bool]:
    """Returns whether a moleculetype name is configured to be recognized as reactive."""
    include = [x.lower() for x in cfg.include.split()]
    exclude = [x.lower() for x in cfg.exclude.split()]
    return get_is_reactive_predicate_f(include, exclude)


def get_is_reactive_predicate_f(include: list, exclude: list) -> Callable[[str], bool]:
    """Returns whether a moleculetype name is configured to be recognized as reactive."""
    default_excludes = [x.lower() for x in SOLVENT_NAMES + ION_NAMES]

    def f(name: str) -> bool:
        lower_name = name.lower()
        return lower_name not in exclude and (
            lower_name not in default_excludes or lower_name in include
        )

    return f


def get_residue_fragments(
    top: Topology,
    residue: list[Atom],
    start1: Atom,
    start2: Atom,
    iterations: int = 20,
) -> tuple[set, set]:
    """Splits a residue into fragments after a bond has been broken.

    Parameters
    ----------
    top
        Topology
    residue
        All atoms of current residue. Ok, when it contains more atoms
        as long as those are not connected to broken bond.
    start1
        First atom with broken bond
    start2
        Second atom with broken bond
    iterations
        Max number of bonds from start atoms to be included when
        building fragmets, by default 20

    Returns
    -------
        Two fragments, or one fragment and empty set in case the
        residue did not change its size.
    """
    residue_nrs = set([atom.nr for atom in residue])
    # could remove duplicate calculations
    fragments = [set([start1.nr]), set([start2.nr])]
    for fragment in fragments:
        for _ in range(iterations):
            neighbors = set()
            for nr in fragment:
                neighbors.update(set(top.atoms[nr].bound_to_nrs))
            fragment.update(neighbors.intersection(residue_nrs))
        if len(fragment) == len(residue):
            logger.debug(
                "Calculating residue fragments, but residue is whole! "
                "Could be intra-residue reaction!"
            )
            return fragment, set()
    return fragments[0], fragments[1]

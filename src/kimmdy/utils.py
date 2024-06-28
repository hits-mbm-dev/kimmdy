"""
Utilities for building plugins, shell convenience functions and GROMACS related functions
"""

from __future__ import annotations

import logging
import re
import subprocess as sp
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import numpy as np

if TYPE_CHECKING:
    from kimmdy.parsing import Plumed_dict
    from kimmdy.tasks import TaskFiles
    from kimmdy.topology.topology import Topology

logger = logging.getLogger(__name__)

TopologyAtomAddress = str
"""Address to an atom in the topology.

Corresponds to the 1-based id in the topology and coordinates file.
Note, that gromacs ids in the atomnr column of the gro file
can overflow due to the fixed width file format.
The line number - 2 (for the title and the number of atoms) is always
the correct the atom id.
"""


def field_or_none(l: list[str], i) -> Optional[str]:
    try:
        return l[i]
    except IndexError as _:
        return None


class longFormatter(logging.Formatter):
    def format(self, record):
        saved_name = record.name  # save and restore for other formatters if desired
        parts = saved_name.split(".")
        if len(parts) > 1:
            record.name = parts[0][0] + "." + ".".join(p[:10] for p in parts[1:])
        else:
            record.name = parts[0]
        result = super().format(record)
        record.name = saved_name
        return result


### IO utility functions ###


def run_shell_cmd(s, cwd=None) -> sp.CompletedProcess:
    """Run command in shell."""
    return sp.run(s, shell=True, cwd=cwd, capture_output=True, text=True)


def run_gmx(s: str, cwd=None) -> Optional[sp.CalledProcessError]:
    """Run GROMACS command in shell.

    Adds a '-quiet' flag to the command and checks the return code.
    """
    logger.debug(f"Starting Gromacs process with command {s} in {cwd}.")
    result = run_shell_cmd(f"{s} -quiet", cwd)
    if result.returncode != 0:
        logger.error(f"Gromacs process with command {s} in {cwd} failed.")
        logger.error(f"Gromacs exit code {result.returncode}.")
        logger.error(f"Gromacs stdout:\n{result.stdout}.")
        logger.error(f"Gromacs stderr:\n{result.stderr}.")
        result.check_returncode()


def get_shell_stdout(s):
    """Run command in shell and capture stdout."""
    process = sp.run(s, shell=True, capture_output=True, encoding="utf-8")
    return process.stdout


def check_file_exists(path: Path, option_name: str | None = None):
    if not path.exists():
        if option_name:
            m = f"File not found: {path} for {option_name}"
        else:
            m = f"File not found: {path}"
        raise LookupError(m)


### reaction plugin building blocks ###


def get_atomnrs_from_plumedid(
    plumedid: str,
    plumed: Plumed_dict,
) -> list[str]:
    """
    Convert from plumedid to atomnr, information from the plumed file is used.

    Parameters
    ----------
    plumedid:
        Identifier from a plumed input file (e.g d0).
    plumed:
        Parsed plumed input file
    """
    # lookup_atomnr_plumedid = {k: frozenset(v["atoms"])
    plumed_action = plumed["labeled_action"][plumedid]
    if a := plumed_action.get("atoms"):
        atomnrs = sorted(a, key=int)
        return atomnrs
    else:
        raise NotImplementedError(
            f"Can't get atomnrs for {plumedid}, is this for an unexpected plumed action?"
        )


def get_atominfo_from_atomnrs(
    atomnrs: list[str], top: Topology
) -> tuple[list[str], list[str]]:
    """Use topology atoms section to convert from atomnr to atomtype"""
    atomtypes = []
    atomnames = []
    for atomnr in atomnrs:
        atomtypes.append(top.atoms[atomnr].type)
        atomnames.append(top.atoms[atomnr].atom)
    return atomtypes, atomnames


def get_bondprm_from_atomtypes(
    atomtypes: list[str],
    ffbonded: dict,
) -> tuple[float, float]:
    """Returns bond parameters (b0, kb) for a set of two atomtypes.

    Parameters
    ----------
    atomtypes:
        Two atomtypes as defined in the respective force field
    ffbonded:
        Force field ffbonded.itp file parsed through the rtp parser
    """
    # search for b0 and kb for the given atomtypes in ffbonded bondtypes
    for bondtype in ffbonded["bondtypes"]["content"]:
        if set(atomtypes) == set(bondtype[:2]):
            b0, kb = [float(x) for x in bondtype[3:5]]
            break
    else:
        raise KeyError(
            f"Did not find bond parameters for atomtypes {atomtypes} in ffbonded file"
        )

    return b0, kb


def get_edissoc_from_atomnames(
    atomnames: list[str], edissoc: dict, residue: str = "_"
) -> float:
    """Returns dissociation energy E_dissoc for a set of two atomnames.

    Parameters
    ----------
    atomnames:
        Two atomnames as defined in the respective force field
    edissoc:
        Parsed file with dissociation energies per bond between two atomtypes or elements
    residue:
        Residue for which the atomnames are defined
    """
    if residue not in edissoc.keys():
        if "general" in edissoc.keys():
            logger.debug(
                f"residue {residue} not in edissoc keys: {edissoc.keys()}, using 'general' as residue."
            )
            residue = "general"
        else:
            raise KeyError(f"Did not find residue {residue} in edissoc file")

    try:
        E_dis = edissoc[residue][frozenset(atomnames)]
    except KeyError:
        # continue with guessed edissoc
        logger.warning(
            f"Did not find dissociation energy for atomtypes {atomnames}, residue {residue} in edissoc file, using standard value of 400.0"
        )
        E_dis = 400.0
        # # raise Error
        # raise KeyError(
        #     f"Did not find dissociation energy for atomtypes {atomnames}, residue {residue} in edissoc file"
        # ) from e

    return E_dis


def morse_transition_rate(
    r_curr: list[float],
    r_0: float,
    dissociation_energy: float,
    k_f: float,
    frequency_factor: float = 0.288,
    temperature: float = 300,
) -> tuple[list[float], list[float]]:
    """Calculates reaction rate constant for a bond breaking event.

    Uses the Morse potential model for this calculation. For an array of bond distances of the same bond,
    first calculates the forces on the bond, then the minima and maxima of the shifted Morse potential
    to get an energy barrier and finally a reaction rate constant using the Arrhenius equation.
    For intramolecular reactions, the reaction rate constant is equal to the reaction rate.

    The calculation should be according to the derivation in the original KIMMDY paper: DOI: 10.1021/acs.jctc.9b00786

    Parameters
    ----------
    r_curr:
        Bond distances for a single bond, typically from a time series.
    r_0:
        Equilibrium bond length of the bond.
    dissociation energy:
        Dissociation energy of the bond.
    k_f:
        Spring constant of the bond.
    frequency_factor:
        Prefactor of the Arrhenius equation in [1/ps]. Default value from fitting averaged C_a - N data to gromacs data, see original KIMMDY paper
        Alternatively 1/2pi sqrt(k/m).
    temperature:
        Temperature for the Arrhenius equation in GROMACS units.

    """
    rs = np.asarray(r_curr)
    beta = np.sqrt(k_f / (2 * dissociation_energy))

    # calculate forces on bond
    fs = (
        2
        * beta
        * dissociation_energy
        * np.exp(-beta * (rs - r_0))
        * (1 - np.exp(-beta * (rs - r_0)))
    )

    # if the bond is stretched beyond the inflection point, take the inflection point force because this force must have acted on the bond at some point
    r_inflection = (beta * r_0 + np.log(2)) / beta
    f_inflection = (
        2
        * beta
        * dissociation_energy
        * np.exp(-beta * (r_inflection - r_0))
        * (1 - np.exp(-beta * (r_inflection - r_0)))
    )
    fs_mask = rs > r_inflection
    fs[fs_mask] = f_inflection

    # calculate extrema of shifted potential i.e. get barrier height of V_eff = V_morse - F*X
    r_min = r_0 - 1 / beta * np.log(
        (
            beta * dissociation_energy
            + np.sqrt(
                (beta**2 * dissociation_energy**2 - 2 * dissociation_energy * beta * fs)
                + 1e-7  # prevent rounding issue close to zero
            )
        )
        / (2 * beta * dissociation_energy)
    )
    r_max = r_0 - 1 / beta * np.log(
        (
            beta * dissociation_energy
            - np.sqrt(
                (beta**2 * dissociation_energy**2 - 2 * dissociation_energy * beta * fs)
                + 1e-7  # prevent rounding issue close to zero
            )
        )
        / (2 * beta * dissociation_energy)
    )
    r_max = np.where(
        ~np.isfinite(r_max), 10 * r_0, r_max
    )  # set rmax to r0 * 10 where no rmax can be found

    v_max = dissociation_energy * (1 - np.exp(-beta * (r_max - r_0))) ** 2 - fs * (
        r_max - r_0
    )
    v_min = dissociation_energy * (1 - np.exp(-beta * (r_min - r_0))) ** 2 - fs * (
        r_min - r_0
    )
    # Note: F*r should lead to same result as F*(r-r_0) since the shifts in Vmax-Vmin adds up to zero
    delta_v = v_max - v_min

    # calculate reaction rate constant from barrier heigth
    R = 8.31446261815324e-3  # [kJ K-1 mol-1]
    k = frequency_factor * np.exp(-delta_v / (R * temperature))  # [1/ps]

    return k, fs


### GROMACS related functions ###


def get_gmx_dir(gromacs_alias: str = "gmx") -> Optional[Path]:
    """Returns the path to the gromacs installation"""

    # get the stder from calling `gmx` to search for the `Data prefix:`
    # line which contains the path to the gromacs installation
    try:
        r = sp.run([gromacs_alias], check=False, capture_output=True, text=True)
    except FileNotFoundError:
        logger.warning("GROMACS not found.")
        return None

    from_source = False
    gmx_prefix = None
    for l in r.stderr.splitlines():
        if l.startswith("Data prefix:"):
            gmx_prefix = Path(l.split()[2])
            if "(source tree)" in l:
                from_source = True
            break

    if gmx_prefix is None:
        logger.warning("GROMACS data directory not found in gromacs message.")
        return None

    if from_source:
        return Path(gmx_prefix) / "share"
    else:
        return Path(gmx_prefix) / "share" / "gromacs"


def check_gmx_version(config):
    """Check for an existing gromacs installation.

    If PLUMED is meant to be used it additionally checks for the keyword
    'MODIFIED' or 'plumed' in the version name.
    """
    # check for existing installation and get version
    try:
        version = [
            l
            for l in get_shell_stdout(
                f"{config.gromacs_alias} --quiet --version"
            ).split("\n")
            if "GROMACS version:" in l
        ][0]
    except Exception as e:
        m = "No system gromacs detected. With error: " + str(e)
        logger.error(m)
        raise SystemError(m)
    # check version for plumed patch if necessary
    if hasattr(config, "mds"):
        for md in config.mds.get_attributes():
            if config.mds.attr(md).use_plumed:
                if not ("MODIFIED" in version or "plumed" in version):
                    m = (
                        "GROMACS version does not contain 'MODIFIED' or "
                        "'plumed', aborting due to apparent lack of PLUMED patch."
                    )
                    logger.error(m)
                    logger.error("Version was: " + version)
                    if not config.dryrun:
                        raise SystemError(m)
    return version


def truncate_sim_files(
    files: TaskFiles,
    time: Optional[float],
    keep_tail: bool = True,
):
    """Truncates latest trr, xtc, edr, and gro to the time to a previous
    point in time.

    The files stay in place, the truncated tail is by default kept and renamed
    to '[...xtc].tail'

    Parameters
    ----------
    time
        Time in ps up to which the data should be truncated.
    files
        TaskFiles to get the latest files.
    """

    if time is None:
        logger.debug("time is None, nothing to truncate")
        return

    paths = {}
    paths["gro"] = files.input["gro"]
    for s in ["trr", "xtc", "edr"]:
        try:
            paths[s] = files.input[s]
        except FileNotFoundError:
            paths[s] = None

    trjs = [p for p in [paths["trr"], paths["xtc"]] if p is not None and p.exists()]

    # trr or xtc must be present
    if len(trjs) == 0:
        logger.info("No trajectory files found, nothing to truncate.")
        return

    for trj in trjs:
        # check time exists in traj
        p = sp.run(
            f"gmx -quiet -nocopyright check -f {trj}",
            text=True,
            capture_output=True,
            shell=True,
        )
        # FOR SOME REASON gmx check writes in stderr instead of stdout
        if ms := re.findall(r"Reading frame\s.*time\s+(\d+\.\d+)", p.stderr):
            if len(ms) == 0:
                m = f"Could not find time in trajectory {trj} with gmx check."
                logger.warning(m)
                return
            last_time = float(ms[-1])
            if last_time == 0.0:
                logger.info(
                    "Last traj contains single frame, will not truncate anything."
                )
                return
            if last_time * 1.01 >= time:
                m = f"Requested to truncate trajectory at time {time} but last frame according to gmx check is at {last_time:.4} ps. This might led to unexpected results."
                logger.warning(m)
        else:
            m = f"gmx check failed:\n{p.stdout}\n{p.stderr}.\nMay not be able to truncate trajectory {trj}."
            logger.error(m)
            raise RuntimeError(m)
        logger.info(
            f"Truncating trajectories to {time:.4} ps. Trajectory time was {last_time:.4} ps"
        )

    # backup the tails of trajectories
    for trj in trjs:
        tmp = trj.rename(trj.with_name("tmp_backup_" + trj.name))
        if keep_tail:
            run_gmx(
                f"gmx trjconv -f {tmp} -b {time} -o {trj}",
            )
            trj.rename(str(trj) + ".tail")

        run_gmx(f"gmx trjconv -f {tmp} -e {time} -o {trj}")
        tmp.unlink()

    # backup the gro
    bck_gro = paths["gro"].rename(
        paths["gro"].with_name("tmp_backup_" + paths["gro"].name)
    )
    sp.run(
        f"gmx trjconv -f {trjs[0]} -s {bck_gro} -dump -1 -o {paths['gro']}",
        text=True,
        input="0",
        shell=True,
    )
    bck_gro.rename(str(paths["gro"]) + ".tail")
    if not keep_tail:
        bck_gro.unlink()

    # backup the edr
    if paths["edr"] is not None:
        bck_edr = paths["edr"].rename(
            paths["edr"].with_name("tmp_backup_" + paths["edr"].name)
        )
        run_shell_cmd(f"gmx eneconv -f {bck_edr} -e {time} -o {paths['edr']}")
        bck_edr.rename(str(paths["edr"]) + ".tail")
        if not keep_tail:
            bck_edr.unlink()
    return


def get_task_directories(dir: Path, tasks: Union[list[str], str] = "all") -> list[Path]:
    """
    create list of subdirectories that match the tasks.
    If tasks is "all", all subdirectories are returned.

    Parameters
    ----------
    dir
        Directory to search for subdirectories
    tasks
        List of steps e.g. ["equilibrium", "production"]. Or a string "all" to return all subdirectories
    """
    directories = sorted(
        [p for p in dir.glob("*_*/") if p.is_dir()],
        key=lambda p: int(p.name.split("_")[0]),
    )
    if tasks == "all":
        matching_directories = directories
    else:
        matching_directories = list(
            filter(lambda d: d.name.split("_")[1] in tasks, directories)
        )

    if not matching_directories:
        print(f"WARNING: Could not find directories {tasks} in {dir}.")

    return matching_directories

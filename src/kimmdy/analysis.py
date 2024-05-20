"""Analysis tools for KIMMDY runs.
For command line usage, run `kimmdy-analysis -h`.
"""

import argparse
import json
import re
import subprocess as sp
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Union

import matplotlib as mpl
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import MDAnalysis as mda
import numpy as np
import pandas as pd
import seaborn as sns
import seaborn.objects as so
from seaborn import axes_style

from kimmdy.constants import MARK_DONE, MARK_STARTED
from kimmdy.parsing import read_json, read_time_marker, write_json
from kimmdy.recipe import Bind, Break, Place, RecipeCollection
from kimmdy.utils import get_task_directories, run_shell_cmd


def get_analysis_dir(dir: Path) -> Path:
    """Get analysis directory for a KIMMDY run.

    Creates the directory if it does not exist.

    Parameters
    ----------
    dir
        Directory of KIMMDY run

    Returns
    -------
        Path to analysis directory
    """
    out = dir / "analysis"
    out.mkdir(exist_ok=True)
    return out


def concat_traj(
    dir: str,
    filetype: str,
    steps: Union[list[str], str],
    open_vmd: bool = False,
    output_group: Optional[str] = None,
):
    """Find and concatenate trajectories (.xtc files) from a KIMMDY run into one trajectory.
    The concatenated trajectory is centered and pbc corrected.

    Parameters
    ----------
    dir
        Directory to search for subdirectories
    steps
        List of steps e.g. ["equilibrium", "production"]. Or a string "all" to return all subdirectories
    open_vmd
        Open concatenated trajectory in VMD
    output_group
        index group for output. Default is "Protein" for xtc and "System" for trr.
    """
    run_dir = Path(dir).expanduser().resolve()
    analysis_dir = get_analysis_dir(run_dir)

    directories = get_task_directories(run_dir, steps)
    if not directories:
        raise ValueError(
            f"Could not find directories {steps} in {dir}. Thus, no trajectories can be concatenated"
        )

    out_xtc = analysis_dir / "concat.xtc"
    out_gro = analysis_dir / "concat.gro"

    if not any([filetype in ["xtc", "trr"]]):
        raise NotImplementedError(
            f"Filetype {filetype} not implemented as trajectory file type. Try 'xtc', 'trr'."
        )

    filetype_ouput_group_default = {"xtc": "Protein", "trr": "System"}
    if output_group is None:
        output = filetype_ouput_group_default[filetype]
    else:
        output = output_group

    ## gather trajectories
    trajectories = []
    tprs = []
    gros = []
    for d in directories:
        trajectories.extend(d.glob(f"*.{filetype}"))
        tprs.extend(d.glob("*.tpr"))
        gros.extend(d.glob("*.gro"))

    assert (
        len(trajectories) > 0
    ), f"No trrs found to concatenate in {run_dir} with subdirectory names {steps}"

    trajectories = [str(t) for t in trajectories]

    ## write concatenated trajectory
    tmp_xtc = str(out_xtc.with_name("tmp.xtc"))
    run_shell_cmd(
        f"gmx trjcat -f {' '.join(trajectories)} -o {tmp_xtc} -cat",
        cwd=run_dir,
    )
    run_shell_cmd(
        f"echo 'Protein\n{output}' | gmx trjconv -dt 0 -f {tmp_xtc} -s {tprs[0]} -o {str(out_xtc)} -center -pbc mol",
        cwd=run_dir,
    )
    run_shell_cmd(
        f"echo 'Protein\n{output}' | gmx trjconv -dump 0 -f {tmp_xtc} -s {tprs[0]} -o {str(out_gro)} -center -pbc mol",
        cwd=run_dir,
    )
    run_shell_cmd(f"rm {tmp_xtc}", cwd=run_dir)
    if open_vmd:
        run_shell_cmd(f"vmd {str(out_gro)} {str(out_xtc)}", cwd=run_dir)


def plot_energy(
    dir: str, steps: Union[list[str], str], terms: list[str], open_plot: bool = False
):
    """Plot GROMACS energy for a KIMMDY run.

    Parameters
    ----------
    dir
        Directory to search for subdirectories
    steps
        List of steps e.g. ["equilibrium", "production"]. Or a string "all" to return all subdirectories.
        Default is "all".
    terms
        Terms from gmx energy that will be plotted. Uses 'Potential' by default.
    open_plot :
        Open plot in default system viewer.
    """
    run_dir = Path(dir).expanduser().resolve()
    xvg_entries = ["time"] + terms
    terms_str = "\n".join(terms)

    subdirs_matched = get_task_directories(run_dir, steps)

    analysis_dir = get_analysis_dir(run_dir)
    xvgs_dir = analysis_dir / "energy_xvgs"
    xvgs_dir.mkdir(exist_ok=True)

    ## gather energy files
    edrs = []
    for d in subdirs_matched:
        edrs.extend(d.glob("*.edr"))
    assert (
        len(edrs) > 0
    ), f"No GROMACS energy files in {run_dir} with subdirectory names {steps}"

    energy = defaultdict(list)

    time_offset = 0
    for i, edr in enumerate(edrs):
        ## write energy .xvg file
        xvg = str(xvgs_dir / edr.parents[0].with_suffix(".xvg").name)
        step_name = edr.parents[0].name.split("_")[1]

        run_shell_cmd(
            f"echo '{terms_str} \n\n' | gmx energy -f {str(edr)} -o {xvg}",
            cwd=run_dir,
        )

        ## read energy .xvg file
        with open(xvg, "r") as f:
            for line in f:
                if line[0] not in ["@", "#"]:
                    energy["step"].append(step_name)
                    energy["step_ix"].append(i)
                    for k, v in zip(xvg_entries, line.split()):
                        if k == "time":
                            energy[k].append(float(v) + time_offset)
                        else:
                            energy[k].append(float(v))
                # get correct order of xvg_entries, not as in cmd!
                elif match := re.match(r"@ s(\d+) legend \"(.*)\"", line):
                    xvg_entries[int(match.group(1)) + 1] = match.group(2)

        time_offset = energy["time"][-1]

    # resolve eventual term numbers to strings
    terms = xvg_entries[1:]

    df = pd.DataFrame(energy).melt(
        id_vars=["time", "step", "step_ix"], value_vars=terms
    )
    step_names = df[df["variable"] == terms[0]]
    step_names.loc[:, "variable"] = "Step"
    step_names.loc[:, "value"] = step_names["step_ix"]
    df = pd.concat([df, step_names], ignore_index=True)

    p = (
        so.Plot(df, x="time", y="value")
        .add(so.Line())
        .facet(row="variable")
        .share(x=True, y=False)
        .theme({**axes_style("white")})
        .label(x="Time [ps]", y="Energy [kJ/mol]")
    )
    p.plot(pyplot=True)

    step_names = step_names.groupby(["step", "step_ix"]).first().reset_index()
    for t, v, s in zip(step_names["time"], step_names["value"], step_names["step"]):
        plt.axvline(x=t, color="black", linestyle="--")
        plt.text(x=t, y=v + 0.5, s=s, fontsize=6)

    ax = plt.gca()
    steps_y_axis = [c for c in ax.get_children() if isinstance(c, mpl.axis.YAxis)][0]
    steps_y_axis.set_visible(False)
    output_path = str(run_dir / "analysis" / "energy.png")
    plt.savefig(output_path, dpi=300)

    if open_plot:
        sp.call(("xdg-open", output_path))


def radical_population(
    dir: str,
    population_type: str = "frequency",
    steps: Union[list[str], str] = "all",
    select_atoms: str = "protein",
    open_plot: bool = False,
    open_vmd: bool = False,
):
    """Plot population of radicals for a KIMMDY run.

    Parameters
    ----------
    dir
        KIMMDY run directory to be analysed.
    population_type
        How to calculate the fractional radical occupancy. Available are 'frequency' and 'time'
    steps
        List of steps e.g. ["equilibrium", "production"]. Or a string "all" to return all subdirectories.
        Default is "all".
    select_atoms
        Atoms chosen for radical population analysis, default is protein (uses MDAnalysis selection syntax)
    open_plot
        Open plot in default system viewer.
    open_vmd
        Open a pdb in VMD with the radical occupation as B-factors.
    """
    run_dir = Path(dir).expanduser().resolve()
    analysis_dir = get_analysis_dir(run_dir)

    subdirs_matched = get_task_directories(run_dir, steps)
    radical_jsons = list(run_dir.glob("**/radicals.json"))
    radical_jsons.sort(
        key=lambda x: int(x.parents[0].name.split("_")[0])
    )  # sort by task number

    radical_info = {"overall_time": [], "residence_time": [], "radicals": []}
    for radical_json in radical_jsons:
        data = read_json(radical_json)
        for k in radical_info.keys():
            radical_info[k].append(data[k])

    write_json(radical_info, analysis_dir / "radical_population.json")

    ## get atoms from gro file
    gro = None
    for subdir in subdirs_matched:
        gro = list(subdir.glob("*.gro"))
        if gro:
            break
    if not gro:
        raise ValueError(f"No .gro file found in {run_dir}")

    u = mda.Universe(str(gro[0]), format="gro")
    atoms = u.select_atoms(select_atoms)
    atoms_identifier = [
        "-".join(str(x) for x in [a.resid, a.resname, a.name]) for a in atoms
    ]
    atom_ids = atoms.ids
    # print(f"Found {len(atom_ids)} atom ids: {atom_ids}")

    ## plot fingerprint
    occupancy = {i: 0.0 for i in atom_ids}
    total_time = float(radical_info["overall_time"][-1])
    if population_type == "frequency":
        print(
            "Will determine occupancy by how often an atom was the educt radical of a reaction."
        )
        n_states = len(radical_info["radicals"])
        print(f"Found {n_states} states!")
        for state in range(n_states):
            for idx in radical_info["radicals"][state]:
                if int(idx) in occupancy.keys():
                    occupancy[int(idx)] += 1 / n_states
    elif population_type == "time":
        print("Will determine occupancy by time of residence for a radical state.")
        for i, radicals in enumerate(radical_info["radicals"]):
            for radical in radicals:
                occupancy[int(radical)] += (
                    float(radical_info["residence_time"][i]) / total_time
                )
    else:
        raise NotImplementedError(
            f"Selected radical occupancy calculation type: {population_type}."
        )

    # filter out atoms with zero occupancy
    print(f"Occupancy: {occupancy}.")
    occupied_counts = {
        atoms_identifier[k - 1]: v for k, v in occupancy.items() if v > 0
    }

    sns.barplot(
        x=list(occupied_counts.keys()), y=list(occupied_counts.values()), errorbar=None
    )

    plt.xlabel("Atom identifier")
    plt.ylabel("Fractional Radical Occupancy")
    output_path = str(analysis_dir / "radical_population_fingerprint.png")
    plt.savefig(output_path, dpi=300)

    u.add_TopologyAttr("tempfactors")
    atoms = u.select_atoms(select_atoms)
    atoms.tempfactors = list(occupancy.values())
    pdb_output = f"{analysis_dir}/radical_population_selection.pdb"
    atoms.write(pdb_output)
    try:
        protein = u.select_atoms("protein")
        pdb_output_protein = f"{analysis_dir}/radical_population.pdb"
        protein.write(pdb_output_protein)
    except IndexError:
        print("Problem with writing protein pdb. Continuing.")

    if open_plot:
        sp.call(("xdg-open", output_path))

    if open_vmd:
        run_shell_cmd(f"vmd {pdb_output}", cwd=analysis_dir)


def radical_migration(
    dirs: list[str],
    type: str = "qualitative",
    cutoff: int = 1,
):
    """Plot population of radicals for a KIMMDY run.

    Parameters
    ----------
    dirs
        KIMMDY run directories to be analysed.
    type
        How to analyse radical migration. Available are 'qualitative','occurence' and 'min_rate'",
    cutoff
        Ignore migration between two atoms if it happened less often than the specified value.

    """
    print(
        "Running radical migration analysis\n"
        f"dirs: \t\t{dirs}\n"
        f"type: \t\t{type}\n"
        f"cutoff: \t{cutoff}\n\n"
        f"Writing analysis files in {dirs[0]}"
    )

    migrations = []
    analysis_dir = get_analysis_dir(Path(dirs[0]))
    for d in dirs:
        run_dir = Path(d).expanduser().resolve()

        picked_recipes = {}
        for recipes in run_dir.glob("*decide_recipe/recipes.csv"):
            task_nr = int(recipes.parents[0].stem.split(sep="_")[0])
            _, picked_recipe = RecipeCollection.from_csv(recipes)
            picked_recipes[task_nr] = picked_recipe
        sorted_recipes = [v for _, v in sorted(picked_recipes.items())]

        for sorted_recipe in sorted_recipes:
            connectivity_difference = {}
            for step in sorted_recipe.recipe_steps:
                if isinstance(step, Break):
                    for atom_id in [step.atom_id_1, step.atom_id_2]:
                        if atom_id in connectivity_difference.keys():
                            connectivity_difference[atom_id] += -1
                        else:
                            connectivity_difference[atom_id] = -1
                elif isinstance(step, Bind):
                    for atom_id in [step.atom_id_1, step.atom_id_2]:
                        if atom_id in connectivity_difference.keys():
                            connectivity_difference[atom_id] += 1
                        else:
                            connectivity_difference[atom_id] = 1

            from_atom = [
                key for key, value in connectivity_difference.items() if value == 1
            ]
            to_atom = [
                key for key, value in connectivity_difference.items() if value == -1
            ]
            if len(from_atom) == 1 and len(to_atom) == 1:
                migrations.append([from_atom[0], to_atom[0], max(sorted_recipe.rates)])

    # get unique migrations
    unique_migrations = {}
    for migration in migrations:
        key = "_".join(migration[:2])
        if key not in unique_migrations.keys():
            unique_migrations[key] = {"count": 0, "max_rate": 1e-70}
        unique_migrations[key]["count"] += 1
        if migration[2] > unique_migrations[key]["max_rate"]:
            unique_migrations[key]["max_rate"] = migration[2]

    # filter by cutoff

    # write json
    out_path = analysis_dir / "radical_migration.json"
    with open(out_path, "w") as json_file:
        json.dump(unique_migrations, json_file)
    print("Done!")


def plot_rates(dir: str):
    """Plot rates of all possible reactions for each 'decide_recipe' step.

    Parameters
    ----------
    dir
        Directory of KIMMDY run
    """
    run_dir = Path(dir).expanduser().resolve()
    analysis_dir = get_analysis_dir(run_dir)

    for recipes in run_dir.glob("*decide_recipe/recipes.csv"):
        rc, picked_rp = RecipeCollection.from_csv(recipes)
        i = recipes.parent.name.split("_")[0]
        rc.plot(analysis_dir / f"{i}_reaction_rates.svg", highlight_r=picked_rp)


def reaction_participation(dir: str, open_plot: bool = False):
    """Plot which atoms participate in reactions.

    Parameters
    ----------
    dir
        Directory of KIMMDY run
    open_plot
        Open plot in default system viewer.
    """
    print(
        "Count reaction participation\n"
        f"dir: \t\t\t{dir}\n"
        f"open_plot: \t\t{open_plot}\n"
    )

    run_dir = Path(dir).expanduser().resolve()
    analysis_dir = get_analysis_dir(run_dir)

    reaction_count = {"overall": 0}
    for recipes in run_dir.glob("*decide_recipe/recipes.csv"):
        # get picked recipe
        _, picked_rp = RecipeCollection.from_csv(recipes)
        if not picked_rp:
            continue
        # get involved atoms
        reaction_atom_ids = set()
        for step in picked_rp.recipe_steps:
            if isinstance(step, Break) or isinstance(step, Bind):
                reaction_atom_ids |= set([step.atom_id_1, step.atom_id_2])
            elif isinstance(step, Place):
                reaction_atom_ids |= set([step.id_to_place])
            else:
                continue
        # update count
        for atom_id in reaction_atom_ids:
            if atom_id in reaction_count:
                reaction_count[atom_id] += 1
            else:
                reaction_count[atom_id] = 1
        reaction_count["overall"] += 1

    reaction_count = dict(sorted(reaction_count.items()))
    sns.barplot(
        x=list(reaction_count.keys()), y=list(reaction_count.values()), errorbar=None
    )

    plt.xlabel("Atom identifier")
    plt.ylabel("Reaction participation count")
    output_path = str(analysis_dir / "reaction_participation_fingerprint.png")
    plt.savefig(output_path, dpi=300)
    if open_plot:
        sp.call(("xdg-open", output_path))


def runtime_analysis(dir: str, open_plot: bool = False):
    """Plot which atoms participate in reactions.

    Parameters
    ----------
    dir
        Directory of KIMMDY run
    open_plot
        Open plot in default system viewer.
    """

    run_dir = Path(dir).expanduser().resolve()
    analysis_dir = get_analysis_dir(run_dir)

    steps = []
    starts = []
    ends = []
    raw_dt = []
    events = []

    for marker in run_dir.rglob(MARK_STARTED):
        if not marker.with_name(MARK_DONE).exists():
            print(f"Step {marker.parent.name} did not finish!")
            continue
        e_started, t_started = read_time_marker(marker)
        e_done, t_done = read_time_marker(marker.with_name(MARK_DONE))

        assert sorted(e_started) == sorted(
            e_done
        ), f"Not all tasks have finished! Error in {marker.parent.name}"

        for event, time_s in zip(e_started, t_started):
            time_e = t_done[e_done.index(event)]
            steps.append(marker.parent.name)
            starts.append(time_s)
            ends.append(time_e)
            raw_dt.append(time_e - time_s)
            events.append(event)

    start_sort = np.argsort(starts)
    starts = np.array(starts)[start_sort]
    steps = np.array(steps)[start_sort]
    ends = np.array(ends)[start_sort]
    raw_dt = np.array(raw_dt)[start_sort]
    events = np.array(events)[start_sort]
    pure_dts = []
    pure_dts_min = []

    steps_names = list(map(lambda s: s.split("_")[1], steps))

    # remove time of nested tasks
    for i in range(len(steps)):
        dt = ends[i] - starts[i]
        crr_start = starts[i]

        for j in range(i + 1, len(steps)):
            # next element starts after crr_start
            if starts[j] > crr_start:
                # next element ends before this one
                if ends[j] < ends[i]:
                    dt -= raw_dt[j]
                    crr_start = ends[j]
        pure_dts.append(dt)
        pure_dts_min.append(dt.total_seconds() / 60)

    dt_all = ends.max() - starts.min()

    df = pd.DataFrame()
    df["Step"] = steps
    df["starts"] = starts
    df["ends"] = ends
    df["Stage"] = events
    df["Task"] = steps_names
    df["Durations"] = pure_dts_min
    df.loc[len(df)] = [
        "KIMMDY",
        starts.min(),
        ends.max(),
        "Overall",
        "Overall",
        dt_all.total_seconds() / 60,
    ]

    # get longest stage
    cum_times = defaultdict(timedelta)
    for e, dt in zip(events, pure_dts):
        cum_times[e] += dt

    longest_stage = ""
    longest_stage_dt = timedelta()
    for e, dt in cum_times.items():
        if dt > longest_stage_dt:
            longest_stage = e
            longest_stage_dt = dt

    # get longest task
    longest_task = steps[np.argmax(pure_dts)]
    longest_task_dt = max(pure_dts)

    # grouped by stage
    plt.figure()
    sns.histplot(
        data=df,
        y="Stage",
        weights="Durations",
        multiple="stack",
        hue="starts",
        legend=False,
        palette="viridis",
    )
    plt.xlabel("Minutes")
    plt.title("Runtime per stage")
    plt.tight_layout()

    output_path = str(analysis_dir / "runtime_per_stage.svg")
    plt.savefig(output_path, dpi=300)
    if open_plot:
        sp.Popen(("xdg-open", output_path))

    # grouped by task
    plt.figure()
    sns.histplot(
        data=df,
        y="Task",
        weights="Durations",
        multiple="stack",
        hue="starts",
        legend=False,
        palette="viridis",
    )
    plt.xlabel("Minutes")
    plt.title("Runtime per task")
    plt.tight_layout()

    output_path = str(analysis_dir / "runtime_per_task.svg")
    plt.savefig(output_path, dpi=300)
    if open_plot:
        sp.Popen(("xdg-open", output_path))

    # Not grouped
    plt.figure(figsize=(8, max(0.3 * len(steps), 4)))
    sns.histplot(
        data=df.loc[df.Step != "KIMMDY"],
        y="Step",
        weights="Durations",
        multiple="stack",
    )
    plt.title("Runtime per step")
    plt.xlabel("Minutes")
    plt.tight_layout()

    output_path = str(analysis_dir / "runtime_all.svg")
    plt.savefig(output_path, dpi=300)
    if open_plot:
        sp.Popen(("xdg-open", output_path))

    print(
        f"""
    Summary of KIMMDY run in \
    {"/".join(run_dir.parts[-2:])}

    Overall duration: {dt_all}
    Longest single step: {longest_task}
    Duration of longest step: {longest_task_dt}
    Longest stage, cumulative: {longest_stage}
    Longest stage time, cumulative: {longest_stage_dt}
    """
    )


def get_analysis_cmdline_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns
    -------
        Parsed command line arguments
    """
    parser = argparse.ArgumentParser(
        description="Welcome to the KIMMDY analysis module. Use this module to analyse existing KIMMDY runs.",
    )
    subparsers = parser.add_subparsers(metavar="module", dest="module")

    parser_trjcat = subparsers.add_parser(
        name="trjcat", help="Concatenate trajectories of a KIMMDY run"
    )
    parser_trjcat.add_argument(
        "dir", type=str, help="KIMMDY run directory to be analysed."
    )
    parser_trjcat.add_argument("--filetype", "-f", default="xtc")
    parser_trjcat.add_argument(
        "--steps",
        "-s",
        nargs="*",
        default="all",
        help=(
            "Apply analysis method to subdirectories with these names. Uses all subdirectories by default."
        ),
    )
    parser_trjcat.add_argument(
        "--open-vmd",
        action="store_true",
        help="Open VMD with the concatenated trajectory.",
    )
    parser_trjcat.add_argument(
        "--output-group",
        type=str,
        help="Index group to include in the output. Default is 'Protein' for xtc and 'System' for trr.",
    )

    parser_energy = subparsers.add_parser(
        name="energy", help="Plot GROMACS energy for a KIMMDY run"
    )
    parser_energy.add_argument(
        "dir", type=str, help="KIMMDY run directory to be analysed."
    )
    parser_energy.add_argument(
        "--steps",
        "-s",
        nargs="*",
        default="all",
        help=(
            "Apply analysis method to subdirectories with these names. Uses all subdirectories by default."
        ),
    )
    parser_energy.add_argument(
        "--terms",
        "-t",
        nargs="*",
        default=["Potential"],
        help=(
            "Terms from gmx energy that will be plotted. Uses 'Potential' by default."
        ),
    )
    parser_energy.add_argument(
        "--open-plot",
        "-p",
        action="store_true",
        help="Open plot in default system viewer.",
    )

    parser_radical_population = subparsers.add_parser(
        name="radical_population",
        help="Plot population of radicals for one or multiple KIMMDY run(s)",
    )
    parser_radical_population.add_argument(
        "dir", type=str, help="KIMMDY run directory to be analysed."
    )
    parser_radical_population.add_argument(
        "--population_type",
        "-t",
        type=str,
        help="How to calculate the fractional radical occupancy. Available are 'frequency' and 'time'",
        default="frequency",
    )
    parser_radical_population.add_argument(
        "--select_atoms",
        "-a",
        type=str,
        help="Atoms chosen for radical population analysis, default is protein (uses MDAnalysis selection syntax)",
        default="protein",
    )
    parser_radical_population.add_argument(
        "--steps",
        "-s",
        nargs="*",
        default="all",
        help=(
            "Apply analysis method to subdirectories with these names. Uses all subdirectories by default."
        ),
    )
    parser_radical_population.add_argument(
        "--open-plot",
        "-p",
        action="store_true",
        help="Open plot in default system viewer.",
    )
    parser_radical_population.add_argument(
        "--open-vmd",
        "-v",
        action="store_true",
        help="Open VMD with the concatenated trajectory."
        "To view the radical occupancy per atom, add a representation with the beta factor as color.",
    )
    parser_radical_migration = subparsers.add_parser(
        name="radical_migration",
        help="Create a json of radical migration events for further analysis.",
    )
    parser_radical_migration.add_argument(
        "dirs",
        type=str,
        help="One or multiple KIMMDY run directories to be analysed.",
        nargs="+",
    )
    parser_radical_migration.add_argument(
        "--type",
        "-t",
        type=str,
        help="How to analyse radical migration. Available are 'qualitative','occurence' and 'min_rate'",
        default="qualitative",
    )
    parser_radical_migration.add_argument(
        "--cutoff",
        "-c",
        type=int,
        help="Ignore migration between two atoms if it happened less often than the specified value.",
        default=1,
    )
    parser_rates = subparsers.add_parser(
        name="rates",
        help="Plot rates of all possible reactions after a MD run. Rates must have been saved!",
    )
    parser_rates.add_argument(
        "dir", type=str, help="KIMMDY run directory to be analysed."
    )

    parser_runtime = subparsers.add_parser(
        name="runtime",
        help="Plot runtime of the tasks of a kimmdy run.",
    )
    parser_runtime.add_argument(
        "dir", type=str, help="KIMMDY run directory to be analysed."
    )
    parser_runtime.add_argument(
        "--open-plot",
        "-p",
        action="store_true",
        help="Open plot in default system viewer.",
    )
    parser_reaction_participation = subparsers.add_parser(
        name="reaction_participation",
        help="Plot counts of reaction participation per atom id",
    )
    parser_reaction_participation.add_argument(
        "dir", type=str, help="KIMMDY run directory to be analysed."
    )
    parser_reaction_participation.add_argument(
        "--open-plot",
        "-p",
        action="store_true",
        help="Open plot in default system viewer.",
    )
    return parser.parse_args()


def entry_point_analysis():
    """Analyse existing KIMMDY runs."""
    args = get_analysis_cmdline_args()

    if args.module == "trjcat":
        concat_traj(
            args.dir, args.filetype, args.steps, args.open_vmd, args.output_group
        )
    elif args.module == "energy":
        plot_energy(args.dir, args.steps, args.terms, args.open_plot)
    elif args.module == "radical_population":
        radical_population(
            args.dir,
            args.population_type,
            args.steps,
            args.select_atoms,
            args.open_plot,
            args.open_vmd,
        )
    elif args.module == "radical_migration":
        radical_migration(args.dirs, args.type, args.cutoff)
    elif args.module == "rates":
        plot_rates(args.dir)
    elif args.module == "runtime":
        runtime_analysis(args.dir, args.open_plot)
    elif args.module == "reaction_participation":
        reaction_participation(
            args.dir,
            args.open_plot,
        )
    else:
        print(
            "No analysis module specified. Use -h for help and a list of available modules."
        )

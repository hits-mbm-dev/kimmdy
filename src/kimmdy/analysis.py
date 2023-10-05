"""Analysis tools for KIMMDY runs.
For command line usage, run `kimmdy-analysis -h`.
"""
from typing import Union
from pathlib import Path
import MDAnalysis as mda
import subprocess as sp
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib as mpl
import seaborn.objects as so
import argparse
from seaborn import axes_style
import pandas as pd
from datetime import datetime

from kimmdy.utils import run_shell_cmd
from kimmdy.parsing import read_json, write_json
from kimmdy.recipe import RecipeCollection


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


def get_step_directories(dir: Path, steps: Union[list[str], str] = "all") -> list[Path]:
    """
    create list of subdirectories that match the steps.
    If steps is "all", all subdirectories are returned.

    Parameters
    ----------
    dir
        Directory to search for subdirectories
    steps
        List of steps e.g. ["equilibrium", "production"]. Or a string "all" to return all subdirectories
    """
    directories = sorted(
        [p for p in dir.glob("*_*/") if p.is_dir()],
        key=lambda p: int(p.name.split("_")[0]),
    )
    if steps == "all":
        matching_directories = directories
    else:
        matching_directories = list(
            filter(lambda d: d.name.split("_")[1] in steps, directories)
        )

    if not matching_directories:
        raise ValueError(
            f"Could not find directories {steps} in {dir}. Thus, no trajectories can be concatenated"
        )

    return matching_directories


def concat_traj(dir: str, steps: Union[list[str], str], open_vmd: bool = False):
    """Find and concatenate trajectories (.xtc or .trr files) from a KIMMDY run into one trajectory.
    The concatenated trajectory is centered and pbc corrected.

    Parameters
    ----------
    dir
        Directory to search for subdirectories
    steps
        List of steps e.g. ["equilibrium", "production"]. Or a string "all" to return all subdirectories
    open_vmd
        Open concatenated trajectory in VMD
    """
    run_dir = Path(dir).expanduser().resolve()
    analysis_dir = get_analysis_dir(run_dir)

    directories = get_step_directories(run_dir, steps)

    out_xtc = analysis_dir / "concat.xtc"

    ## gather trajectories
    trajectories = []
    tprs = []
    gros = []
    for d in directories:
        trajectories.extend(d.glob("*.xtc"))
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
        f"echo '1 1' | gmx trjconv -f {tmp_xtc} -s {tprs[0]} -o {str(out_xtc)} -center -pbc mol",
        cwd=run_dir,
    )
    # has to be in two steps
    run_shell_cmd(
        f"echo '1 1' | gmx trjconv -f {str(out_xtc)} -s {tprs[0]} -o {str(out_xtc)} -fit rot+trans",
        cwd=run_dir,
    )
    run_shell_cmd(f"rm {tmp_xtc}", cwd=run_dir)
    if open_vmd:
        gro = str(gros[0])
        run_shell_cmd(f"cp {gro} {str(analysis_dir)}", cwd=run_dir)
        run_shell_cmd(f"vmd {gro} {str(out_xtc)}", cwd=run_dir)


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

    subdirs_matched = get_step_directories(run_dir, steps)

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

    energy = {}
    for k in xvg_entries:
        energy[k] = []

    energy["step"] = []
    energy["step_ix"] = []

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

        time_offset = energy["time"][-1]

    df = pd.DataFrame(energy).melt(
        id_vars=["time", "step", "step_ix"], value_vars=terms
    )
    step_names = df[df["variable"] == terms[0]]
    step_names["variable"] = "Step"
    step_names["value"] = step_names["step_ix"]
    df = pd.concat([df, step_names], ignore_index=True)

    p = (
        so.Plot(df, x="time", y="value")
        .add(so.Line())
        .facet(row="variable")
        .share(x=True, y=False)
        .theme({**axes_style("white")})
        .label(x="Time [ps]", y="E [kJ/mol]")
    )
    p.plot(pyplot=True)

    step_names = step_names.groupby(["step", "step_ix"]).first().reset_index()
    for t, v, s in zip(step_names["time"], step_names["value"], step_names["step"]):
        plt.axvline(x=t, color="black", linestyle="--")
        plt.text(x=t + 2, y=v + 0.5, s=s, fontsize=6)

    ax = plt.gca()
    steps_y_axis = [c for c in ax.get_children() if isinstance(c, mpl.axis.YAxis)][0]
    steps_y_axis.set_visible(False)
    output_path = str(run_dir / "analysis" / "energy.png")
    plt.savefig(output_path, dpi=300)

    if open_plot:
        sp.call(("xdg-open", output_path))


def radical_population(
    dir: str,
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

    subdirs_matched = get_step_directories(run_dir, steps)
    radical_jsons = run_dir.glob("**/radicals.json")

    radical_info = {"time": [], "radicals": []}
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
        "-".join(x)
        for x in list(
            zip(
                [str(resid) for resid in atoms.resids],
                [str(name) for name in atoms.names],
            )
        )
    ]
    atom_ids = atoms.ids

    ## plot fingerprint
    counts = {i: 0.0 for i in atom_ids}
    n_states = len(radical_info["time"])
    for state in range(n_states):
        for idx in radical_info["radicals"][state]:
            if int(idx) in counts.keys():
                counts[int(idx)] += 1 / n_states

    plt.bar(x=atoms_identifier, height=counts.values())
    plt.xlabel("Atom identifier")
    plt.ylabel("Fractional Radical Occupancy")
    plt.ylim(0, 1)
    plt.xticks(atoms_identifier, rotation=90, ha="right")
    plt.tight_layout()
    output_path = str(analysis_dir / "radical_population_fingerprint.png")
    plt.savefig(output_path, dpi=300)

    u.add_TopologyAttr("tempfactors")
    atoms = u.select_atoms(select_atoms)
    atoms.tempfactors = list(counts.values())
    protein = u.select_atoms("protein")
    pdb_output = f"{analysis_dir}/radical_population.pdb"
    protein.write(pdb_output)

    if open_plot:
        sp.call(("xdg-open", output_path))

    if open_vmd:
        run_shell_cmd(f"vmd {pdb_output}", cwd=analysis_dir)


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


def plot_runtime(dir: str, md_tasks: list, datefmt: str, open_plot: bool = False):
    """Plot runtime of all tasks.

    Parameters
    ----------
    dir
        Directory of KIMMDY run
    md_tasks
        Names of MD tasks to color
    datefmt
        Date format in the KIMMDY logfile
    open_plot
        Open plot in default system viewer.
    """

    def time_from_logfile(log_path: Path, sep: int, factor: float = 1.0):
        with open(log_path, "r") as f:
            log = f.readlines()
        starttime = datetime.strptime(" ".join(log[0].split()[:sep]), datefmt)
        endtime = datetime.strptime(" ".join(log[-1].split()[:sep]), datefmt)
        return (endtime - starttime).total_seconds() * factor

    run_dir = Path(dir).expanduser().resolve()
    analysis_dir = get_analysis_dir(run_dir)
    n_datefmt_substrings = len(datefmt.split())

    run_log = next(run_dir.glob("*.log"))
    walltime = time_from_logfile(run_log, n_datefmt_substrings)
    # sensitive to changes in runmanager logging
    open_task = False
    is_nested = False
    task_is_nested = []
    count = 0
    with open(run_log, "r") as f:
        foo = f.readlines()
    for i, line in enumerate(foo):
        # if "Starting task: " in line:
        if "Start " in line:
            if "Start run" in line:
                continue
            count += 1
            # if any(
            #     x in line
            #     for x in [
            #         "Starting task: _place_reaction_tasks",
            #         "Starting task: _decide_recipe",
            #     ]
            # ):
            #     continue
            if open_task:
                is_nested = True
            open_task = True
        elif " Done with " in line:
            if is_nested:
                task_is_nested.append(True)
                is_nested = False
            else:
                task_is_nested.append(False)
            open_task = False

    # set scale of plot to hour, minute or second
    if walltime < 120:
        t_factor = 1.0
        t_unit = "s"
    elif walltime < 7200:
        t_factor = 1 / 60
        t_unit = "min"
    else:
        t_factor = 1 / 3600
        t_unit = "h"
    walltime = walltime * t_factor

    tasks = []
    runtimes = []
    # sort by task number which is the number before _ in the logfile name
    print([x.name for x in run_dir.glob("*_*/*_*.log")])
    for log_path in sorted(
        run_dir.glob("*_*/*_*.log"), key=lambda x: int(x.name.split(sep="_")[0])
    ):
        tasks.append(log_path.stem)
        runtimes.append(time_from_logfile(log_path, n_datefmt_substrings, t_factor))
    # remove duration of nested task for mother task
    print(len(runtimes), len(task_is_nested))
    print(runtimes)
    for i, is_nested in enumerate(task_is_nested):
        if is_nested:
            runtimes[i] -= runtimes[i + 1]
    print(tasks)
    print(task_is_nested)
    overhead = walltime - sum(runtimes)
    # sns muted palette
    c_palette = [
        "#4878d0",
        "#ee854a",
        "#6acc64",
        "#d65f5f",
        "#956cb4",
        "#8c613c",
        "#dc7ec0",
        "#797979",
        "#d5bb67",
        "#82c6e2",
    ]
    c = [
        c_palette[0] if x.split(sep="_")[1] in md_tasks else c_palette[1] for x in tasks
    ]

    l1 = mpatches.Patch(color="#4878d0", label="MD task")
    l2 = mpatches.Patch(color="#ee854a", label="Reaction task")
    l3 = mpatches.Patch(color="#d65f5f", label="Overhead")
    plt.legend(handles=[l1, l2, l3])

    plt.barh(tasks[::-1], runtimes[::-1], color=c[::-1])
    plt.barh("KIMMDY overhead", overhead, color=c_palette[3])
    plt.xlabel(f"Time [{t_unit}]")
    plt.title(f"Runtime of {run_dir.name}; overall {walltime:.2f} {t_unit}")
    plt.tight_layout()

    output_path = analysis_dir / "runtime.png"
    plt.savefig(output_path, dpi=300)

    print(f"Finished analyzing runtime of {run_dir}.")

    if open_plot:
        sp.call(("xdg-open", output_path))


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
        "--md-tasks",
        nargs="*",
        default=["equilibrium", "pull", "relax", "prod"],
        help="Names of MD tasks of the specified KIMMDY run",
    )
    parser_runtime.add_argument(
        "--datefmt",
        type=str,
        default="%d-%m-%y %H:%M:%S",
        help="Date format in the KIMMDY logfile.",
    )
    parser_runtime.add_argument(
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
        concat_traj(args.dir, args.steps, args.open_vmd)
    elif args.module == "energy":
        plot_energy(args.dir, args.steps, args.terms, args.open_plot)
    elif args.module == "radical_population":
        radical_population(
            args.dir, args.steps, args.select_atoms, args.open_plot, args.open_vmd
        )
    elif args.module == "rates":
        plot_rates(args.dir)
    elif args.module == "runtime":
        plot_runtime(args.dir, args.md_tasks, args.datefmt, args.open_plot)
    else:
        print(
            "No analysis module specified. Use -h for help and a list of available modules."
        )

from typing import Union
from pathlib import Path
import subprocess
import argparse
from math import isclose
import matplotlib.pyplot as plt

from kimmdy.utils import run_shell_cmd


def get_subdirs(run_dir: Path, steps: Union[list, str]):
    ## create list of subdirectories of run_dir that match the ones named in steps
    subdirs_sorted = sorted(
        list(filter(lambda d: d.is_dir(), run_dir.glob("*_*/"))),
        key=lambda p: int(p.name.split("_")[0]),
    )
    if steps == "all":
        steps = list(set([x.name.split("_")[1] for x in subdirs_sorted]))
    subdirs_matched = list(
        filter(lambda d: d.name.split("_")[1] in steps, subdirs_sorted)
    )

    if not subdirs_matched:
        raise ValueError(
            f"Could not find directories {steps} in {run_dir}. Thus, no trajectories can be concatenated"
        )

    return subdirs_matched


def concat_traj(args: argparse.Namespace):
    """Find and concatenate trajectories (.xtc files) from KIMMDY runs."""
    run_dir = Path(args.dir).expanduser().resolve()
    steps: Union[list, str] = args.steps

    ## check if step argument is valid
    if not isinstance(steps, list):
        if not steps in ["all"]:
            raise ValueError(f"Steps argument {steps} can not be dealt with.")

    subdirs_matched = get_subdirs(run_dir, steps)

    ## create output dir
    (run_dir / "analysis").mkdir(exist_ok=True)
    out = run_dir / "analysis" / "concat.xtc"
    out = Path(out).expanduser()

    ## gather trajectories
    trajectories = []
    tprs = []
    for d in subdirs_matched:
        trajectories.extend(d.glob("*.xtc"))
        tprs.extend(d.glob("*.tpr"))

    # trajectories = list(filter(lambda p: "rotref" not in p.stem, trajectories))
    trajectories = [str(t) for t in trajectories]
    assert (
        len(trajectories) > 0
    ), f"No trrs found to concatenate in {run_dir} with subdirectory names {steps}"

    ## write concatenated trajectory
    run_shell_cmd(
        f"gmx trjcat -f {' '.join(trajectories)} -o {str(out.with_name('tmp.xtc'))} -cat",
        cwd=run_dir,
    )
    run_shell_cmd(
        f"echo '1 0' | gmx trjconv -f {str(out.with_name('tmp.xtc'))} -s {tprs[0]} -o {str(out)} -center -pbc mol",
        cwd=run_dir,
    )


def plot_energy(args: argparse.Namespace):
    run_dir = Path(args.dir).expanduser().resolve()
    steps: Union[list, str] = args.steps
    terms_list = args.terms
    xvg_entries = ["time"] + terms_list
    terms: str = "\n".join(args.terms)

    subdirs_matched = get_subdirs(run_dir, steps)

    ## create output dir
    (run_dir / "analysis").mkdir(exist_ok=True)
    xvgs_dir = run_dir / "analysis" / "energy_xvgs"
    xvgs_dir.mkdir(exist_ok=True)

    ## gather energy files
    edrs = []
    for d in subdirs_matched:
        edrs.extend(d.glob("*.edr"))
    assert (
        len(edrs) > 0
    ), f"No GROMACS energy files in {run_dir} with subdirectory names {steps}"

    energy = []
    ## write energy .xvg files
    for edr in edrs:
        print(edr.parents[0].name + ".xvg")
        xvg = str(xvgs_dir / edr.parents[0].with_suffix(".xvg").name)
        run_shell_cmd(
            f"echo '{terms} \n\n' | gmx energy -f {str(edr)} -o {xvg}",
            cwd=run_dir,
        )

        ## read energy .xvg files
        with open(xvg, "r") as f:
            energy_raw = f.readlines()
        for line in energy_raw:
            if line[0] not in ["@", "#"]:
                energy.append({k: float(v) for k, v in zip(xvg_entries, line.split())})

    ## plot energy
    snapshot = range(len(energy))
    sim_start = [i for i in snapshot if isclose(energy[i]["time"], 0)]
    sim_names = [str(edr.parents[0].name).split("_")[1] for edr in edrs]
    # diffs =[j-i for i, j in zip(sim_start[:-1],sim_start[1:])]
    limy = [energy[0][terms_list[0]], energy[0][terms_list[0]]]
    print(sim_start)

    for term in terms_list:
        val = [x[term] for x in energy]
        print(term, min(val), max(val))
        limy[0] = min(val) if min(val) < limy[0] else limy[0]
        limy[1] = max(val) if max(val) > limy[1] else limy[1]
        plt.plot(snapshot, val, label=term)

    for i, pos in enumerate(sim_start):
        plt.plot([pos, pos], limy, c="k", linewidth=1)
        plt.text(pos + 1, limy[1] - 0.05 * (limy[1] - limy[0]), sim_names[i])

    plt.xlabel("Snapshot #")
    plt.ylabel("Energy [kJ mol-1]")
    plt.legend()
    plt.savefig(str(run_dir / "analysis" / "energy.png"), dpi=300)

    print(limy)
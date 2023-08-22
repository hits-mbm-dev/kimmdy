"""
The changemanager module holds functions that modify the molecular system
according to reaction recipes.
This includes the topolgy and coordinates as well miscellaneous files
used by e.g. the gromacs extension PLUMED as input. 
"""


from __future__ import annotations
from typing import Union
import logging
from typing import Optional
import MDAnalysis as mda
import numpy as np
from pathlib import Path

from kimmdy.reaction import Bind, Break, Move, RecipeStep
from kimmdy.parsing import read_top, write_top, write_plumed, read_plumed
from kimmdy.tasks import TaskFiles
from kimmdy.topology.topology import Topology
from kimmdy.parameterize import Parameterizer
from kimmdy.coordinates import merge_top_parameter_growth



logger = logging.getLogger(__name__)


def modify_coords(
    recipe_steps: list[RecipeStep],
    files: TaskFiles,
    topA: Topology,
    topB: Topology,
) -> tuple[bool, Union[Path, None]]:
    """Modify the coordinates of the system according to the recipe steps.

    One option is to change the coordinates using Move.new_coords and save 
    those as .gro and .trr

    The other option is to merge the pre-reaction topology topA and the 
    post-reaction topology topB for a free-energy like MD simulation. The 
    actual change of coordinates then takes place this MD simulation.

    TODO: the previous sentence is a lie right now, the MD step happens
    in a different function as its own task, so
    this function does not actually modify any coordinates,
    contrary to its name.
    We should either change the name or make it do what it says it does.

    Parameters
    ----------
    recipe_steps :
        A list of [](`~kimmdy.reaction.RecipeStep`) where each steps contains a `new_coords`
        parameter.
    files :
        Input and output files for this [](`~kimmdy.tasks.Task`).
        files.input:
            - trr
            - tpr
        files.outputdir
        files.output:
            - trr
            - gro
    topA :
        Previous Topology
    topB :
        Parameter-adjusted Topology
    """

    logger.debug(f"Entering modify_coords with recipe_steps {recipe_steps}")

    trr = files.input["trr"]
    tpr = files.input["tpr"]

    u = mda.Universe(str(tpr), str(trr), topology_format="tpr", format="trr")

    ttime = None
    to_move = []
    run_parameter_growth = False

    top_merge_path = None
    # if recipe_steps:
    for step in recipe_steps:
        if isinstance(step, Move):
            if step.new_coords:
                if ttime is None:
                    ttime = step.new_coords[1]

                elif abs(ttime - step.new_coords[1]) > 1e-5:  # 0.01 fs
                    m = (
                        f"Multiple coordinate changes requested at different times!"
                        "\nRecipeSteps:{recipe_steps}"
                    )
                    logger.error(m)
                    raise ValueError(m)
                to_move.append(step.ix_to_move)
            else:
                run_parameter_growth = True
                ttime = u.trajectory[-1].time

        elif isinstance(step, Break) or isinstance(step, Bind):
            run_parameter_growth = True
            ttime = u.trajectory[-1].time

        np.unique(to_move, return_counts=True)

        for ts in u.trajectory[::-1]:
            if abs(ts.time - ttime) > 1e-5:  # 0.01 fs
                continue
            for step in recipe_steps:
                if isinstance(step, Move) and step.new_coords is not None:
                    atm_move = u.select_atoms(f"index {step.ix_to_move}")
                    atm_move[0].position = step.new_coords[0]

            break
        else:
            raise LookupError(
                f"Did not find time {ttime} in trajectory "
                f"with length {u.trajectory[-1].time}"
            )

        if run_parameter_growth:
            top_merge = merge_top_parameter_growth(topA, topB)
            top_merge_path = files.outputdir / "top_merge.top"
            write_top(top_merge.to_dict(), top_merge_path)

        else:
            trr_out = files.outputdir / "coord_mod.trr"
            gro_out = files.outputdir / "coord_mod.gro"

            u.atoms.write(trr_out)
            u.atoms.write(gro_out)

            files.output["trr"] = trr_out
            files.output["gro"] = gro_out

            logger.debug(
                f"Exit modify_coords, final coordinates written to {trr_out.parts[-2:]}"
            )

    return run_parameter_growth, top_merge_path


def modify_top(
    recipe_steps: list[RecipeStep],
    files: TaskFiles,
    topology: Optional[Topology],
    parameterizer: Parameterizer,
) -> None:
    """Modify the topology of the system according to the recipe steps.

    Modifies the topology in place and also writes the new topology
    to a file to be used by external programs (gromacs).

    Parameters
    ----------
    recipe_steps :
        A list of [](`~kimmdy.reaction.RecipeStep`)s.
        parameter.
    files :
        Input and output files for this [](`~kimmdy.tasks.Task`).
        files.input:
            - top
        files.output:
            - top
    topology:
        TODO: make this required instead of optional
    """
    files.output = {"top": files.outputdir / "topol_mod.top"}
    oldtop = files.input["top"]
    newtop = files.output["top"]

    logger.info(f"Reading: {oldtop} and writing modified topology to {newtop}.")
    if topology is None:
        topologyDict = read_top(oldtop)
        topology = Topology(topologyDict)

    focus = set()
    # if recipe_steps:
    for step in recipe_steps:
        if isinstance(step, Break):
            topology.break_bond((step.atom_id_1, step.atom_id_2))
            focus.add(step.atom_ix_1)
            focus.add(step.atom_ix_2)
        elif isinstance(step, Bind):
            topology.bind_bond((step.atom_id_1, step.atom_id_2))
            focus.add(step.atom_id_1)
            focus.add(step.atom_id_2)
        elif isinstance(step, Move):
            top_done = False
            if step.id_to_bind is not None and step.id_to_break is None:
                # implicit H-bond breaking
                topology.move_hydrogen((step.id_to_move, step.id_to_bind))
                focus.add(step.id_to_move)
                focus.add(step.id_to_bind)
                top_done = True
            if step.id_to_break is not None and not top_done:
                topology.break_bond((step.id_to_move, step.id_to_break))
                focus.add(step.id_to_move)
                focus.add(step.id_to_break)
            if step.id_to_bind is not None and not top_done:
                topology.bind_bond((step.id_to_move, step.id_to_bind))
                focus.add(step.id_to_move)
                focus.add(step.id_to_bind)

        else:
            raise NotImplementedError(f"RecipeStep {step} not implemented!")
    parameterizer.parameterize_topology(topology)
    write_top(topology.to_dict(), newtop)

    return


def modify_plumed(
    recipe_steps: list[RecipeStep],
    oldplumeddat: Path,
    newplumeddat: Path,
    plumeddist: Path,
):
    """Modify plumed input files.

    TODO: finish this function.
    TODO: this function in here take TaskFiles, some take their inputs
    directly. Need to unify.

    Parameters
    ----------
    recipe_steps :
        A list of [](`~kimmdy.reaction.RecipeStep`)s.
        parameter.
    """
    logger.info(
        f"Reading: {oldplumeddat} and writing modified plumed input to {newplumeddat}."
    )
    plumeddat = read_plumed(oldplumeddat)

    for step in recipe_steps:
        if isinstance(step, Break):
            plumeddat = break_bond_plumed(
                plumeddat, (step.atom_id_1, step.atom_id_2), plumeddist
            )
        else:
            # TODO: handle BIND / MOVE
            # for now, we wouldn't bind or move bonds that are relevant for plumed
            logger.debug(f"Plumed changes for {step} not implemented!")

    write_plumed(plumeddat, newplumeddat)


def break_bond_plumed(plumeddat, breakpair: tuple[str, str], plumeddist):
    new_distances = []
    broken_distances = []
    for line in plumeddat["distances"]:
        if all(x in line["atoms"] for x in breakpair):
            broken_distances.append(line["id"])
        else:
            new_distances.append(line)

    plumeddat["distances"] = new_distances

    for line in plumeddat["prints"]:
        line["ARG"] = [id for id in line["ARG"] if not id in broken_distances]
        line["FILE"] = plumeddist

    return plumeddat

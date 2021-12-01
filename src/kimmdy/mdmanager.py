from __future__ import annotations
import logging
import queue
from pathlib import Path
from enum import Enum, auto
from dataclasses import dataclass
from typing import Callable
from kimmdy.utils import run_shell_cmd
from kimmdy.config import Config
from kimmdy.reaction import ConversionType, ConversionRecipe

import subprocess as sp


class MDManager:
    # def __init__(self):
        # self.topfile = top
        # self.struct = struct
        
        # self.indexfile = idx
        # self.mdpfile = mdppath
        # self.it = str(iteration)

        # self.outgro = basename + self.it + ".gro"
        # self.outtpr = basename + self.it + ".tpr"
        # self.outtrr = basename + self.it + ".trr"

    def dummy_step(self, out_dir, top, gro):
        run_shell_cmd(
            "pwd>./pwd.pwd", out_dir
        )
    
    def write_mdp(self):
        pass

    def minimzation(self, out_dir, top, gro, mdp):
        tpr = out_dir / "minimization.tpr"
        outgro = out_dir / "minimized.gro"
        run_shell_cmd(
            f"gmx grompp -p {top} -c {gro} -r {gro} -f {mdp} -o {tpr}"
        )
        run_shell_cmd(f"gmx mdrun -s {tpr} -c {outgro}")
    
    def _minimzation(self):

        run_shell_cmd(
            f"gmx grompp -p {self.topfile} -c {self.grofile} -r {self.grofile} -f {self.mdpfile} -o {self.tprfile}"
        )
        run_shell_cmd(f"gmx mdrun -s {self.tprfile} -c {self.outgro}")

    def equilibration(self, out_dir, top, gro, mdp):
        tpr = out_dir / "equil.tpr"
        outgro = out_dir / "equil.gro"
        run_shell_cmd(
            f"gmx grompp -p {top} -c {gro} -r {gro} -f {mdp} -o {tpr}"
        )
        run_shell_cmd(f"gmx mdrun -v -s {tpr} -c {outgro}")

    def equilibrium(self, out_dir, top, gro, mdp, idx):
        """equilibrium before pulling md"""
        tpr = out_dir / "equil.tpr"
        outgro = out_dir / "equil.gro"
        outtrr = out_dir / "equil.trr"
        run_shell_cmd(
            f"gmx grompp -p {top} -c {gro} -f {mdp} -n {idx} -o {tpr} -maxwarn 5"
        )
        run_shell_cmd(
            f"gmx mdrun -v -s {tpr} -c {outgro} -o {outtrr}"
        )  # use mpirun mdrun_mpi on cluster / several nodes are used

    def production(self, out_dir, top, gro, mdp, idx, cpt, dat):
        """normal pulling md"""
        tpr = out_dir / "prod.tpr"
        outgro = out_dir / "prod.gro"
        outtrr = out_dir / "prod.trr"
        run_shell_cmd(
            f" gmx grompp -p {top} -c {gro} -f {mdp} -n {idx} -t {cpt} -o {tpr} -maxwarn 5"
        )
        run_shell_cmd(
            f"gmx mdrun -v -s {tpr} -c {outgro} -plumed {dat} -o {outtrr}"
        )  # use mpirun mdrun_mpi on cluster
    
    def _production(self, cpt, plumedfile):
        """normal pulling md"""
        run_shell_cmd(
            f" gmx grompp -p {self.topfile} -c {self.struct} -f {self.mdpfile} -n {self.indexfile} -t {cpt} -o {self.outtpr} -maxwarn 5"
        )
        run_shell_cmd(
            f"gmx mdrun -v -s {self.outtpr} -c {self.outgro} -plumed {plumedfile} -o {self.outtrr}"
        )  # use mpirun mdrun_mpi on cluster
        return self.outgro

    def relaxation(self, out_dir, top, gro, mdp, idx, cpt):
        """equil after break -->> called from changer"""
        tpr = out_dir / "relax.tpr"
        outgro = out_dir / "relax.gro"
        outtrr = out_dir / "relax.trr"
        run_shell_cmd(
            f" gmx grompp -p {top} -c {gro} -f {mdp} -n {idx} -t {cpt} -o {tpr} -maxwarn 5"
        )
        run_shell_cmd(
            f"gmx mdrun -v -s {tpr} -c {outgro} -o {outtrr}"
        )  # use mpirun mdrun_mpi on cluster / several nodes are used

    def _relaxation(self, cpt):
        """equil after break -->> called from changer"""
        run_shell_cmd(
            f" gmx grompp -p {self.topfile} -c {self.struct} -f {self.mdpfile} -n {self.indexfile} -t {cpt} -o {self.outtpr} -maxwarn 5"
        )
        run_shell_cmd(
            f"gmx mdrun -v -s {self.outtpr} -c {self.outgro} -o {self.outtrr}"
        )  # use mpirun mdrun_mpi on cluster / several nodes are used
        return self.outgro

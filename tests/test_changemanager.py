import pytest
import shutil
from pathlib import Path

from kimmdy.reaction import Break, Bind, Move, RecipeStep
from kimmdy.parsing import read_plumed, read_top
from kimmdy.changemanager import (
    break_bond_plumed,
    modify_plumed,
    modify_coords,
    modify_top,
)
from kimmdy.topology.topology import Topology
from kimmdy.parameterize import BasicParameterizer
from conftest import SlimFiles
import os


@pytest.fixture
def tmpdir(tmp_path) -> Path:
    dirname = "test_changemanager"
    try:
        filedir = Path(__file__).parent / "test_files" / dirname
        assetsdir = Path(__file__).parent / "test_files" / "assets"
    except NameError:
        filedir = Path("./tests/test_files") / dirname
        assetsdir = Path("./tests/test_files") / "assets"
    test_dir = tmp_path / dirname
    shutil.copytree(filedir, test_dir)
    Path(test_dir / "amber99sb-star-ildnp.ff").symlink_to(
        assetsdir / "amber99sb-star-ildnp.ff",
        target_is_directory=True,
    )
    return test_dir


def test_plumed_break(tmpdir):
    plumed = read_plumed(tmpdir / "plumed_nat.dat")
    plumed_break_ref = read_plumed(tmpdir / "plumed_break29-35.dat")

    breakpair = ("29", "35")
    plumed_break = break_bond_plumed(plumed, breakpair, Path("distances.dat"))

    assert plumed_break["distances"] == plumed_break_ref["distances"]
    assert plumed_break["prints"] == plumed_break_ref["prints"]


def test_plumed_modify(tmpdir):
    plumeddat: Path = tmpdir / "plumed_nat.dat"
    newplumeddat: Path = tmpdir / "plumed_test.dat"
    recipe_steps = [Break(28, 34)]
    plumeddist: Path = Path("distances.dat")

    modify_plumed(recipe_steps, plumeddat, newplumeddat, plumeddist)

    plumed_break_ref = read_plumed(tmpdir / "plumed_break29-35.dat")
    plumed_break_test = read_plumed(newplumeddat)

    assert plumed_break_test["distances"] == plumed_break_ref["distances"]
    assert plumed_break_test["prints"] == plumed_break_ref["prints"]


def test_modify_coords_break(tmpdir):
    steps: list[RecipeStep] = [Break(28, 34)]
    files = SlimFiles(outputdir=tmpdir)
    files.input["trr"] = tmpdir / "pull.trr"
    files.input["tpr"] = tmpdir / "pull.tpr"
    files.input["top"] = tmpdir / "hexala_out.top"
    files.output["top"] = tmpdir / "topol_mod.top"
    topA_dict = read_top(files.input["top"])
    topB_dict = read_top(files.output["top"])
    topA = Topology(topA_dict)
    topB = Topology(topB_dict)
    run_parameter_growth, top_merge_path = modify_coords(steps, files, topA, topB)
    assert run_parameter_growth
    assert top_merge_path
    assert (files.outputdir / "top_merge.top").exists()


def test_modify_coords_move(tmpdir):
    steps = [Move(ix_to_move=0, new_coords=[[0.0, 0.0, 0.0], 100.0])]
    files = SlimFiles(outputdir=tmpdir)
    files.input["trr"] = tmpdir / "pull.trr"
    files.input["tpr"] = tmpdir / "pull.tpr"
    run_parameter_growth, top_merge_path = modify_coords(steps, files, None, None)
    assert not run_parameter_growth
    assert files.output["trr"].exists()
    assert files.output["gro"].exists()
    # could check whether the coordinates were actually changed, probably using mda
    # could even randomize idx and coords


def test_modify_top(tmpdir, generic_topology):
    steps = [Move(ix_to_move=2, ix_to_bind=1, new_coords=((0.0, 0.0, 0.0), 100.0))]
    parameterizer = BasicParameterizer()
    files = SlimFiles(outputdir=tmpdir)
    files.input["top"] = tmpdir / ""
    files.output["top"] = tmpdir / "out.top"

    modify_top(steps, files, generic_topology, parameterizer)

    assert files.output["top"].exists()

from pathlib import Path

import pytest
import yaml

from kimmdy.config import Config
from kimmdy.parsing import read_top
from kimmdy.runmanager import get_existing_files
from kimmdy.topology.ff import FF
from kimmdy.topology.topology import Topology


@pytest.mark.require_gmx
def test_parse_config1_casting(arranged_tmp_path):
    config = Config(Path("config1.yml"))

    assert config.dryrun is True
    assert isinstance(config.dryrun, bool)
    assert config.max_tasks == 10

    assert isinstance(config.cwd, Path)
    assert isinstance(config.out, Path)

    assert isinstance(config.mds, Config)
    assert isinstance(config.mds.equilibrium1, Config)
    assert isinstance(config.plumed, Path)
    assert isinstance(config.mds.pull1.use_plumed, bool)


def test_non_existent_sections_with_defaults_in_subsections_are_created(
    arranged_tmp_path,
):
    config = Config(Path("config1.yml"))

    assert config.log.file.name == "kimmdy.log"
    assert config.log.level == "INFO"


def test_no_sections_are_created_for_not_mentioned_reactions(arranged_tmp_path):
    config_1 = Config(Path("config1.yml"))
    config_2 = Config(Path("config2.yml"))

    assert config_1.reactions.homolysis
    assert config_1.reactions.homolysis.edis.name == "edissoc.dat"
    assert len(config_1.reactions.__dict__) == 1

    assert len(config_2.reactions.__dict__) == 2
    assert config_2.reactions.homolysis
    assert config_2.reactions.homolysis.edis.name == "edissoc.dat"
    assert config_2.reactions.hat_naive
    assert config_2.reactions.hat_naive.polling_rate == 1
    assert config_2.reactions.hat_naive.h_cutoff == 4


def test_subsections_with_defaults_are_kept(arranged_tmp_path):
    config = Config(Path("config2.yml"))

    assert config.log.file.name == "kimmdy.log"
    assert config.log.level == "DEBUG"


def test_out_is_generated_from_name_if_not_set(arranged_tmp_path):
    config_1 = Config(Path("config1.yml"))
    config_2 = Config(Path("config2.yml"))

    assert config_1.name == "kimmdy"
    assert config_1.out.name == "test_config_1"

    assert config_2.name == "config2"
    assert config_2.out.name == "config2"


def test_general_settings_for_mds_are_set(arranged_tmp_path):
    config = Config(Path("config1.yml"))

    assert len(config.mds.__dict__) == 3
    # explicitly set:
    assert config.mds.equilibrium1.mdp.name == "pullf1500_equil.mdp"
    assert config.mds.pull1.use_plumed == True
    # '.*' defaults:
    assert config.mds.equilibrium1.use_plumed == False


def test_complains_if_plumed_used_but_not_set(arranged_tmp_path):
    with open(Path("config1.yml"), "r") as f:
        raw = yaml.safe_load(f)
    del raw["plumed"]

    with pytest.raises(
        AssertionError,
        match="Plumed requested in md section, but not defined at config root",
    ):
        _ = Config(recursive_dict=raw)


def test_parse_reaction_only(arranged_tmp_path):
    with open(Path("config1.yml"), "r") as f:
        raw = yaml.safe_load(f)
    raw["tpr"] = "pull.tpr"
    raw["trr"] = "pull.trr"
    del raw["mds"]
    del raw["changer"]["coordinates"]["md"]
    raw["sequence"] = ["homolysis"]

    config = Config(recursive_dict=raw)

    assert isinstance(config.tpr, Path)
    assert isinstance(config.trr, Path)
    assert isinstance(config.plumed, Path)
    assert config.sequence == ["homolysis"]


def test_parse_missing_mdp_file(arranged_tmp_path):
    with open(Path("config1.yml"), "r") as f:
        raw = yaml.safe_load(f)
    raw["mds"]["relax"]["mdp"] = "nonexisting.mdp"
    with pytest.raises(LookupError, match="File not found:"):
        Config(recursive_dict=raw)


def test_parse_missing_required_mdp_section(arranged_tmp_path):
    with open(Path("config1.yml"), "r") as f:
        raw = yaml.safe_load(f)
    del raw["mds"]["relax"]["mdp"]
    with pytest.raises(
        AssertionError, match="MD instance defined but contains no mdp file."
    ):
        Config(recursive_dict=raw)


def test_parse_sequence_missing_entry(arranged_tmp_path):
    with open(Path("config1.yml"), "r") as f:
        raw = yaml.safe_load(f)
    raw["sequence"][1]["tasks"][0] = "nonexistent_entry"
    with pytest.raises(
        AssertionError,
        match="Task nonexistent_entry listed in sequence, but not defined!",
    ):
        Config(recursive_dict=raw)


def test_parse_sequence_missing(arranged_tmp_path):
    with open(Path("config1.yml"), "r") as f:
        raw = yaml.safe_load(f)
    del raw["sequence"]
    with pytest.raises(AssertionError, match="No sequence defined!"):
        Config(recursive_dict=raw)


def test_parse_coordinates_bad_reference(arranged_tmp_path):
    with open(Path("config1.yml"), "r") as f:
        raw = yaml.safe_load(f)
    raw["changer"]["coordinates"]["md"] = "relax_nonexistent.mdp"
    with pytest.raises(
        AssertionError, match="Relax MD relax_nonexistent.mdp not in MD section!"
    ):
        Config(recursive_dict=raw)


def test_get_existing_files(arranged_tmp_path):
    config = Config(Path("config1.yml"))
    file_d = get_existing_files(config)
    assert set(file_d.keys()) == set(
        [
            "top",
            "gro",
            "ndx",
            "plumed",
            "plumed_out",
            "pullf1500_equil.mdp",
            "pullf1500.mdp",
            "broken_equil_f1000.mdp",
            "edissoc.dat",
            "ffbonded.itp",
            "yml",
        ]
    )


def test_explicit_residuetypes(arranged_tmp_path):
    config = Config(Path("config3.yml"))
    ff = FF(top=read_top(config.top), residuetypes_path=config.residuetypes)

    assert len(ff.residuetypes.keys()) == 5
    assert ff.residuetypes["ALA"]


def test_explicit_radicals(arranged_tmp_path):
    config = Config(Path("config1.yml"))
    config.radicals = "1 2 3 4"
    top = Topology(read_top(config.top), radicals=config.radicals)

    assert len(top.radicals) == 4
    for radical in config.radicals.split(sep=" "):
        assert top.radicals.get(radical)


def test_config_retains_additional_kwargs(
    arranged_tmp_path,
):
    config = Config(Path("config4.yml"))
    assert config.changer.topology.parameterization_kwargs.test_arg == "hello"

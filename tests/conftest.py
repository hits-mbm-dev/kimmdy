"""File for pytest configuration in python and fixture definition.

The name 'conftest.py' is recognized by pytest to execute it before tests.
"""

import sys
import pytest
import shutil
import os
from pathlib import Path
from dataclasses import dataclass, field

from kimmdy.runmanager import RunManager
from kimmdy.topology.topology import Topology
from kimmdy.config import Config
from kimmdy.tasks import TaskFiles
from kimmdy.parsing import read_top, read_json
from kimmdy.utils import get_gmx_dir
from typing import Callable


# create pytest mark decorators
def pytest_configure(config):
    # register an additional marker
    config.addinivalue_line(
        "markers", "require_gmx: mark test to run if gmx is executable"
    )


def pytest_runtest_setup(item):
    require_gmx = [mark for mark in item.iter_markers(name="require_gmx")]
    if require_gmx:
        if not get_gmx_dir():
            pytest.skip("Command 'gmx' not found, can't test gmx dir parsing.")


# slim classes
@dataclass
class SlimFiles(TaskFiles):
    input: dict[str, Path] = field(default_factory=dict)
    output: dict[str, Path] = field(default_factory=dict)
    outputdir: Path = Path()
    get_latest: Callable = lambda x: x


# fixtures for setup and teardown


# general object fixtures
@pytest.fixture
def generic_rmgr(tmp_path):
    shutil.copytree(
        Path(__file__).parent / "test_files/test_homolysis",
        tmp_path,
        dirs_exist_ok=True,
    )
    os.chdir(tmp_path)
    Path(tmp_path / "amber99sb-star-ildnp.ff").symlink_to(
        Path(__file__).parent / "test_files/assets/amber99sb-star-ildnp.ff",
        target_is_directory=True,
    )
    return RunManager(Config(tmp_path / "kimmdy.yml"))


@pytest.fixture(scope="function")
def generic_topology():
    filedir = Path(__file__).parent / "test_files/test_integration/hat_naive"
    top_path = filedir / "Ala_out.top"
    top_dict = read_top(top_path)
    top = Topology(top_dict)
    print(top.ff)
    return top


@pytest.fixture
def generic_parameter_input():
    return read_json(
        Path(__file__).parent / "test_files/assets/GrAPPa_input_alanine.json"
    )


@pytest.fixture
def generic_parameter_output():
    return read_json(
        Path(__file__).parent / "test_files/assets/GrAPPa_output_alanine.json"
    )
